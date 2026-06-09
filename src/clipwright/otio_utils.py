"""otio_utils.py — OTIO ヘルパー（clip/gap/marker/metadata/summary）。

薄いラッパー層として OTIO オブジェクトの生成・I/O・メタデータ操作を担う。
時間変換は schemas.py の to_otio_time / from_otio_time を import して使う。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any

import opentimelineio as otio

from clipwright.schemas import (
    MediaRef,
    RationalTimeModel,
    TimeRangeModel,
    from_otio_time,
    to_otio_time,
)

# ===========================================================================
# Timeline 生成・I/O
# ===========================================================================


def new_timeline(name: str) -> otio.schema.Timeline:
    """新しい Timeline を生成する。

    §13.5 DC-AS-001 に従い [V1(Video), A1(Audio)] の順でトラックを生成する。
    フラット index: 0=V1, 1=A1。
    """
    timeline = otio.schema.Timeline(name=name)

    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)

    timeline.tracks.append(v1)
    timeline.tracks.append(a1)

    return timeline


def load_timeline(path: str) -> otio.schema.Timeline:
    """OTIO ファイルを読み込んで Timeline を返す。

    読み込み失敗は例外をそのまま伝播させる（呼び出し元が ClipwrightError に変換）。
    """
    result = otio.adapters.read_from_file(path)
    assert isinstance(result, otio.schema.Timeline), (
        f"OTIO ファイルが Timeline ではありません: {type(result)}"
    )
    return result


def save_timeline(timeline: otio.schema.Timeline, path: str) -> None:
    """Timeline をアトミックに保存する（temp → os.replace）。

    書き込み途中で失敗しても既存ファイルを破損しない。
    temp ファイルは同ディレクトリに .otio 拡張子で作成し、
    完了後 os.replace で置換する。

    OTIO は拡張子でアダプターを選ぶため、temp も .otio 拡張子を使う。
    """
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".otio")
    try:
        os.close(fd)
        otio.adapters.write_to_file(timeline, tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        # 書き込み失敗時は temp ファイルを削除して例外を再送出
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# ===========================================================================
# クリップ・ギャップ・マーカー追加
# ===========================================================================


def add_clip(
    track: otio.schema.Track,
    media: MediaRef,
    source_range: TimeRangeModel,
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> otio.schema.Clip:
    """Track にクリップを追加する。

    media の target_url を ExternalReference として設定する。
    返り値は追加した Clip オブジェクト。
    """
    ref = otio.schema.ExternalReference(target_url=media.target_url)
    sr = otio.opentime.TimeRange(
        start_time=to_otio_time(source_range.start_time),
        duration=to_otio_time(source_range.duration),
    )
    clip = otio.schema.Clip(
        name=name or "",
        media_reference=ref,
        source_range=sr,
    )
    if metadata is not None:
        clip.metadata["clipwright"] = metadata
    track.append(clip)
    return clip


def add_gap(
    track: otio.schema.Track,
    duration: RationalTimeModel,
) -> otio.schema.Gap:
    """Track にギャップを追加する。

    duration から source_range を構成して Gap を生成する。
    返り値は追加した Gap オブジェクト。
    """
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, duration.rate),
        duration=to_otio_time(duration),
    )
    gap = otio.schema.Gap(source_range=sr)
    track.append(gap)
    return gap


def add_marker(
    item: otio.core.Item,
    marked_range: TimeRangeModel,
    name: str,
    color: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> otio.schema.Marker:
    """item（Track / Clip 等）に Marker を付与する。

    §13.5 DC-GP-001 再: AddMarkerOp は track 自体（item=Track）に付ける。
    clip の存在を要求しない（空トラックも成功）。
    返り値は追加した Marker オブジェクト。
    """
    mr = otio.opentime.TimeRange(
        start_time=to_otio_time(marked_range.start_time),
        duration=to_otio_time(marked_range.duration),
    )
    marker_kwargs: dict[str, Any] = {"name": name, "marked_range": mr}
    if color is not None:
        marker_kwargs["color"] = color
    marker = otio.schema.Marker(**marker_kwargs)
    if metadata is not None:
        marker.metadata["clipwright"] = metadata
    item.markers.append(marker)
    return marker


# ===========================================================================
# Clipwright メタデータ（metadata["clipwright"] 配下）
# ===========================================================================


def set_clipwright_metadata(obj: Any, data: dict[str, Any]) -> None:
    """OTIO オブジェクトの metadata["clipwright"] 配下にデータを設定する（規約 §4.3）。

    他キーを汚染しない。上書き時は clipwright キー全体を置換する。
    """
    obj.metadata["clipwright"] = data


def get_clipwright_metadata(obj: Any) -> dict[str, Any]:
    """OTIO オブジェクトの metadata["clipwright"] 配下のデータを返す。

    未設定の場合は空 dict を返す。
    """
    return dict(obj.metadata.get("clipwright", {}))


# ===========================================================================
# Timeline サマリ（§13.5 DC-AM-001 再: 全件返却・truncation なし）
# ===========================================================================


def summarize_timeline(timeline: otio.schema.Timeline) -> dict[str, Any]:
    """Timeline の統計情報とマーカー一覧を返す。

    §13.5 DC-AM-001 再: 常に全件を返す（truncation なし）。
    閾値 50 の truncation は server.read_timeline の整形責務であり本関数は持たない。

    返り値キー:
      - clip_count: int
      - gap_count: int
      - marker_count: int
      - total_duration: RationalTimeModel（§13.5 DC-AM-002 再）
      - markers: list[dict] — [{name, time, kind}] 全件

    total_duration の算出規則（§13.5 DC-AM-002 再）:
      - 全トラック長の最大（合算ではない）
      - rate = V1 トラック（kind=Video）にクリップが存在すればその rate、無ければ 1000.0
      - クリップ 0 件なら RationalTime(0, グローバル rate)
    """
    clip_count = 0
    gap_count = 0
    markers: list[dict[str, Any]] = []

    # グローバル rate 決定: V1 の最初のクリップから rate を取得
    global_rate = _resolve_global_rate(timeline)

    # 全トラックを走査してカウント・マーカー収集
    track_durations_sec: list[float] = []
    for track in timeline.tracks:
        for item in track:
            if isinstance(item, otio.schema.Clip):
                clip_count += 1
            elif isinstance(item, otio.schema.Gap):
                gap_count += 1

        # トラックの duration（秒）を算出
        track_dur = _track_duration_sec(track)
        track_durations_sec.append(track_dur)

        # トラック自身の markers を収集
        for marker in track.markers:
            markers.append(_marker_to_dict(marker))

    # クリップに付いた markers も収集（track.markers に加えてクリップ内 markers も対象）
    for track in timeline.tracks:
        for item in track:
            if hasattr(item, "markers"):
                for marker in item.markers:
                    markers.append(_marker_to_dict(marker))

    # marker_count はマーカー総数（track + clip markers）
    marker_count = len(markers)

    # total_duration: 全トラック長の最大
    max_sec = max(track_durations_sec) if track_durations_sec else 0.0

    if max_sec == 0.0:
        total_duration = RationalTimeModel(value=0.0, rate=global_rate)
    else:
        # max を global_rate で表現
        total_value = max_sec * global_rate
        total_duration = RationalTimeModel(value=total_value, rate=global_rate)

    return {
        "clip_count": clip_count,
        "gap_count": gap_count,
        "marker_count": marker_count,
        "total_duration": total_duration,
        "markers": markers,
    }


def _resolve_global_rate(timeline: otio.schema.Timeline) -> float:
    """グローバル rate を決定する。

    V1（kind=Video）トラックに1件以上のクリップがある場合はその最初のクリップの rate。
    それ以外（V1 が空・V1 が存在しない）は 1000.0。
    """
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            for item in track:
                if isinstance(item, otio.schema.Clip) and item.source_range is not None:
                    return float(item.source_range.duration.rate)
            # V1 は存在するがクリップなし
            break
    return 1000.0


def _track_duration_sec(track: otio.schema.Track) -> float:
    """トラックの合計 duration を秒で返す。

    OTIO Track の duration() を使う。クリップが0件なら 0.0。
    """
    try:
        dur = track.duration()
        return float(dur.to_seconds())
    except Exception:
        return 0.0


def _marker_to_dict(marker: otio.schema.Marker) -> dict[str, Any]:
    """Marker オブジェクトを辞書に変換する。"""
    time_model = from_otio_time(marker.marked_range.start_time)
    cw_meta = marker.metadata.get("clipwright", {})
    kind = cw_meta.get("kind", "") if isinstance(cw_meta, dict) else ""
    return {
        "name": marker.name,
        "time": time_model,
        "kind": kind,
    }
