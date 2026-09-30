# -*- coding: utf-8 -*-
"""文件名精简（重命名）。

这是本项目里**第一个会真正改动视频文件**的功能，所以规矩定得比别处严：

1. 默认只试运行，必须显式 --apply 才动文件；
2. 每一步都写进日志（默认 var\\rename_journal.csv），`rename --undo` 可原样回滚；
3. 改名后**直接就地更新索引库的 path / name / rel**，而不是让扫描去重认 ——
   这样文件 ID 不变，标签、哈希、探测结果全都原封不动跟着走；
4. 目标名冲突时自动加 -2、-3 后缀，绝不覆盖已存在的文件；
5. 只做重命名，不改扩展名、不移动目录。

精简规则（按顺序）：
    ⓪ 去掉爬虫/工具留下的前缀（Ezma_python1_pachong 这类）
    ① 去掉下载站的尾巴（- 黄色仓库-hsck.net 之类）
    ② 去掉营销词（详情介绍 / 在线观看 / 迅雷下载 / 高清资源 …）
    ③ 收敛多余分隔符
    ④ 去掉整段重复的标题（"标题-标题-标题" → "标题"）
    ⑤ 还是超长，就从头截断到指定长度
"""

from __future__ import annotations

import csv
import os
import re

from . import db, util

# ⓪ 爬虫前缀：纯 ASCII + 下划线（如 Ezma_python1_pachong），后面还得跟中文才敢删
SCRAPER_PREFIX = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+[\s\-_]*")
CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# ① 站点尾巴：从这些标记往后全部丢掉
TAIL_PATTERNS = [
    r"\s*[-_]\s*黄色仓库.*$",
    r"\s*[-_]\s*hsck\.net.*$",
    r"hsck\.net",
    r"\s*[-_]\s*高清资源.*$",
    r"\s*[-_]\s*ALIBB影视.*$",
    r"\s*[-_]\s*迅雷下载.*$",
    r"\s*[-_]\s*在线观看.*$",
]

# ② 营销词：直接删掉，不留分隔符
NOISE_WORDS = [
    "详情介绍", "在线观看", "迅雷下载", "免费在线观看", "高清资源",
    "在线播放", "未删减版", "完整版", "国语中字", "中文字幕",
]

# Windows 文件名里非法、或者容易惹麻烦的字符
ILLEGAL_CHARS = '<>:"/\\|?*'
JOURNAL_HEADERS = ["时间", "文件ID", "原名", "新名", "原路径", "新路径"]


def shorten_stem(stem: str, max_len: int = 30, folder: str = None) -> str:
    """把过长的文件名（不含扩展名）精简到不超过 max_len 个字符。

    folder 是文件所在的目录名，用来识别「冗余的目录名前缀」
    （ScreenRecorder\\Screenrecorder-2026-08-26-… 里的 Screenrecorder-）。
    """
    s = stem

    s = _strip_folder_prefix(s, folder)           # ⓪-a 冗余的目录名前缀

    # ⓪-b 爬虫前缀：Ezma_python1_pachong爬神奇的website2黑丝00后… → 黑丝00后…
    match = SCRAPER_PREFIX.match(s)
    if match:
        prefix = match.group(0).strip(" -_")
        rest = s[match.end():]
        if len(prefix) >= 8 and CJK.search(rest):
            s = rest

    for pattern in TAIL_PATTERNS:                 # ①
        s = re.sub(pattern, "", s, flags=re.IGNORECASE)

    for word in NOISE_WORDS:                      # ②
        s = s.replace(word, "")

    s = re.sub(r"[\s\-_]{2,}", "-", s)            # ③ 多个分隔符压成一个
    s = re.sub(r"^[\s\-_.]+", "", s)              # 去掉开头的分隔符
    s = s.strip(" -_.")

    segments = [seg.strip() for seg in s.split("-") if seg.strip()]   # ④ 去重复标题
    if len(segments) >= 2:
        segments = _collapse_segments(segments)
    s = "-".join(segments)
    s = re.sub(r"[\s\-_]{2,}", "-", s).strip(" -_.")

    if not s:                                     # 全被删光了，退回原名的头部
        s = stem.strip(" -_.")[:max_len]

    if len(s) > max_len:                          # ⑤
        s = s[:max_len].strip(" -_.")
    return _sanitize(s) or ("video" + str(abs(hash(stem)) % 10000))


