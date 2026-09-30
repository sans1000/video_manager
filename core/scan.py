# -*- coding: utf-8 -*-
"""扫描：把磁盘上的文件同步进 SQLite 索引。

只读操作——只读磁盘元数据 + 写自己的索引库，绝不修改被管理的文件。
"""

from __future__ import annotations

import fnmatch
import os
from concurrent.futures import ThreadPoolExecutor

from . import db, probe, util


def iter_files(root: str, recurse: bool = True, exclude_dirs=None,
               exclude_globs=None, on_error=None):
    """遍历目录，产出文件记录 dict。

    用 os.scandir 拿 stat 缓存，比 os.walk + os.stat 快得多。
    """
    exclude_dirs = exclude_dirs or set()
    exclude_globs = list(exclude_globs or [])
    root = os.path.abspath(root)

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(util.long_path(current)))
        except OSError as exc:
            if on_error:
                on_error(current, str(exc))
            continue

        for entry in entries:
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if not recurse:
                    continue
                if name.lower() in exclude_dirs:
                    continue
                if entry.is_symlink():
                    continue
                stack.append(entry.path)
                continue

            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                if on_error:
                    on_error(entry.path, str(exc))
                continue

            path = util.strip_long(entry.path)
            rel = os.path.relpath(path, root)
            if exclude_globs:
                hit = any(fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel, pat)
                          for pat in exclude_globs)
                if hit:
                    continue

            ext = os.path.splitext(name)[1]
            yield {
                "root": root,
                "rel": rel,
                "path": path,
                "name": name,
                "ext": ext.lower(),
                "kind": util.classify(ext),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "ctime": stat.st_ctime,
            }


def sync_root(conn, root: str, recurse: bool = True, exclude_dirs=None,
              exclude_globs=None, progress=None, limit=None) -> dict:
    """扫描一个根目录并写入索引；文件和大小都没变的记录直接跳过。"""
    stamp = util.now_stamp()
    existing = db.load_existing(conn, root)
    seen = set()
    summary = {"root": root, "seen": 0, "new": 0, "updated": 0,
               "unchanged": 0, "missing": 0, "errors": 0, "by_kind": {}}

    def on_error(path, message):
        summary["errors"] += 1
        if progress:
            progress("跳过（无法读取）：%s -> %s" % (path, message))

    for rec in iter_files(root, recurse, exclude_dirs, exclude_globs, on_error):
        seen.add(rec["path"])
        summary["seen"] += 1
        kind = rec["kind"]
        summary["by_kind"][kind] = summary["by_kind"].get(kind, 0) + 1

        old = existing.get(rec["path"])
        if old is not None and old["size"] == rec["size"] and abs((old["mtime"] or 0) - rec["mtime"]) < 1e-6:
            summary["unchanged"] += 1
            if old.get("missing"):
                db.touch_file(conn, old["id"], stamp)
            continue

        rec["first_seen"] = (old or {}).get("first_seen") or stamp
        rec["last_seen"] = stamp
        rec["missing"] = 0
        if old is None:
            rec["probe_state"] = 0
            db.insert_file(conn, rec)
            summary["new"] += 1
        else:
            # 内容变了：清空旧的探测结果和哈希，等下一轮重新算
            for field in db.VIDEO_FIELDS + ("quick_hash", "full_hash"):
                rec[field] = None
            rec["probe_state"] = 0
            db.update_file(conn, old["id"], rec)
            summary["updated"] += 1

        if progress and summary["seen"] % 200 == 0:
            progress("已扫描 %d 个文件…" % summary["seen"])

        if limit and summary["seen"] >= limit:
            break

    summary["missing"] = db.mark_missing(conn, root, seen)
    conn.commit()
    return summary


def probe_pending(conn, config, roots, force: bool = False, limit=None,
                  progress=None) -> dict:
    """给还没探测过的视频补元数据（时长/分辨率/编码/封面）。"""
    where = ["missing = 0", "kind = 'video'"]
    params = []
    if roots:
        where.append("root IN (%s)" % ", ".join("?" for _ in roots))
        params.extend(roots)
    if not force:
        where.append("probe_state = 0")
    rows = list(db.iter_rows(conn,
                             "SELECT id, path FROM files WHERE %s ORDER BY size DESC"
                             % " AND ".join(where), params))
    if limit:
        rows = rows[:int(limit)]

    summary = {"probed": 0, "failed": 0, "total": len(rows)}
    if not rows:
        return summary

    ffprobe = config.ffprobe
    timeout = config.probe_timeout
    workers = max(1, int(config.probe_workers))

    def job(row):
        return row["id"], probe.probe(ffprobe, row["path"], timeout)

    def apply(file_id, result):
        db.update_file(conn, file_id, result)
        summary["probed"] += 1
        if result["probe_state"] != 1:
            summary["failed"] += 1
            if progress:
                progress("探测失败：id=%d %s" % (file_id, result.get("probe_error")))
        if progress and summary["probed"] % 25 == 0:
            progress("已探测 %d/%d…" % (summary["probed"], len(rows)))

    if workers == 1:
        for row in rows:
            file_id, result = job(row)
            apply(file_id, result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for file_id, result in pool.map(job, rows):
                apply(file_id, result)

    conn.commit()
    return summary
