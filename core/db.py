# -*- coding: utf-8 -*-
"""SQLite 索引层。

兼容 Python 3.7 自带的 SQLite 3.21：不使用 UPSERT / 窗口函数，
一律用「先查后改」的写法。
"""

from __future__ import annotations

import os
import sqlite3

from . import util

SCHEMA_VERSION = "2"

DDL = """
CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    root         TEXT    NOT NULL,
    rel          TEXT    NOT NULL,
    path         TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    ext          TEXT,
    kind         TEXT,
    size         INTEGER,
    mtime        REAL,
    ctime        REAL,
    duration     REAL,
    width        INTEGER,
    height       INTEGER,
    vcodec       TEXT,
    acodec       TEXT,
    bitrate      INTEGER,
    container    TEXT,
    has_cover    INTEGER,
    nb_streams   INTEGER,
    quick_hash   TEXT,
    full_hash    TEXT,
    probe_state  INTEGER DEFAULT 0,
    probe_error  TEXT,
    missing      INTEGER DEFAULT 0,
    first_seen   TEXT,
    last_seen    TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_kind     ON files(kind);
CREATE INDEX IF NOT EXISTS idx_files_ext      ON files(ext);
CREATE INDEX IF NOT EXISTS idx_files_size     ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_duration ON files(duration);
CREATE INDEX IF NOT EXISTS idx_files_quick    ON files(quick_hash);
CREATE INDEX IF NOT EXISTS idx_files_full     ON files(full_hash);
CREATE INDEX IF NOT EXISTS idx_files_missing  ON files(missing);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------- 标签系统
-- 标签字典。key 是规范化后的唯一键（去空白 + 折叠大小写），name 是显示名。
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    note       TEXT,
    created_at TEXT
);

-- 文件与标签的多对多关系。用关系表而不是往字段里塞 "a,b"，
-- 这样重命名标签只需一条 UPDATE。
CREATE TABLE IF NOT EXISTS file_tags (
    file_id  INTEGER NOT NULL,
    tag_id   INTEGER NOT NULL,
    added_at TEXT,
    source   TEXT,
    PRIMARY KEY (file_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_file_tags_tag  ON file_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_file_tags_file ON file_tags(file_id);

-- 标签操作日志，供 tag undo 回滚
CREATE TABLE IF NOT EXISTS tag_journal (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT,
    action   TEXT,
    file_id  INTEGER,
    tag_key  TEXT,
    tag_name TEXT,
    detail   TEXT
);
"""

# 会被 SQL 直接使用的列名白名单，防止拼接注入
COLUMNS = {
    "id", "root", "rel", "path", "name", "ext", "kind", "size", "mtime",
    "ctime", "duration", "width", "height", "vcodec", "acodec", "bitrate",
    "container", "has_cover", "nb_streams", "quick_hash", "full_hash",
    "probe_state", "probe_error", "missing", "first_seen", "last_seen",
}

VIDEO_FIELDS = ("duration", "width", "height", "vcodec", "acodec", "bitrate",
                "container", "has_cover", "nb_streams", "probe_state",
                "probe_error")


def open_db(path: str) -> sqlite3.Connection:
    util.ensure_dir(os.path.dirname(path))
    conn = sqlite3.connect(util.long_path(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DDL)
    if get_meta(conn, "schema_version") != SCHEMA_VERSION:
        set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()
    return conn


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def load_existing(conn, root: str) -> dict:
    """把某个根目录下已有的记录读进内存：path -> row（dict）。"""
    out = {}
    cur = conn.execute("SELECT * FROM files WHERE root = ?", (root,))
    for row in cur:
        out[row["path"]] = dict(row)
    return out


def insert_file(conn, rec: dict) -> int:
    cols = [c for c in rec if c in COLUMNS]
    placeholders = ", ".join("?" for _ in cols)
    sql = "INSERT INTO files(%s) VALUES(%s)" % (", ".join(cols), placeholders)
    cur = conn.execute(sql, [rec[c] for c in cols])
    return cur.lastrowid


def touch_file(conn, file_id: int, stamp: str):
    """文件没变化，只刷新 last_seen 并清掉 missing 标记。"""
    conn.execute("UPDATE files SET last_seen = ?, missing = 0 WHERE id = ?",
                 (stamp, file_id))


def update_file(conn, file_id: int, rec: dict):
    cols = [c for c in rec if c in COLUMNS]
    assignments = ", ".join("%s = ?" % c for c in cols)
    conn.execute("UPDATE files SET %s WHERE id = ?" % assignments,
                 [rec[c] for c in cols] + [file_id])


def mark_missing(conn, root: str, seen_paths) -> int:
    """把这次没扫到的旧记录标记为 missing=1，返回标记数量。"""
    rows = conn.execute("SELECT id, path FROM files WHERE root = ? AND missing = 0",
                        (root,)).fetchall()
    gone = [(r["id"],) for r in rows if r["path"] not in seen_paths]
    if gone:
        conn.executemany("UPDATE files SET missing = 1 WHERE id = ?", gone)
    return len(gone)


def iter_rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    for row in cur:
        yield dict(row)


def count(conn, where="1=1", params=()) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM files WHERE " + where,
                        params).fetchone()["n"]


