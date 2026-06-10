"""plan.py — clipwright-render の純ロジック層。

ffmpeg/ffprobe を一切実行しない。probe 結果は ProbeInfo を引数で受ける（DC-AM-007）。
タイムライン解析・filter_complex 構築・dry_run 概算の3責務を持つ。

設計判断:
- 再エンコードは1回（ADR-1）: filter_complex で trim+concat を使うため
  フレーム精度の時刻制御が可能で、区間を重ねて再エンコードするより劣化が少ない。
- concat=n=1 一律（DC-AS-005）: 1区間でも分岐をなくすことで実装を単純化する。
  ffmpeg は n=1 を正常に処理する。
- 音声複数時は第1音声のみ採用（ADR-7）: 複数音声ストリームのマッピングは
  複雑さを大幅に増すため、本イテレーションでは第1音声のみを対象とする。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.schemas import RenderOptions

# ===========================================================================
# データ型
# ===========================================================================


@dataclass
class KeptRange:
    """タイムライン上の残区間を表す値オブジェクト。

    source: メディアファイルの target_url（ソースパス）
    source_range: OTIO TimeRange（opentime で保持し秒変換は遅延）
    """

    source: str
    source_range: otio.opentime.TimeRange


@dataclass
class ProbeInfo:
    """ffprobe の probe 結果を表す値オブジェクト（DC-AM-007）。

    plan.py は本型を引数で受け取り、subprocess を一切呼ばない。
    bit_rate: None の場合は概算サイズが算出不能（ADR-3）。
    """

    has_video: bool
    audio_count: int
    bit_rate: int | None = None


@dataclass
class RenderPlan:
    """build_plan が返す実行計画。

    filter_complex: ffmpeg -filter_complex 引数の単一文字列（インジェクション防止）。
    ffmpeg_args: ffmpeg へ渡す引数リスト（-fc 以外）。str のみ（M-1）。
    segment_count: 残区間数。
    total_duration_seconds: 出力総尺（秒）。
    estimated_size_bytes: 概算ファイルサイズ（bytes）。bit_rate None 時は None。
    warnings: dry_run 概算の注意事項。
    """

    filter_complex: str
    ffmpeg_args: list[str]
    segment_count: int
    total_duration_seconds: float
    estimated_size_bytes: float | None = None
    warnings: list[str] = field(default_factory=list)


# ===========================================================================
# resolve_kept_ranges
# ===========================================================================


def resolve_kept_ranges(timeline: otio.schema.Timeline) -> list[KeptRange]:
    """先頭 video トラックの Clip を走査し、残区間リストを返す（ADR-5/DC-AS-006）。

    - Gap はスキップ（除去領域の表現のため）。
    - Transition が含まれる場合は UNSUPPORTED_OPERATION を送出する。
    - video トラックが2本以上ある場合は UNSUPPORTED_OPERATION を送出する。
    - すべての Clip が同一 target_url を持つことを検証する（単一ソース前提）。
      不一致の場合は UNSUPPORTED_OPERATION を送出する。
    - Clip が0件の場合は INVALID_INPUT を送出する。

    Returns:
        KeptRange のリスト（source と source_range を opentime で保持）。
    """
    # 先頭 video トラックを取得（複数 video トラックは非対応）
    video_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) >= 2:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="video トラックが2本以上含まれています。",
            hint=(
                "先頭の video トラック1本のみを持つ OTIO"
                " タイムラインを使用してください。"
            ),
        )

    if len(video_tracks) == 0:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="video トラックが見つかりません。",
            hint="video トラックを含む OTIO タイムラインを使用してください。",
        )

    video_track = video_tracks[0]

    ranges: list[KeptRange] = []
    first_source: str | None = None

    for item in video_track:
        if isinstance(item, otio.schema.Gap):
            # Gap は除去領域を表すためスキップ
            continue
        if isinstance(item, otio.schema.Transition):
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="Transition が含まれています。",
                hint="Transition を含まない OTIO タイムラインを使用してください。",
            )
        if isinstance(item, otio.schema.Clip):
            mr = item.media_reference
            if isinstance(mr, otio.schema.MissingReference):
                # MissingReference はタイムラインのデータ不正（参照欠落）を意味する。
                # 「サポートしていない構成」（UNSUPPORTED_OPERATION）ではなく
                # 「データが不正」（INVALID_INPUT）として扱う。
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message="メディア参照が欠落しています（MissingReference）。",
                    hint="target_url を持つ ExternalReference を使用してください。",
                )
            if not isinstance(mr, otio.schema.ExternalReference):
                # 非対応構成（GeneratorReference 等）は UNSUPPORTED_OPERATION。
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message="ExternalReference 以外のメディア参照は非対応です。",
                    hint=("target_url を持つ ExternalReference を使用してください。"),
                )
            source = mr.target_url
            # 単一ソース検証（DC-AS-007）
            if first_source is None:
                first_source = source
            elif source != first_source:
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message=(
                        "複数の source が検出されました:"
                        f" {first_source!r} と {source!r}。"
                    ),
                    hint="単一ソースファイルのみを使用してください。",
                )
            source_range = item.source_range
            ranges.append(KeptRange(source=source, source_range=source_range))

    if len(ranges) == 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="残区間が0件です（Clip が見つかりません）。",
            hint=("少なくとも1件の Clip を含む OTIO タイムラインを使用してください。"),
        )

    return ranges


# ===========================================================================
# build_plan
# ===========================================================================


def _to_seconds(rt: otio.opentime.RationalTime) -> float:
    """RationalTime を秒（小数6桁）に変換する。

    OTIO の型スタブが to_seconds() を Any で定義しているため、
    明示的に float へキャストして mypy strict を通す。
    """
    return round(float(rt.to_seconds()), 6)


def build_plan(
    ranges: list[KeptRange],
    probe_info: ProbeInfo,
    options: RenderOptions,
) -> RenderPlan:
    """filter_complex 文字列と ffmpeg 引数配列を RenderPlan で返す（ADR-1/ADR-7）。

    - 映像なしは UNSUPPORTED_OPERATION（DC-AS-002）。
    - 区間1件も concat=n=1 を一律使用（DC-AS-005）。
    - 音声0: a=0（-map [outv] のみ）。
    - 音声1以上: a=1、第1音声のみ採用（ADR-7）。
    - trim 座標は opentime→秒（小数6桁）に変換し数値として引数化（DC-AS-004）。
    - filter_complex は単一文字列として返す（コマンドインジェクション回避）。
    - bit_rate が None の場合は estimated_size_bytes=None + warnings 追加（ADR-3）。
    - codec 等のいずれか非 None 指定時に「概算は目安」warning（DC-AM-005）。
    """
    if not probe_info.has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="映像ストリームが含まれていません。",
            hint="映像ストリームを持つメディアファイルを使用してください。",
        )

    # 音声有無: 複数音声は第1音声のみ採用（a=1 として扱う）
    has_audio = probe_info.audio_count >= 1
    n = len(ranges)

    # ---------- filter_complex 構築 ----------
    # 各区間の trim/atrim フィルタセグメントを生成
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        start = _to_seconds(r.source_range.start_time)
        end = round(start + _to_seconds(r.source_range.duration), 6)
        vl = f"v{i}"
        filter_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[{vl}]"
        )
        video_labels.append(f"[{vl}]")

        if has_audio:
            al = f"a{i}"
            filter_parts.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{al}]"
            )
            audio_labels.append(f"[{al}]")

    # concat フィルタ（ビデオ/オーディオラベルをインターリーブして入力する）
    v_count = 1
    a_count = 1 if has_audio else 0
    if has_audio:
        interleaved: list[str] = []
        for vl, al in zip(video_labels, audio_labels, strict=True):
            interleaved.append(vl)
            interleaved.append(al)
        input_labels = "".join(interleaved)
    else:
        input_labels = "".join(video_labels)

    concat_output = "[outv]" if not has_audio else "[outv][outa]"
    filter_parts.append(
        f"{input_labels}concat=n={n}:v={v_count}:a={a_count}{concat_output}"
    )

    # width/height 指定時: scale を filter_complex 内に統合する（ADR-1 準拠）。
    # -vf と -filter_complex を同時指定すると ffmpeg がエラーを返すため、
    # concat 出力 [outv] に対して scale フィルタを連結して [outvscaled] を生成し
    # -map [outvscaled] に差し替える。
    use_scale = options.width is not None and options.height is not None
    if use_scale:
        filter_parts.append(f"[outv]scale={options.width}:{options.height}[outvscaled]")
        video_map_label = "[outvscaled]"
    else:
        video_map_label = "[outv]"

    filter_complex = ";".join(filter_parts)

    # ---------- ffmpeg_args 構築 ----------
    # ffmpeg_args は list[str] に統一する。数値は str() で変換して格納する（M-1）。
    ffmpeg_args: list[str] = [
        "-filter_complex",
        filter_complex,
        "-map",
        video_map_label,
    ]
    if has_audio:
        ffmpeg_args += ["-map", "[outa]"]

    # RenderOptions → ffmpeg 引数への写像
    if options.video_codec is not None:
        ffmpeg_args += ["-c:v", options.video_codec]
    if options.audio_codec is not None:
        ffmpeg_args += ["-c:a", options.audio_codec]
    # width/height は filter_complex 内に統合済みのため -vf は追加しない（L-4）
    if options.fps is not None:
        ffmpeg_args += ["-r", str(options.fps)]
    if options.crf is not None:
        ffmpeg_args += ["-crf", str(options.crf)]

    # ---------- dry_run 概算 ----------
    total_duration = sum(_to_seconds(r.source_range.duration) for r in ranges)

    estimated_size: float | None = None
    warnings: list[str] = []

    if probe_info.bit_rate is not None:
        estimated_size = probe_info.bit_rate * total_duration / 8.0
    else:
        warnings.append(
            "bit_rate が取得できないため概算ファイルサイズを算出できません。"
        )

    # codec/解像度/fps/crf/audio_codec のいずれかが指定された場合は「概算は目安」warning
    # audio_codec も出力ビットレートを変えるため概算精度に影響する（DC-AM-005 適用）
    if (
        options.video_codec is not None
        or options.audio_codec is not None
        or options.width is not None
        or options.height is not None
        or options.fps is not None
        or options.crf is not None
    ):
        warnings.append(
            "変換オプション（codec/解像度/fps/crf）が指定されているため、"
            "概算ファイルサイズはあくまで目安です。実際のサイズは異なる場合があります。"
        )

    return RenderPlan(
        filter_complex=filter_complex,
        ffmpeg_args=ffmpeg_args,
        segment_count=n,
        total_duration_seconds=total_duration,
        estimated_size_bytes=estimated_size,
        warnings=warnings,
    )