def _strip_folder_prefix(stem, folder):
    """去掉与所在目录同名的前缀：ScreenRecorder\\Screenrecorder-2026-… → 2026-…

    只在「前缀后面紧跟分隔符」且「去掉后还剩得下东西」时才动手，
    所以 SM\\SM女王-Nana.mp4 这种不会被误切成 女王-Nana.mp4。
    """
    folder = (folder or "").strip()
    if len(folder) < 4 or not stem.lower().startswith(folder.lower()):
        return stem
    rest = stem[len(folder):]
    if not rest or rest[0] not in "-_ .":
        return stem
    trimmed = rest.lstrip("-_ .")
    if len(trimmed) < 4:                       # 去掉后太空了，宁可不动
        return stem
    return trimmed


def _collapse_segments(segments):
    """把整段重复的标题压成一份。

    要同时认得出这两种真实形态：
        "标题-标题"                    两份，第二份是被尾巴截断的
        "A-B-A-B-A-B-黄"               三份完整重复 + 一个多余的尾巴
    做法：找最小的周期 p，只要前 m 段能按 p 循环对上、且凑够两个完整周期，
    就把重复部分压成一份，剩下的段原样保留（不擅自丢信息）。
    """
    n = len(segments)
    best = None
    for period in range(1, n // 2 + 1):
        block = segments[:period]
        matched = 0
        while matched < n and segments[matched] == block[matched % period]:
            matched += 1
        if matched // period < 2:                 # 至少要重复两遍才敢压
            continue
        candidate = block + segments[matched:]
        if best is None or len(candidate) < len(best):
            best = candidate
    return best if best else segments


def _sanitize(name: str) -> str:
    out = "".join("_" if ch in ILLEGAL_CHARS or ord(ch) < 32 else ch for ch in name)
    return out.strip(" .")


# ------------------------------------------------------------------ 计划

def plan(conn, max_len: int = 30, min_len: int = 0, root: str = None,
         limit: int = None, only_longer_than: bool = True,
         exclude_globs=None) -> list:
    """生成改名计划；不碰磁盘，纯计算。

    only_longer_than=True 时只处理「名字长度 > max_len」的文件，
    也就是用户说的那种「大于 N 个字符才精简」。
    """
    import fnmatch

    exclude_globs = list(exclude_globs or [])
    where = ["missing = 0"]
    params = []
    if root:
        where.append("root = ?")
        params.append(root)
    sql = ("SELECT id, root, rel, path, name, ext, size, mtime FROM files "
           "WHERE %s ORDER BY path" % " AND ".join(where))
    rows = [dict(r) for r in conn.execute(sql, params)]

    used = {}          # 目录 -> 已占用的名字
    plans = []
    for row in rows:
        stem, ext = os.path.splitext(row["name"])
        if only_longer_than and len(stem) <= max_len:
            continue
        if min_len and len(stem) < min_len:
            continue
        if any(fnmatch.fnmatch(row["name"], pat) for pat in exclude_globs):
            continue

        directory = os.path.dirname(row["path"])
        taken = used.setdefault(directory, None)
        if taken is None:
            taken = set()
            try:
                for entry in os.scandir(util.long_path(directory)):
                    taken.add(entry.name.lower())
            except OSError:
                pass                      # 目录读不到就不做占用检查，后面还有存在性校验
            used[directory] = taken
        taken.discard(row["name"].lower())          # 自己让出原名

        new_stem = shorten_stem(stem, max_len, folder=os.path.basename(directory))
        new_name = new_stem + ext
        suffix = 2
        while new_name.lower() in taken:
            new_name = "%s-%d%s" % (new_stem, suffix, ext)
            suffix += 1
        taken.add(new_name.lower())

        new_path = os.path.join(directory, new_name)
        plans.append({
            "id": row["id"],
            "old_path": row["path"],
            "new_path": new_path,
            "old_name": row["name"],
            "new_name": new_name,
            "old_len": len(stem),
            "new_len": len(new_stem),
            "size": row["size"],
            "mtime": row["mtime"],
        })
        if limit and len(plans) >= int(limit):
            break
    return plans


# ------------------------------------------------------------------ 执行

def apply(conn, plans, journal_path: str, progress=None) -> dict:
    """执行改名：先动磁盘，成功一个就立刻更新索引和日志。"""
    util.ensure_dir(os.path.dirname(os.path.abspath(journal_path)))
    new_file = not os.path.isfile(journal_path)
    stamp = util.now_stamp()
    done, failed = 0, []

    with open(util.long_path(journal_path), "a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(JOURNAL_HEADERS)

        for plan in plans:
            old, new = plan["old_path"], plan["new_path"]
            if not os.path.isfile(util.long_path(old)):
                failed.append((old, "源文件不存在"))
                continue
            if os.path.exists(util.long_path(new)):
                failed.append((old, "目标已存在：%s" % plan["new_name"]))
                continue
            # 制定计划之后文件又变了（可能还在录制/拷贝），这时不要动它
            if plan.get("size") is not None:
                try:
                    now_size = os.stat(util.long_path(old)).st_size
                except OSError as exc:
                    failed.append((old, str(exc)))
                    continue
                if now_size != plan["size"]:
                    failed.append((old, "文件在计划之后仍在变化（可能正在写入），已跳过"))
                    continue
            try:
                os.rename(util.long_path(old), util.long_path(new))
            except OSError as exc:
                failed.append((old, "%s" % exc))
                continue

            root_row = conn.execute("SELECT root FROM files WHERE id = ?",
                                    (plan["id"],)).fetchone()
            root = root_row["root"] if root_row else os.path.dirname(old)
            conn.execute(
                "UPDATE files SET path = ?, name = ?, rel = ? WHERE id = ?",
                (new, plan["new_name"], os.path.relpath(new, root), plan["id"]))
            writer.writerow([stamp, plan["id"], plan["old_name"], plan["new_name"],
                             old, new])
            fh.flush()
            done += 1
            if progress and done % 10 == 0:
                progress("已改名 %d/%d…" % (done, len(plans)))

    conn.commit()
    return {"renamed": done, "failed": failed, "total": len(plans),
            "journal": journal_path}


def read_journal(journal_path: str) -> list:
    if not os.path.isfile(journal_path):
        return []
    rows = []
    with open(util.long_path(journal_path), "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("新路径") and row.get("原路径"):
                rows.append(row)
    return rows


def undo(conn, journal_path: str, steps: int = 0, progress=None) -> dict:
    """按日志把改名倒回去。steps<=0 表示全部回滚。"""
    rows = read_journal(journal_path)
    if not rows:
        return {"restored": 0, "failed": [], "remaining": 0}
    targets = rows[::-1]
    if steps and steps > 0:
        targets = targets[:int(steps)]

    restored, failed, handled = 0, [], []
    for row in targets:
        old, new = row["原路径"], row["新路径"]
        if not os.path.isfile(util.long_path(new)):
            failed.append((new, "当前文件不存在，跳过"))
            continue
        if os.path.exists(util.long_path(old)):
            failed.append((old, "原路径已被占用，跳过"))
            continue
        try:
            # 目录可能已被删掉，先补回来
            parent = os.path.dirname(old)
            if parent and not os.path.isdir(util.long_path(parent)):
                os.makedirs(util.long_path(parent))
            os.rename(util.long_path(new), util.long_path(old))
        except OSError as exc:
            failed.append((new, str(exc)))
            continue

        row_db = conn.execute("SELECT root FROM files WHERE path = ?",
                              (new,)).fetchone()
        if row_db:
            root = row_db["root"]
            conn.execute("UPDATE files SET path = ?, name = ?, rel = ? WHERE path = ?",
                         (old, os.path.basename(old), os.path.relpath(old, root), new))
        restored += 1
        handled.append(row)
        if progress and restored % 10 == 0:
            progress("已还原 %d/%d…" % (restored, len(targets)))

    conn.commit()
    # 处理过的记录从日志里移除，避免重复回滚
    if handled:
        remaining = [r for r in rows if r not in handled]
        util.ensure_dir(os.path.dirname(os.path.abspath(journal_path)))
        with open(util.long_path(journal_path), "w", encoding="utf-8-sig",
                  newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=JOURNAL_HEADERS)
            writer.writeheader()
            for row in remaining:
                writer.writerow(row)
    return {"restored": restored, "failed": failed, "remaining": len(rows) - restored}
