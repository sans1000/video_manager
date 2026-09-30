# -*- coding: utf-8 -*-
"""查重：三级校验。

  第 1 级  文件大小   —— 纯 SQL，秒级筛出可疑集合
  第 2 级  首尾各 1MiB —— 排除大小相同但内容不同的文件
  第 3 级  全文哈希     —— 最终确认

只有上一级仍然重复的文件才会进入下一级，所以不会真的去读完整个盘。
本模块只做「判定 + 报告」，绝不删除/移动任何文件。
"""

from __future__ import annotations

from . import hash as hashmod
from . import util


def _by_size(conn, kind, min_size):
    where = ["missing = 0", "size >= ?"]
    params = [int(min_size)]
    if kind and kind != "all":
        where.append("kind = ?")
        params.append(kind)
    sql = ("SELECT size, COUNT(*) AS n FROM files WHERE %s "
           "GROUP BY size HAVING n > 1 ORDER BY size DESC" % " AND ".join(where))
    return [dict(r) for r in conn.execute(sql, params)]


def _rows_of_size(conn, size, kind):
    where = ["missing = 0", "size = ?"]
    params = [size]
    if kind and kind != "all":
        where.append("kind = ?")
        params.append(kind)
    sql = ("SELECT id, path, name, size, mtime, kind, duration, quick_hash, full_hash "
           "FROM files WHERE %s ORDER BY mtime ASC" % " AND ".join(where))
    return [dict(r) for r in conn.execute(sql, params)]


def _group_by(rows, key):
    buckets = {}
    for row in rows:
        buckets.setdefault(row.get(key), []).append(row)
    return buckets


def find_groups(conn, kind: str = "video", min_size: int = 1,
                use_quick: bool = True, use_full: bool = True,
                workers: int = 4, algo: str = "blake2b",
                chunk_size: int = 1048576, progress=None,
                max_groups=None) -> dict:
    """返回 {'groups': [...], 'stats': {...}}。"""
    stats = {"size_groups": 0, "quick_groups": 0, "groups": 0,
             "files": 0, "wasted": 0, "hashed_quick": 0, "hashed_full": 0,
             "hash_errors": 0}

    if progress:
        progress("第 1 级：按文件大小分组…")
    size_groups = _by_size(conn, kind, min_size)
    stats["size_groups"] = len(size_groups)
    if not size_groups:
        return {"groups": [], "stats": stats}

    if progress:
        progress("第 1 级命中 %d 组同尺寸文件，开始第 2 级（首尾校验）…"
                 % len(size_groups))

    quick_groups = []
    if use_quick:
        for entry in size_groups:
            rows = _rows_of_size(conn, entry["size"], kind)
            pending = [r["path"] for r in rows if not r["quick_hash"]]
            if pending:
                digests = hashmod.hash_many(pending, "quick", workers,
                                            chunk_size, algo, progress)
                stats["hashed_quick"] += len(digests)
                stats["hash_errors"] += len(pending) - len(digests)
                _update_hashes(conn, digests, "quick_hash")
                for row in rows:
                    if not row["quick_hash"]:
                        row["quick_hash"] = digests.get(row["path"])
            for digest, group in _group_by(rows, "quick_hash").items():
                if digest and len(group) > 1:
                    quick_groups.append({"size": entry["size"],
                                         "quick_hash": digest,
                                         "rows": group})
        stats["quick_groups"] = len(quick_groups)
    else:
        quick_groups = [{"size": e["size"], "quick_hash": None,
                         "rows": _rows_of_size(conn, e["size"], kind)}
                        for e in size_groups]

    if not quick_groups:
        return {"groups": [], "stats": stats}

    groups = []
    if use_full:
        if progress:
            progress("第 2 级命中 %d 组，开始第 3 级（全文哈希）…" % len(quick_groups))
        for entry in quick_groups:
            rows = entry["rows"]
            pending = [r["path"] for r in rows if not r["full_hash"]]
            if pending:
                digests = hashmod.hash_many(pending, "full", workers,
                                            chunk_size, algo, progress)
                stats["hashed_full"] += len(digests)
                stats["hash_errors"] += len(pending) - len(digests)
                _update_hashes(conn, digests, "full_hash")
                for row in rows:
                    if not row["full_hash"]:
                        row["full_hash"] = digests.get(row["path"])
            for digest, group in _group_by(rows, "full_hash").items():
                if digest and len(group) > 1:
                    groups.append(_make_group(entry["size"], digest, group))
    else:
        for entry in quick_groups:
            groups.append(_make_group(entry["size"], entry["quick_hash"],
                                      entry["rows"]))

    groups.sort(key=lambda g: g["wasted"], reverse=True)
    if max_groups:
        groups = groups[:int(max_groups)]

    stats["groups"] = len(groups)
    stats["files"] = sum(g["count"] - 1 for g in groups)
    stats["wasted"] = sum(g["wasted"] for g in groups)
    return {"groups": groups, "stats": stats}


def _update_hashes(conn, mapping, column):
    if column not in ("quick_hash", "full_hash"):
        return
    if not mapping:
        return
    conn.executemany("UPDATE files SET %s = ? WHERE path = ?" % column,
                     [(h, p) for p, h in mapping.items()])
    conn.commit()


def _make_group(size, digest, rows):
    keeper = pick_keeper(rows)
    return {
        "size": size,
        "hash": digest,
        "count": len(rows),
        "wasted": size * (len(rows) - 1),
        "keeper_id": keeper["id"],
        "keeper_path": keeper["path"],
        "files": rows,
    }


def pick_keeper(rows):
    """从重复组里挑一个「最像原件」的：优先非副本名、路径浅、路径短、时间早。"""
    def score(row):
        name = row.get("name") or ""
        path = row.get("path") or ""
        return (
            1 if util.looks_like_copy(name) else 0,   # 副本名排后面
            path.count("\\"),                          # 目录层级浅的优先
            len(path),                                 # 路径短的优先
            row.get("mtime") or 0,                     # 时间早的优先
        )
    return sorted(rows, key=score)[0]
