# -*- coding: utf-8 -*-
"""ffprobe 封装：一次调用拿到时长、分辨率、编码、码率、是否内嵌封面。"""

from __future__ import annotations

import json
import subprocess

from . import util

# 探测失败时写回数据库的占位结果
EMPTY = {
    "duration": None, "width": None, "height": None, "vcodec": None,
    "acodec": None, "bitrate": None, "container": None, "has_cover": None,
    "nb_streams": None, "probe_state": -1, "probe_error": None,
}


def _cmd(ffprobe: str, path: str) -> list:
    return [
        ffprobe, "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        util.long_path(path),
    ]


def probe(ffprobe: str, path: str, timeout: float = 60.0) -> dict:
    """探测单个文件。返回 dict，probe_state: 1 成功 / -1 失败。"""
    if not ffprobe:
        result = dict(EMPTY)
        result["probe_error"] = "找不到 ffprobe"
        return result

    try:
        proc = subprocess.run(
            _cmd(ffprobe, path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result = dict(EMPTY)
        result["probe_error"] = "ffprobe 超时"
        return result
    except OSError as exc:
        result = dict(EMPTY)
        result["probe_error"] = "无法执行 ffprobe: %s" % exc
        return result

    raw = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not raw.strip():
        result = dict(EMPTY)
        result["probe_error"] = (err.splitlines()[-1] if err else
                                 "ffprobe 返回码 %d" % proc.returncode)
        return result

    try:
        data = json.loads(raw)
    except ValueError as exc:
        result = dict(EMPTY)
        result["probe_error"] = "ffprobe 输出不是合法 JSON: %s" % exc
        return result

    return _parse(data)


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse(data: dict) -> dict:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []

    vcodec = acodec = None
    width = height = None
    has_cover = 0
    cover_count = 0
    for st in streams:
        kind = st.get("codec_type")
        disposition = st.get("disposition") or {}
        if kind == "video":
            # 内嵌封面在 ffmpeg 里是一条 attached_pic 视频流
            if int(disposition.get("attached_pic") or 0) == 1:
                has_cover = 1
                cover_count += 1
                continue
            if vcodec is None:
                vcodec = st.get("codec_name")
                width = _to_int(st.get("width"))
                height = _to_int(st.get("height"))
        elif kind == "audio" and acodec is None:
            acodec = st.get("codec_name")

    duration = _to_float(fmt.get("duration"))
    if duration is None:
        for st in streams:
            duration = _to_float(st.get("duration"))
            if duration:
                break

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "vcodec": vcodec,
        "acodec": acodec,
        "bitrate": _to_int(fmt.get("bit_rate")),
        "container": fmt.get("format_name"),
        "has_cover": has_cover,
        "nb_streams": len(streams) - cover_count,
        "probe_state": 1,
        "probe_error": None,
    }
