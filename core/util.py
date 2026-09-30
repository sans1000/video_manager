# -*- coding: utf-8 -*-
"""通用工具：长路径、单位换算、扩展名分类、控制台输出。

本模块刻意只依赖标准库，且兼容 Python 3.7+（脚本用 py / python 都能跑）。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata

# ---------------------------------------------------------------- 扩展名分类

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv", ".wmv", ".ts",
    ".m2ts", ".mts", ".mpg", ".mpeg", ".rmvb", ".rm", ".3gp", ".3g2", ".vob",
    ".f4v", ".asf", ".ogv", ".divx", ".mxf", ".m2v",
}

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif",
    ".dng", ".tif", ".tiff", ".jfif", ".avif",
}

AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".wma", ".ape", ".opus",
}

# 扫描时默认跳过的目录名（小写比较）
DEFAULT_EXCLUDE_DIRS = {
    "$recycle.bin", "system volume information", ".git", ".svn", "__pycache__",
    "node_modules", ".dsh", ".npm-cache", ".idea", ".vscode",
}

# 形似「副本」的文件名特征，查重时用来判断谁更像是原件
COPY_HINTS = (
    "_1", "_2", "(1)", "(2)", "（1）", "（2）", " - 副本", "-副本", "副本",
    "copy", "拷贝", " 的副本",
)


def classify(ext: str) -> str:
    """按扩展名分类：video / image / audio / other。"""
    e = (ext or "").lower()
    if e in VIDEO_EXTS:
        return "video"
    if e in IMAGE_EXTS:
        return "image"
    if e in AUDIO_EXTS:
        return "audio"
    return "other"


def looks_like_copy(name: str) -> bool:
    """文件名是否带有「副本」特征。"""
    stem = os.path.splitext(name)[0].lower()
    for hint in COPY_HINTS:
        if stem.endswith(hint):
            return True
    return False


# ---------------------------------------------------------------- 长路径支持

def long_path(path: str) -> str:
    r"""Windows 长路径前缀。

    只在绝对路径长度 >= 240 时加 \\?\，避免影响 ffprobe 等外部程序读取。
    """
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if len(p) < 240:
        return p
    if p.startswith("\\\\"):            # UNC: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def strip_long(path: str) -> str:
    r"""去掉 \\?\ 前缀，还原成可读、可入库的路径。"""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


# ---------------------------------------------------------------- 格式化

def human_size(num) -> str:
    """字节数转人类可读，例如 1536 -> 1.5 KB。"""
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "-"
    neg = n < 0
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            if unit == "B":
                text = "%d B" % int(n)
            else:
                text = "%.2f %s" % (n, unit)
            return ("-" + text) if neg else text
        n /= 1024.0
    return "-"


def parse_size(text) -> int:
    """把 '100MB' / '1.5G' / '2048' 解析成字节数。"""
    if text is None:
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).strip().lower().replace(" ", "")
    if not s:
        return 0
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([kmgtp]?)(i?b?)?$", s)
    if not m:
        raise ValueError("无法识别的容量写法：%s" % text)
    value = float(m.group(1))
    unit = m.group(2) or ""
    scale = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3,
             "t": 1024 ** 4, "p": 1024 ** 5}[unit]
    return int(value * scale)


def human_duration(seconds) -> str:
    """秒数转 H:MM:SS / M:SS。"""
    if seconds is None:
        return "-"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "-"
    if total < 0:
        return "-"
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def parse_duration(text) -> float:
    """把 '10m' / '1h30m' / '90' / '00:10:00' 解析成秒。"""
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().lower()
    if not s:
        return 0.0
    if ":" in s:
        parts = [float(x) for x in s.split(":")]
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return total
    m = re.match(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s?)?$", s)
    if not m or not any(m.groups()):
        raise ValueError("无法识别的时长写法：%s" % text)
    h = float(m.group(1) or 0)
    mi = float(m.group(2) or 0)
    sec = float(m.group(3) or 0)
    return h * 3600 + mi * 60 + sec


def natural_key(text: str):
    """自然排序：TMY2 排在 TMY10 前面。"""
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", text or "")]


def disp_width(text: str) -> int:
    """终端显示宽度（中日韩字符算 2 列）。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate(text: str, limit: int) -> str:
    """按显示宽度截断，超出加省略号。"""
    text = text or ""
    if disp_width(text) <= limit:
        return text
    out = []
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > limit - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def render_table(headers, rows, max_col_width=60):
    """渲染一张对齐的文本表格（中文按 2 列宽计算）。"""
    cols = len(headers)
    cells = []
    for row in rows:
        cells.append([truncate(str(c) if c is not None else "-", max_col_width)
                      for c in list(row)[:cols]])
    widths = [disp_width(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], disp_width(c))

    def pad(text, width):
        return text + " " * max(0, width - disp_width(text))

    lines = ["  ".join(pad(h, widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in cells:
        lines.append("  ".join(pad(c, widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)


# ---------------------------------------------------------------- 其它

def setup_console():
    """把 stdout/stderr 切到 UTF-8，避免中文文件名在 GBK 控制台崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ensure_dir(path: str) -> str:
    if path and not os.path.isdir(path):
        os.makedirs(path)
    return path


def file_uri(path: str) -> str:
    """生成 HTML 里可点击的 file:/// 链接。"""
    try:
        from pathlib import Path
        return Path(path).as_uri()
    except Exception:
        return "file:///" + path.replace("\\", "/").replace(" ", "%20")


def now_stamp() -> str:
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 标签名规范

def normalize_tag(name: str) -> str:
    """标签显示名：去首尾空白、内部连续空白压成一个、Unicode NFC 规范化。"""
    text = unicodedata.normalize("NFC", str(name or ""))
    return re.sub(r"\s+", " ", text).strip()


def tag_key(name: str) -> str:
    """标签唯一键。

    在显示名基础上再折叠大小写，避免 '1080P' 和 '1080p'、
    'Cos' 和 'cos' 变成两个互不相干的标签。
    """
    return normalize_tag(name).casefold()