def build_filters(args):
    """把 list 子命令的各种过滤条件翻译成 SQL 片段。"""
    where = []
    params = []
    if getattr(args, "kind", None):
        where.append("kind = ?")
        params.append(args.kind)
    if getattr(args, "ext", None):
        exts = []
        for item in args.ext:
            for piece in str(item).split(","):
                piece = piece.strip().lower()
                if not piece:
                    continue
                exts.append(piece if piece.startswith(".") else "." + piece)
        if exts:
            where.append("lower(ext) IN (%s)" % ", ".join("?" for _ in exts))
            params.extend(exts)
    if getattr(args, "name", None):
        where.append("name LIKE ?")
        params.append("%" + args.name + "%")
    if getattr(args, "path_like", None):
        where.append("path LIKE ?")
        params.append("%" + args.path_like + "%")
    if getattr(args, "min_duration", None):
        where.append("duration >= ?")
        params.append(float(util.parse_duration(args.min_duration)))
    if getattr(args, "max_duration", None):
        where.append("duration <= ?")
        params.append(float(util.parse_duration(args.max_duration)))
    if getattr(args, "min_size", None):
        where.append("size >= ?")
        params.append(int(util.parse_size(args.min_size)))
    if getattr(args, "max_size", None):
        where.append("size <= ?")
        params.append(int(util.parse_size(args.max_size)))
    if getattr(args, "year", None):
        where.append("strftime('%Y', mtime, 'unixepoch', 'localtime') = ?")
        params.append(str(args.year))
    if getattr(args, "min_height", None):
        where.append("height >= ?")
        params.append(int(args.min_height))
    if getattr(args, "has_cover", None) is True:
        where.append("has_cover = 1")
    if getattr(args, "has_cover", None) is False:
        where.append("(has_cover IS NULL OR has_cover = 0)")
    if getattr(args, "missing", False):
        where.append("missing = 1")
    elif not getattr(args, "include_missing", False):
        where.append("missing = 0")
    if getattr(args, "probe_failed", False):
        where.append("probe_state = -1")

    # 标签过滤：--tag 之间是 AND（同时具备），--tag-any 之间是 OR（具备任一）
    tag_names = list(getattr(args, "tag", None) or [])
    for name in tag_names:
        where.append("id IN (SELECT ft.file_id FROM file_tags ft "
                     "JOIN tags t ON t.id = ft.tag_id WHERE t.key = ?)")
        params.append(util.tag_key(name))
    tag_any = list(getattr(args, "tag_any", None) or [])
    if tag_any:
        keys = [util.tag_key(n) for n in tag_any]
        where.append("id IN (SELECT ft.file_id FROM file_tags ft "
                     "JOIN tags t ON t.id = ft.tag_id WHERE t.key IN (%s))"
                     % ", ".join("?" for _ in keys))
        params.extend(keys)
    return (" AND ".join(where) if where else "1=1"), params


SORT_WHITELIST = {
    "id": "id", "name": "name", "size": "size", "duration": "duration",
    "mtime": "mtime", "path": "path", "height": "height",
}


def query_files(conn, filters, sort="name", desc=False, limit=None):
    where, params = filters
    order = SORT_WHITELIST.get(sort, "name")
    direction = "DESC" if desc else "ASC"
    sql = "SELECT * FROM files WHERE %s ORDER BY %s %s" % (where, order, direction)
    if limit:
        sql += " LIMIT %d" % int(limit)
    return list(iter_rows(conn, sql, params))


def stats(conn) -> dict:
    """整体统计：数量、体积、时长、分辨率、年份、封面、探测失败等。"""
    out = {"by_kind": [], "by_ext": [], "by_year": [], "by_resolution": [],
           "total": 0, "total_size": 0, "video_count": 0, "video_size": 0,
           "video_duration": 0.0, "no_cover": 0, "probe_failed": 0, "missing": 0}

    out["total"] = count(conn)
    row = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
                       "WHERE missing = 0").fetchone()
    out["total"] = row["n"]
    out["total_size"] = row["s"]

    for r in iter_rows(conn, "SELECT kind, COUNT(*) n, COALESCE(SUM(size),0) s "
                             "FROM files WHERE missing = 0 GROUP BY kind ORDER BY n DESC"):
        out["by_kind"].append((r["kind"] or "?", r["n"], r["s"]))

    row = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(size),0) s, "
                       "COALESCE(SUM(duration),0) d FROM files "
                       "WHERE kind = 'video' AND missing = 0").fetchone()
    out["video_count"], out["video_size"], out["video_duration"] = row["n"], row["s"], row["d"]

    for r in iter_rows(conn, "SELECT COALESCE(ext,'?') e, COUNT(*) n, "
                             "COALESCE(SUM(size),0) s FROM files "
                             "WHERE missing = 0 GROUP BY e ORDER BY n DESC"):
        out["by_ext"].append((r["e"], r["n"], r["s"]))

    for r in iter_rows(conn, "SELECT strftime('%Y', mtime, 'unixepoch', 'localtime') y, "
                             "COUNT(*) n, COALESCE(SUM(size),0) s FROM files "
                             "WHERE kind = 'video' AND missing = 0 "
                             "GROUP BY y ORDER BY y DESC"):
        out["by_year"].append((r["y"] or "未知", r["n"], r["s"]))

    for r in iter_rows(conn, "SELECT width w, height h, COUNT(*) n FROM files "
                             "WHERE kind = 'video' AND missing = 0 AND height IS NOT NULL "
                             "GROUP BY w, h ORDER BY n DESC LIMIT 15"):
        out["by_resolution"].append(("%sx%s" % (r["w"], r["h"]), r["n"]))

    out["no_cover"] = count(conn, "kind = 'video' AND missing = 0 AND "
                                  "(has_cover IS NULL OR has_cover = 0)")
    out["probe_failed"] = count(conn, "probe_state = -1 AND missing = 0")
    out["missing"] = count(conn, "missing = 1")
    return out
