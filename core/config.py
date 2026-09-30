# -*- coding: utf-8 -*-
"""配置加载：vm_config.toml（Python 3.11 用 tomllib，3.7 走内置的极简解析器）。

配置项都能被命令行参数覆盖，这里只负责「读文件 + 补默认值 + 找 ffmpeg」。
"""

from __future__ import annotations

import os
import shutil
import sys

from . import util

# 已知的 ffmpeg / ffprobe 位置（本机上验证过存在）
FFPROBE_CANDIDATES = [
    r"E:\app2\雷火映画\tools\ffmpeg\bin\ffprobe.exe",
    r"E:\app2\ACLOS\Cross\recorder-release\ffprobe.exe",
    r"E:\Delta Force\Delta Force\icreate\recorder-release\ffprobe.exe",
]
FFMPEG_CANDIDATES = [
    r"E:\app2\雷火映画\tools\ffmpeg\bin\ffmpeg.exe",
    r"E:\app2\ACLOS\Cross\recorder-release\ffmpeg.exe",
    r"D:\app\FormatFactory\ffmpeg.exe",
]

DEFAULTS = {
    "library": {
        # 默认管理的文件夹；命令行 --root 可覆盖，可写多个
        "roots": [r"F:\请输入文本\HSCK"],
        "recurse": True,
        "exclude_dirs": [],
        "exclude_globs": [],
    },
    "probe": {
        "ffprobe": "",
        "ffmpeg": "",
        "timeout_sec": 60,
        "probe_images": False,   # 图片默认不探测，加快扫描
        "workers": 4,
    },
    "hash": {
        "algo": "blake2b",       # blake2b / md5 / sha1 / sha256
        "chunk_size": 1048576,   # 1 MiB
        "workers": 4,
    },
    "dupes": {
        "min_size": 1,           # 小于该字节数的文件不参与查重
        "use_quick_hash": True,  # 大小相同 -> 首尾各 1MiB 哈希
        "use_full_hash": True,   # 仍相同 -> 全文哈希
    },
    "report": {
        "out_dir": "var/report",
    },
    "gui": {
        # 无边框窗口：去掉 Windows 自带标题栏，自己画最小化/最大化/关闭。
        # 出问题时可改成 false，或启动时加 --native-frame。
        "frameless": True,
        # 启动就最大化（占满屏幕可用区域）。
        "start_maximized": True,
        # start_maximized = false 时，窗口大小 = 屏幕可用区域的这个比例，居中显示。
        "window_fraction": 0.5,
    },
    "tags": {
        # 每次写标签后自动导出的备份；标签是手工数据，删库就没了，必须留后路
        "backup": "var/tags_backup.csv",
        "journal": "var/tag_journal.csv",
    },
    "db": {
        "path": "var/video_index.db",
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# ------------------------------------------------------------ 极简 TOML 解析

def _parse_scalar(raw: str):
    s = raw.strip()
    if not s:
        return ""
    if s[0] in "\"'" and s[-1] == s[0] and len(s) >= 2:
        body = s[1:-1]
        return (body.replace("\\\\", "\\").replace('\\"', '"').replace("\\'", "'")
                if s[0] == '"' else body)
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        items, depth, buf, quote = [], 0, "", ""
        for ch in inner:
            if quote:
                buf += ch
                if ch == quote:
                    quote = ""
                continue
            if ch in "\"'":
                quote = ch
                buf += ch
            elif ch == "," and depth == 0:
                items.append(_parse_scalar(buf))
                buf = ""
            else:
                if ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                buf += ch
        if buf.strip():
            items.append(_parse_scalar(buf))
        return items
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _mini_toml(text: str) -> dict:
    """只支持 [section] / key = value / 数组 / 字符串 / 布尔 / 数字 / # 注释。"""
    data, section = {}, None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉行尾注释（不处理引号内 # 的极端情况，够用即可）
        if "#" in line:
            head = line.split("#", 1)[0].rstrip()
            if head:
                line = head
            else:
                continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            section = data.setdefault(name, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        target = section if section is not None else data
        target[key.strip()] = _parse_scalar(value)
    return data


def load_config_file(path: str) -> dict:
    """读 TOML；文件不存在或格式有问题时返回空 dict，让默认值兜底。"""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    try:
        import tomllib                                    # Python 3.11+
        return tomllib.loads(text)
    except ImportError:
        pass
    except Exception:
        pass
    try:
        return _mini_toml(text)
    except Exception:
        return {}


# ------------------------------------------------------------ 路径解析

def is_frozen() -> bool:
    """是不是被打包成 exe 在跑（PyInstaller 会设 sys.frozen）。"""
    return bool(getattr(sys, "frozen", False))


def project_dir() -> str:
    """程序根目录：配置、索引库、缩略图缓存都以它为基准。

    源码运行时是 vm.py 所在目录；**打包成 exe 之后是 exe 所在目录**。
    这一步不能省：打包后 `__file__` 指向 PyInstaller 的解包目录
    （单文件模式下每次启动还是个新的临时目录），照它去算的话
    配置文件读不到、索引库和标签备份会写到临时目录、退出就被清掉 ——
    表现就是"每次都像第一次打开，什么都没有"。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_config_path() -> str:
    return os.path.join(project_dir(), "vm_config.toml")


def resolve_path(path: str) -> str:
    """相对路径一律相对项目目录，避免受当前工作目录影响。"""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(project_dir(), path))


def find_tool(explicit: str, candidates, names) -> str:
    """依次尝试：配置指定 -> 已知候选路径 -> PATH。"""
    if explicit and os.path.isfile(explicit):
        return explicit
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def tool_candidates(name):
    """程序自己旁边那几个常见的手放位置。

    ffmpeg 有几十 MB，不塞进安装包里；但用户把 ffmpeg.exe 丢在程序旁边
    也该能用 —— 便携软件就是这么放的。这几个位置排在写死的本机路径前面。
    """
    root = project_dir()
    return [os.path.join(root, name),
            os.path.join(root, "bin", name),
            os.path.join(root, "tools", name),
            os.path.join(root, "ffmpeg", "bin", name)]


class Config(object):
    """合并后的配置对象，属性访问 + 原始 dict 都可用。"""

    def __init__(self, data: dict, source: str = ""):
        self.data = data
        self.source = source
        lib = data["library"]
        self.roots = [resolve_path(r) for r in lib.get("roots") or []]
        self.recurse = bool(lib.get("recurse", True))
        self.exclude_dirs = set(util.DEFAULT_EXCLUDE_DIRS)
        for name in lib.get("exclude_dirs") or []:
            self.exclude_dirs.add(str(name).lower())
        self.exclude_globs = list(lib.get("exclude_globs") or [])

        self.probe_images = bool(data["probe"].get("probe_images", False))
        self.probe_workers = int(data["probe"].get("workers", 4))
        self.probe_timeout = float(data["probe"].get("timeout_sec", 60))
        self.ffprobe = find_tool(data["probe"].get("ffprobe", ""),
                                 tool_candidates("ffprobe.exe") + FFPROBE_CANDIDATES,
                                 ["ffprobe", "ffprobe.exe"])
        self.ffmpeg = find_tool(data["probe"].get("ffmpeg", ""),
                                tool_candidates("ffmpeg.exe") + FFMPEG_CANDIDATES,
                                ["ffmpeg", "ffmpeg.exe"])

        self.hash_algo = str(data["hash"].get("algo", "blake2b")).lower()
        self.hash_chunk = int(data["hash"].get("chunk_size", 1048576))
        self.hash_workers = int(data["hash"].get("workers", 4))

        self.dupes = dict(data["dupes"])
        self.report_dir = resolve_path(data["report"].get("out_dir", "var/report"))
        self.frameless = bool(data.get("gui", {}).get("frameless", True))
        self.start_maximized = bool(data.get("gui", {}).get("start_maximized", True))
        try:
            self.window_fraction = float(
                data.get("gui", {}).get("window_fraction", 0.5))
        except (TypeError, ValueError):
            self.window_fraction = 0.5
        self.tags_backup = resolve_path(data["tags"].get("backup", "var/tags_backup.csv"))
        self.tags_journal = resolve_path(data["tags"].get("journal", "var/tag_journal.csv"))
        self.db_path = resolve_path(data["db"].get("path", "var/video_index.db"))

    def rebind_db(self, db_path):
        """换一个索引库，并把标签备份/日志挪到库旁边。

        否则用 --db 打开另一个库（比如临时测试库）时，标签备份还是往主库的
        备份文件上写，会把真实标签的备份覆盖掉。
        """
        self.db_path = resolve_path(db_path)
        stem = os.path.splitext(self.db_path)[0]
        self.tags_backup = stem + "_tags_backup.csv"
        self.tags_journal = stem + "_tag_journal.csv"
        return self.db_path


def load(config_path: str = "") -> Config:
    path = config_path or default_config_path()
    data = _deep_merge(DEFAULTS, load_config_file(path))
    return Config(data, source=path if os.path.isfile(path) else "(默认值)")
