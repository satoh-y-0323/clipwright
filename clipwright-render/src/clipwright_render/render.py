"""render.py — clipwright-render のオーケストレーション層。

ffprobe による probe と ffmpeg による再エンコードを統合し、
入力検証 → OTIO 解析 → probe → 計画構築 → 実行 の一連フローを担う。

設計判断:
- _probe() は core inspect_media を呼び出し MediaInfo → ProbeInfo に変換する（AD-3）。
  独自 ffprobe 呼び出しを廃止し重複を解消する（DC-AS-001/ADR-6 暫定対応を解消）。
- ffmpeg timeout = max(300, ceil(出力総尺秒 × 10)) 秒（ADR-4/DC-AM-006）。
  再エンコードの最悪ケース (~10x 実時間) を見込んだ安全マージン。
- PROBE_FAILED 等のエラーは inspect_media が送出するものをそのまま伝播する。
- ffmpeg stderr 生文字列・内部パスは summary/data/error に露出しない。
  core の process.run が先頭 200 文字要約のみ message に含めることで実現する。
- 全ユニークソースの境界検証・存在確認・probe を行う（ADR-C8）。
- ffmpeg コマンドの -i 並びは RenderPlan.input_sources をそのまま使う（ADR-C9-r2）。
- _probe は fps = 「第1 video StreamInfo あり AND duration not None」のときのみ設定し
  音声のみソースの rate=1000.0 センチネルを fps に誤採用しない（ADR-C2-r2）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import get_clipwright_metadata, load_timeline
from clipwright.process import resolve_tool, run

from clipwright_render.plan import (
    BgmClip,
    ProbeInfo,
    build_plan,
    resolve_bgm,
    resolve_kept_ranges,
    unique_sources_in_order,
)
from clipwright_render.schemas import RenderOptions

# 出力拡張子ホワイトリスト（DC-AM-003）
_ALLOWED_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".webm"})


def _probe(source: str) -> ProbeInfo:
    """inspect_media を呼び出して ProbeInfo を返す（AD-3 / ADR-C2-r2）。

    core の inspect_media に ffprobe 実行を委譲し、返された MediaInfo を
    plan.py が必要とする ProbeInfo 形式に変換する純粋なアダプタ。
    FILE_NOT_FOUND 時は message を basename のみに差し替えて再送出し、
    OTIO target_url の絶対パスを露出しない（Sec M-1）。
    PROBE_FAILED 等、FILE_NOT_FOUND 以外のエラーはそのまま伝播する。

    fps は「第1 video StreamInfo あり AND MediaInfo.duration not None」の
    ときのみ MediaInfo.duration.rate を採用する。
    音声のみソースは duration.rate=1000.0 センチネルを fps に誤採用しないため、
    video stream なしのときは fps=None を返す（ADR-C2-r2）。

    Args:
        source: probe 対象のメディアファイルパス。

    Returns:
        ProbeInfo(has_video, audio_count, bit_rate, width, height, fps)。

    Raises:
        ClipwrightError: PROBE_FAILED / DEPENDENCY_MISSING / SUBPROCESS_FAILED /
            SUBPROCESS_TIMEOUT / FILE_NOT_FOUND（inspect_media が送出）。
    """
    try:
        info = inspect_media(source)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=(
                    f"ソースメディアファイルが見つかりません: {Path(source).name}"
                ),
                hint=exc.hint,
            ) from exc
        raise
    has_video = any(s.codec_type == "video" for s in info.streams)
    audio_count = sum(1 for s in info.streams if s.codec_type == "audio")

    # 解像度・fps は第1 video StreamInfo から取得（ADR-C2-r2）
    width: int | None = None
    height: int | None = None
    fps: float | None = None

    if has_video:
        # 第1 video StreamInfo を取得
        first_video = next((s for s in info.streams if s.codec_type == "video"), None)
        if first_video is not None:
            width = first_video.width
            height = first_video.height

        # fps: video stream あり AND duration not None のときのみ採用（ADR-C2-r2）
        # 音声のみソースは rate=1000.0 センチネルのため、video stream なしは fps=None。
        if info.duration is not None:
            fps = float(info.duration.rate)

    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=info.bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _check_source_within_timeline_dir(timeline_path: Path, source: str) -> None:
    """source パスが timeline 親ディレクトリ配下にあることを検証する（Sec M-2）。

    OTIO target_url に任意パスが埋め込まれた悪意ある OTIO への対策。
    単一 source は OTIO と同一ディレクトリ配下に配置することを前提とする。

    Args:
        timeline_path: OTIO タイムラインファイルのパス。
        source: OTIO target_url から取得したメディアソースパス。

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED（source がプロジェクト境界外を指す場合）。
    """
    try:
        allowed_base = timeline_path.parent.resolve()
        source_resolved = Path(source).resolve()
        # パス区切り文字を含めて比較し、ディレクトリ名の前方一致誤検知を防ぐ
        source_str = str(source_resolved)
        base_str = str(allowed_base)
        if not (
            source_str == base_str
            or source_str.startswith(base_str + "/")
            or source_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="source ファイルがプロジェクト境界外を指しています。",
                hint=(
                    "OTIO タイムラインと同じディレクトリ配下の"
                    "ソースファイルを使用してください。"
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve() 失敗（ネットワークパス・超長パス・シンボリックリンクループ等）は
        # absolute() ベースの best-effort 比較にフォールバックする（SR L-1）。
        # これにより境界検証が完全にスキップされるリスクを低減し、
        # resolve() 失敗のような極端なケースでも境界外 probe を防ぐ。
        # absolute() も失敗した場合のみスキップし、後続の存在確認に委ねる
        # （_check_path_not_allowed の既存フォールバック作法に倣う）。
        try:
            allowed_base_abs = str(timeline_path.parent.absolute())
            source_abs = str(Path(source).absolute())
            if not (
                source_abs == allowed_base_abs
                or source_abs.startswith(allowed_base_abs + "/")
                or source_abs.startswith(allowed_base_abs + "\\")
            ):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="source ファイルがプロジェクト境界外を指しています。",
                    hint=(
                        "OTIO タイムラインと同じディレクトリ配下の"
                        "ソースファイルを使用してください。"
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # absolute() も失敗した場合のみスキップ（本当に解決不能なパスのみ）
            pass


def _check_path_not_allowed(output_path: Path, source: str) -> None:
    """output と source が同一パスを指していないか確認する（DC-AM-002）。

    resolve() を用いてシンボリックリンク等を考慮した比較を行う。
    resolve() が失敗する場合（パスが存在しない等）は absolute() 比較に、
    それも失敗した場合のみ文字列比較にフォールバックする（Sec L-1）。
    """
    try:
        if output_path.resolve() == Path(source).resolve():
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="出力パスと入力ソースパスが同一です。",
                hint=(
                    "出力ファイルパスを入力ソースファイルとは別のパスに変更してください。"
                ),
            )
    except OSError as exc:
        # resolve() 失敗時（ネットワークパス・極端に長いパス等）は
        # absolute() 比較を試み、それも失敗した場合のみ文字列比較にフォールバック。
        try:
            if Path(output_path).absolute() == Path(source).absolute():
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="出力パスと入力ソースパスが同一です。",
                    hint=(
                        "出力ファイルパスを入力ソースファイルとは別のパスに変更してください。"
                    ),
                ) from exc
        except OSError as exc2:
            if str(output_path) == source:
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message="出力パスと入力ソースパスが同一です。",
                    hint=(
                        "出力ファイルパスを入力ソースファイルとは別のパスに変更してください。"
                    ),
                ) from exc2


def render_timeline(
    timeline: str,
    output: str,
    options: RenderOptions,
    dry_run: bool = False,
) -> dict[str, Any]:
    """OTIO タイムラインを FFmpeg で実体化する（§3 データフロー）。

    非破壊: 入力 timeline ファイル・元素材メディアは一切書き換えない。
    出力は新規生成した動画ファイルのパスを artifacts に返す。

    フロー:
      1. 入力検証（timeline/output の存在・拡張子・上書き・パス衝突）
      2. load_timeline → resolve_kept_ranges → 全ユニークソースの検証・probe
      3. build_plan(ranges, probe_info, options, source_probes=source_probes)
      4a. dry_run=True  → 計画要約を ok_result（ffmpeg は呼ばない）
      4b. dry_run=False → ffmpeg を1回 run → output 存在確認 → ok_result

    Args:
        timeline: 入力 OTIO タイムラインファイルパス。
        output: 出力動画ファイルパス。
        options: RenderOptions（コーデック/解像度/fps/crf/overwrite）。
        dry_run: True のとき ffmpeg を呼ばず計画のみを返す。

    Returns:
        ok_result または error_result のエンベロープ dict。

    Raises:
        なし（すべての ClipwrightError を error_result に変換して返す）。
    """
    try:
        return _render_inner(timeline, output, options, dry_run)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _render_inner(
    timeline: str,
    output: str,
    options: RenderOptions,
    dry_run: bool,
) -> dict[str, Any]:
    """render の内部実装。ClipwrightError をそのまま送出する。

    BGM オーケストレーション拡張（§7 ADR-B4-r2/B5-r2/B6-r2/B8）:
    - resolve_bgm(tl) で A2 Audio トラックの BGM クリップを検出する。
    - BGM クリップがある場合、BGM ソースにも全ソース境界検証を適用する（ADR-B8）。
    - build_plan に bgm=BgmClip を渡す（None でも従来同一・後方互換・ADR-B7）。
    - plan.bgm_source がある場合、-i 並びに -stream_loop -1 を前置して
      BGM を末尾に追加する（ADR-B6-r2/DC-AS-005）。
    """
    timeline_path = Path(timeline)
    output_path = Path(output)

    # --- 1. 入力検証（timeline 存在確認）---

    # timeline ファイル存在確認
    if not timeline_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"タイムラインファイルが見つかりません: {timeline_path.name}",
            hint="有効な .otio ファイルパスを指定してください。",
        )

    # --- 2. OTIO 解析（先行）---
    # BGM ソースと output の比較を出力拡張子チェックより先に行うため、
    # OTIO 解析を前倒しにする（ADR-B8・output==BGM で PATH_NOT_ALLOWED 優先）。
    tl = load_timeline(timeline)
    ranges = resolve_kept_ranges(tl)

    # 全ユニークソースを出現順で取得する（ADR-C9-r2）
    # unique_sources_in_order は plan.py の単一情報源（ADR-C9-r2）
    unique_sources = unique_sources_in_order(ranges)

    # --- 2b. BGM クリップ検出（ADR-B4-r2）---
    # A2 Audio トラックの kind=="bgm" クリップを検出する。
    # 複数 BGM クリップは UNSUPPORTED_OPERATION（resolve_bgm が送出）。
    bgm_clip: BgmClip | None = resolve_bgm(tl)

    # BGM ソースと output の早期パス衝突確認（ADR-B8・PATH_NOT_ALLOWED 優先）
    # output == BGM ソース は拡張子チェックより前に検出する（非破壊保証）。
    if isinstance(bgm_clip, BgmClip):
        _check_path_not_allowed(output_path, bgm_clip.source)

    # --- 3. 出力の入力検証 ---

    # 出力拡張子ホワイトリスト確認（DC-AM-003）
    output_ext = output_path.suffix.lower()
    if output_ext not in _ALLOWED_EXTENSIONS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"出力ファイルの拡張子が不正です: {output_ext!r}。"
                f"許可されている拡張子: {sorted(_ALLOWED_EXTENSIONS)}"
            ),
            hint=(
                "出力ファイルパスの拡張子を .mp4 / .mkv / .mov / .webm"
                " のいずれかにしてください。"
            ),
        )

    # 出力親ディレクトリ存在確認（自動作成しない・DC-GP-005）
    # フルパスを error.message に含めない（Sec M-1）
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=(
                "出力先ディレクトリが存在しません。"
                "指定 output の親ディレクトリを確認してください。"
            ),
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

    # --- 4. 全ユニークソースに境界検証・存在確認・パス衝突確認を適用する（ADR-C8）---
    for src in unique_sources:
        # source がタイムラインと同一ディレクトリ配下にあることを検証する（Sec M-2）
        _check_source_within_timeline_dir(timeline_path, src)

        # output == source チェック（PATH_NOT_ALLOWED・DC-AM-002）
        _check_path_not_allowed(output_path, src)

        # source ファイル存在確認（DC-GP-005）
        if not Path(src).exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"ソースメディアファイルが見つかりません: {Path(src).name}",
                hint=(
                    "OTIO タイムラインに記録されているソースファイルを配置してください。"  # noqa: E501
                ),
            )

    # --- 4b. BGM ソースの詳細境界検証（ADR-B8）---
    # BGM ソースにも既存の全ソース境界検証を適用する。
    # 早期パス衝突確認（ステップ2b）で output == BGM は既にチェック済み。
    if isinstance(bgm_clip, BgmClip):
        bgm_src = bgm_clip.source
        _check_source_within_timeline_dir(timeline_path, bgm_src)
        # output == bgm_src は早期チェック済みだが、ここでも適用する（二重保護）
        _check_path_not_allowed(output_path, bgm_src)
        if not Path(bgm_src).exists():
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"BGM ソースファイルが見つかりません: {Path(bgm_src).name}",
                hint=(
                    "OTIO タイムラインに記録されている"
                    " BGM ソースファイルを配置してください。"
                ),
            )

    # output 既存 + overwrite=False → INVALID_INPUT（DC-AM-002）
    if output_path.exists() and not options.overwrite:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"出力ファイルが既に存在します: {output_path.name}",
            hint=("既存ファイルを上書きする場合は overwrite=True を指定してください。"),
        )

    # --- 3. 全ユニークソースを probe して source_probes を構築（ADR-C8 / ADR-C2-r2）---
    source_probes: dict[str, ProbeInfo] = {}
    for src in unique_sources:
        source_probes[src] = _probe(src)

    # 先頭ソースの ProbeInfo を probe_info として渡す（単一ソース経路の後方互換）
    first_source = unique_sources[0]
    probe_info = source_probes[first_source]

    # --- 4. denoise / loudness メタデータ読み出し ---
    # timeline-level metadata["clipwright"] から denoise / loudness を読み出す。
    # None のときは後方互換でそれぞれなし（既存テスト非回帰・ADR-L6）。
    # 存在するときは build_plan 内で各 Directive 検証を行い、
    # 不正なら INVALID_INPUT が送出される。
    clipwright_meta = get_clipwright_metadata(tl)
    raw_denoise = clipwright_meta.get("denoise")
    raw_loudness = clipwright_meta.get("loudness")

    # --- 5. build_plan ---
    # source_probes を渡して複数ソース経路を有効にする（ADR-C2-r2 / ADR-C9-r2）
    # bgm_clip を渡して BGM 音声チェーンを組み込む（ADR-B5-r2・bgm=None で後方互換）
    plan = build_plan(
        ranges,
        probe_info,
        options,
        denoise=raw_denoise,
        loudness=raw_loudness,
        source_probes=source_probes,
        bgm=bgm_clip,
    )

    # --- 6a. dry_run ---
    if dry_run:
        size_info = (
            f"、概算サイズ {plan.estimated_size_bytes / 1024 / 1024:.1f} MB"
            if plan.estimated_size_bytes is not None
            else "、概算サイズ算出不可"
        )
        summary = (
            f"[dry_run] {plan.segment_count} 区間・"
            f"総尺 {plan.total_duration_seconds:.2f} 秒{size_info}。"
            f"ffmpeg を実行すると {output_path.name} を生成します。"
        )
        return ok_result(
            summary,
            data={
                "ffmpeg_args": plan.ffmpeg_args,
                "filter_complex": plan.filter_complex,
                "segment_count": plan.segment_count,
                "total_duration_seconds": plan.total_duration_seconds,
                "estimated_size_bytes": plan.estimated_size_bytes,
            },
            warnings=plan.warnings,
        )

    # --- 6b. 実行 ---
    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    timeout = max(300, math.ceil(plan.total_duration_seconds * 10))

    # overwrite フラグ（-y / -n）
    overwrite_flag = ["-y"] if options.overwrite else ["-n"]

    # -i 並びは plan.input_sources をそのまま使う（ADR-C9-r2）
    # render.py で順序を再計算しない（二重実装排除）
    inputs: list[str] = []
    for src in plan.input_sources:
        inputs += ["-i", src]

    # BGM がある場合、-stream_loop -1 を前置して BGM を末尾 -i として追加する
    # （ADR-B6-r2/DC-AS-005）。-stream_loop は入力オプションのため -i の直前に置く。
    # BGM index == len(plan.input_sources) の不変条件を維持する（DC-AS-005）。
    if plan.bgm_source is not None:
        inputs += ["-stream_loop", "-1", "-i", plan.bgm_source]

    cmd = [ffmpeg] + overwrite_flag + inputs + plan.ffmpeg_args + [str(output)]

    run(cmd, timeout=float(timeout))

    # output ファイル存在確認
    if not output_path.exists():
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message="ffmpeg が正常終了しましたが出力ファイルが生成されませんでした。",
            hint="ffmpeg のコマンド引数・出力パスを確認してください。",
        )

    output_size = output_path.stat().st_size
    summary = (
        f"{plan.segment_count} クリップを連結し"
        f"総尺 {plan.total_duration_seconds:.2f} 秒の動画を生成しました"
        f"（{output_size / 1024 / 1024:.1f} MB）。"
    )
    return ok_result(
        summary,
        data={
            "segment_count": plan.segment_count,
            "total_duration_seconds": plan.total_duration_seconds,
            "output_size_bytes": output_size,
        },
        artifacts=[{"path": str(output_path), "kind": "video"}],
        warnings=plan.warnings,
    )
