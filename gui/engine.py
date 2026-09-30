# -*- coding: utf-8 -*-
"""界面层专用的查询/操作服务。

刻意不改动 core 里的任何东西：GUI 只是把 core.db / core.scan / core.tags
包一层，提供「表格分页查询」「标签列表」「增量扫描」这几个界面直接要用的入口。
"""

from __future__ import annotations

import os

from core import config as config_mod
from core import db, report, scan, tags as tags_mod, util

# 排序选项：(显示名, 数据库字段)。界面改成缩略图网格后不再有表头，
# 排序改成工具栏下拉，所以这里给一份选项列表。
SORT_OPTIONS = [
    ("名称", "name"),
    ("大小", "size"),
    ("时长", "duration"),
    ("修改日期", "mtime"),
    ("分辨率", "height"),
    ("路径", "path"),
]


class Engine(object):
    """界面与索引库之间的唯一入口。"""

    def __init__(self, config_path="", db_path="", root=None):
        self.cfg = config_mod.load(config_path)
        if db_path:
            self.cfg.rebind_db(db_path)
        if root:
            self.cfg.roots = [config_mod.resolve_path(root)]
        self.conn = db.open_db(self.cfg.db_path)

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ---------------------------------------------------------- 查询

    def build_where(self, keyword=None, tags=None, untagged=False,
                    min_height=None, year=None, has_cover=None,
                    min_duration=None, kind="video"):
        where, params = ["missing = 0"], []
        if kind and kind != "all":
            where.append("kind = ?")
            params.append(kind)
        if keyword:
            where.append("(name LIKE ? OR path LIKE ?)")
            like = "%" + keyword + "%"
            params.extend([like, like])
        if min_height:
            where.append("height >= ?")
            params.append(int(min_height))
        if year:
            where.append("strftime('%Y', mtime, 'unixepoch', 'localtime') = ?")
            params.append(str(year))
        if min_duration:
            where.append("duration >= ?")
            params.append(float(min_duration))
        if has_cover is True:
            where.append("has_cover = 1")
        elif has_cover is False:
            where.append("(has_cover IS NULL OR has_cover = 0)")

        # 标签：多个标签是「同时具备」
        for name in (tags or []):
            where.append("id IN (SELECT ft.file_id FROM file_tags ft "
                         "JOIN tags t ON t.id = ft.tag_id WHERE t.key = ?)")
            params.append(util.tag_key(name))
        if untagged:
            where.append("id NOT IN (SELECT file_id FROM file_tags)")
        return " AND ".join(where), params

    def query(self, limit=200, offset=0, sort="name", desc=False, **filters):
        """返回 (rows, 命中总数)。rows 里已经带上 tags 字段。"""
        where, params = self.build_where(**filters)
        total = db.count(self.conn, where, params)
        rows = db.query_files(self.conn, (where, params), sort=sort, desc=desc,
                              limit=limit + offset)
        rows = rows[offset:offset + limit]
        self.attach_tags(rows)
        return rows, total

    def attach_tags(self, rows):
        mapping = tags_mod.tags_map(self.conn, [r["id"] for r in rows])
        for row in rows:
            row["tags"] = "、".join(mapping.get(row["id"], []))
        return rows

    def get_file(self, file_id):
        found = list(db.iter_rows(self.conn, "SELECT * FROM files WHERE id = ?",
                                  (int(file_id),)))
        if not found:
            return None
        self.attach_tags(found)
        return found[0]

    def tag_names_of(self, file_id):
        return tags_mod.tags_map(self.conn, [int(file_id)]).get(int(file_id), [])

    def stats(self):
        return db.stats(self.conn)

    def tags(self):
        return tags_mod.list_tags(self.conn)

    def years(self):
        rows = db.iter_rows(
            self.conn,
            "SELECT DISTINCT strftime('%Y', mtime, 'unixepoch', 'localtime') AS y "
            "FROM files WHERE kind = 'video' AND missing = 0 ORDER BY y DESC")
        return [r["y"] for r in rows if r["y"]]

    # ---------------------------------------------------------- 标签操作

    def add_tags(self, file_ids, names):
        result = tags_mod.add_tags(self.conn, file_ids, names)
        tags_mod.export_csv(self.conn, self.cfg.tags_backup)
        return result

    def remove_tags(self, file_ids, names):
        result = tags_mod.remove_tags(self.conn, file_ids, names)
        tags_mod.export_csv(self.conn, self.cfg.tags_backup)
        return result

    def delete_tag(self, name):
        info = tags_mod.delete_tag(self.conn, name)
        tags_mod.export_csv(self.conn, self.cfg.tags_backup)
        return info

    def rename_tag(self, old, new):
        info = tags_mod.rename_tag(self.conn, old, new)
        tags_mod.export_csv(self.conn, self.cfg.tags_backup)
        return info

    def all_tag_names(self):
        return [t["name"] for t in self.tags()]

    # ---------------------------------------------------------- 长任务

    def scan(self, progress=None, conn=None):
        """扫描所有配置的根目录 + 探测元数据 + 重接标签。

        conn 允许调用方传入自己的连接 —— 工作线程必须这么做，
        原因见 scan_in_own_connection()。
        """
        conn = conn or self.conn
        if not self.cfg.ffprobe and progress:
            progress("警告：找不到 ffprobe，跳过了时长/分辨率探测")
        summary = {"files": 0, "new": 0, "updated": 0, "unchanged": 0,
                   "missing": 0, "probed": 0, "probe_failed": 0, "relinked": 0}
        for root in self.cfg.roots:
            if not os.path.isdir(root):
                continue
            one = scan.sync_root(conn, root, recurse=self.cfg.recurse,
                                 exclude_dirs=self.cfg.exclude_dirs,
                                 exclude_globs=self.cfg.exclude_globs,
                                 progress=progress)
            for key in ("seen", "new", "updated", "unchanged", "missing"):
                if key == "seen":
                    summary["files"] += one[key]
                else:
                    summary[key] += one[key]

        if self.cfg.ffprobe:
            probed = scan.probe_pending(conn, self.cfg, self.cfg.roots,
                                        progress=progress)
            summary["probed"] = probed["probed"]
            summary["probe_failed"] = probed["failed"]

        relinked = tags_mod.relink(conn, progress)
        summary["relinked"] = relinked["migrated"]
        return summary

    def scan_in_own_connection(self, progress=None):
        """在工作线程里跑扫描 —— **必须新开一个 sqlite 连接**。

        sqlite3 的连接只能在创建它的线程里使用。界面那个连接是主线程建的，
        工作线程一碰它就抛：
            ProgrammingError: SQLite objects created in a thread can only be
            used in that same thread.
        于是扫描按钮长期处于「一按就失败」的状态，失败后还会弹模态错误框，
        看起来就像界面卡死了。
        """
        conn = db.open_db(self.cfg.db_path)
        try:
            return self.scan(progress=progress, conn=conn)
        finally:
            conn.close()


def ffmpeg_path():
    """给缩略图用的 ffmpeg 路径（复用 core 的自动查找逻辑）。"""
    return config_mod.load().ffmpeg
