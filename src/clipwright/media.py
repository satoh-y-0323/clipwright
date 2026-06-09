"""media.py — ffprobe ラッパー。

メディアファイルを ffprobe でプローブし、構造化した MediaInfo を返す。
ffprobe の呼び出しは process.run に委譲し、サブプロセス規律（§6.5）を守る。
"""

from __future__ import annotations

import json
from pathlib import Path

import clipwright.process as _process_module
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo


def inspect_media(path: str) -> MediaInfo:
    """メディアファイルを ffprobe でプローブして MediaInfo を返す。

    入力ファイルの存在確認 → ffprobe 探索 → サブプロセス実行 → JSON パース の順。
    ffprobe は CLIPWRIGHT_FFPROBE 環境変数 → shutil.which の順で探す（ADR-3）。

    Args:
        path: プローブ対象のメディアファイルパス。

    Returns:
        パース済みの MediaInfo インスタンス。

    Raises:
        ClipwrightError: ファイル不在（FILE_NOT_FOUND）、ffprobe 不在
            （DEPENDENCY_MISSING）、JSON パース失敗（PROBE_FAILED）、
            サブプロセス失敗（SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT）。
    """
    _validate_existing_file(path)
    ffprobe = _process_module.resolve_tool("ffprobe", "CLIPWRIGHT_FFPROBE")

    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = _process_module.run(cmd, timeout=30.0)
    return _parse_ffprobe_json(path, result.stdout)


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------


def _to_optional_int(val: object) -> int | None:
    """任意の値を int に変換する。変換できない場合は None を返す。

    JSON パース後のフィールド値（int / 数値文字列 / None 等）を安全に int へ変換する
    ためのヘルパー（L-2: CR-Q-002）。float 文字列（"1.5" 等）は変換不可として None。

    Args:
        val: 変換対象の値。

    Returns:
        変換後の int、または変換不可の場合は None。
    """
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return None
    return None


def _validate_existing_file(path: str) -> None:
    """ファイルが存在することを確認する。

    シンボリックリンクは拒否する（F-04: SR-V-002）。
    存在しない場合は FILE_NOT_FOUND を送出する。
    """
    p = Path(path)
    if p.is_symlink():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"シンボリックリンクは受け付けません: {path}",
            hint="シンボリックリンクではなく実ファイルのパスを指定してください。",
        )
    if not p.is_file():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"ファイルが見つかりません: {path}",
            hint="パスが正しいか、ファイルが存在するか確認してください。",
        )


def _parse_avg_frame_rate(avg_frame_rate: str) -> float:
    """ffprobe の avg_frame_rate 文字列（例: "30/1", "24000/1001"）を float に変換する。

    不正な形式の場合は 0.0 を返す（呼び出し元で映像ストリームとして扱わない）。
    """
    if "/" in avg_frame_rate:
        parts = avg_frame_rate.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
            if den == 0.0:
                return 0.0
            return num / den
        except ValueError:
            return 0.0
    try:
        return float(avg_frame_rate)
    except ValueError:
        return 0.0


def _parse_ffprobe_json(path: str, stdout: str) -> MediaInfo:
    """ffprobe の JSON 出力を MediaInfo へ構造化する。

    JSON パースや必須フィールドの欠落時は PROBE_FAILED を送出する。
    rate 決定規則（§13.3 DC-AS-006）:
      - 映像ストリームがあれば第1映像の avg_frame_rate を rate とする
      - 音声のみ素材は rate = 1000.0
    duration.value は秒 × rate で計算したフレーム数を保持する。

    Args:
        path: 元の入力ファイルパス（MediaInfo.path に設定する）。
        stdout: ffprobe が出力した JSON 文字列。

    Returns:
        パース済みの MediaInfo インスタンス。

    Raises:
        ClipwrightError: JSON パース失敗または必須フィールド欠落（PROBE_FAILED）。
    """
    if not stdout:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe が空の出力を返しました",
            hint="入力ファイルが有効なメディアファイルか確認してください。",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        # パーサ内部エラー文字列は露出させず汎用メッセージに統一する（R3-L-02）
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe の出力が有効な JSON ではありません。",
            hint="入力ファイルが有効なメディアファイルか確認してください。",
        ) from exc

    # 必須フィールドの確認
    if "streams" not in data or "format" not in data:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message="ffprobe の JSON に必須フィールド（streams / format）がありません",
            hint="入力ファイルが有効なメディアファイルか確認してください。",
        )

    raw_streams: list[dict[str, object]] = data["streams"]
    raw_format: dict[str, object] = data["format"]

    # ストリーム情報を構造化する
    streams: list[StreamInfo] = []
    for s in raw_streams:
        codec_name_raw = s.get("codec_name")
        index_raw = s.get("index", 0)

        streams.append(
            StreamInfo(
                index=_to_optional_int(index_raw) or 0,
                codec_type=str(s.get("codec_type", "")),
                codec_name=str(codec_name_raw) if codec_name_raw is not None else None,
                width=_to_optional_int(s.get("width")),
                height=_to_optional_int(s.get("height")),
                sample_rate=_to_optional_int(s.get("sample_rate")),
                channels=_to_optional_int(s.get("channels")),
            )
        )

    # rate 決定規則（§13.3 DC-AS-006）
    # 第1映像ストリームの avg_frame_rate を採用する。音声のみは 1000.0。
    rate = 1000.0
    for s in raw_streams:
        if str(s.get("codec_type", "")) == "video":
            avg_frame_rate_raw = s.get("avg_frame_rate", "")
            if avg_frame_rate_raw:
                parsed_rate = _parse_avg_frame_rate(str(avg_frame_rate_raw))
                if parsed_rate > 0.0:
                    rate = parsed_rate
                    break

    # duration を RationalTimeModel で表現する
    duration: RationalTimeModel | None = None
    duration_raw = raw_format.get("duration")
    if duration_raw is not None:
        try:
            duration_sec = float(str(duration_raw))
            # value = 秒 × rate（フレーム数相当）
            duration = RationalTimeModel(value=duration_sec * rate, rate=rate)
        except (ValueError, TypeError):
            pass

    container = str(raw_format.get("format_name", "")) or None

    return MediaInfo(
        path=path,
        container=container,
        duration=duration,
        streams=streams,
    )
