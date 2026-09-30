# -*- coding: utf-8 -*-
"""标签系统。

设计要点
--------
1. 规范化存储：`tags.key` 是唯一键（去空白 + 折叠大小写），`tags.name` 是显示名。
   所以 "1080P" 和 "1080p" 是同一个标签。
2. 多对多关系表 `file_tags`，重命名标签只需一条 UPDATE，不用重写每一条记录。
3. 所有写操作都会：记一条 `tag_journal`（供 undo） + 重新导出 CSV 备份。
   标签是磁盘上推导不出来的手工数据，索引库可以删、标签不能丢。
4. `relink`：文件被改名/移动后，旧行标记 missing、新行是同 size + 同 mtime，
   用它把标签接到新行上，避免「一整理标签全丢」。

本模块只写索引库，不碰视频文件本身。
"""

from __future__ import annotations

import csv
import json
import os

from . import util

BACKUP_HEADERS = ["完整路径", "文件名", "标签", "打标签时间"]


# ------------------------------------------------------------------ 基础查询

def get_tag(conn, name):
    return conn.execute("SELECT * FROM tags WHERE key = ?",
                        (util.tag_key(name),)).fetchone()


def get_or_create_tag(conn, name):
    """返回 (tag_id, 是否新建)。"""
    key = util.tag_key(name)
    if not key:
        return None, False
    row = conn.execute("SELECT id FROM tags WHERE key = ?", (key,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO tags(key, name, created_at) VALUES(?, ?, ?)",
        (key, util.normalize_tag(name), util.now_stamp()))
    return cur.lastrowid, True


def list_tags(conn):
    """所有标签 + 每个标签的文件数、占用空间、总时长。"""
    sql = ("SELECT t.id, t.name, t.key, t.created_at, "
           "COUNT(f.id) AS n, COALESCE(SUM(f.size), 0) AS size, "
           "COALESCE(SUM(f.duration), 0) AS duration "
           "FROM tags t "
           "LEFT JOIN file_tags ft ON ft.tag_id = t.id "
           "LEFT JOIN files f ON f.id = ft.file_id AND f.missing = 0 "
           "GROUP BY t.id ORDER BY n DESC, t.name ASC")
    return [dict(r) for r in conn.execute(sql)]


def tags_map(conn, file_ids=None):
    """{file_id: [标签名...]}，用于在列表/报表里显示标签列。"""
    sql = ("SELECT ft.file_id, t.name FROM file_tags ft "
           "JOIN tags t ON t.id = ft.tag_id")
    params = []
    if file_ids is not None:
        ids = list(file_ids)
        if not ids:
            return {}
        placeholders = []
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders.extend("?" for _ in batch)
            params.extend(batch)
        sql += " WHERE ft.file_id IN (%s)" % ", ".join(placeholders)
    out = {}
    for row in conn.execute(sql, params):
        out.setdefault(row["file_id"], []).append(row["name"])
    for names in out.values():
        names.sort(key=util.natural_key)
    return out


def files_of_tags(conn, names, mode="and"):
    """按标签取文件 id 列表。mode: and（同时具备）/ or（具备任一）。"""
    keys = [util.tag_key(n) for n in names if util.tag_key(n)]
    if not keys:
        return []
    if mode == "or":
        sql = ("SELECT DISTINCT file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
               "WHERE t.key IN (%s)" % ", ".join("?" for _ in keys))
        return [r["file_id"] for r in conn.execute(sql, keys)]
    out = None
    for key in keys:
        rows = {r["file_id"] for r in conn.execute(
            "SELECT ft.file_id FROM file_tags ft JOIN tags t ON t.id = ft.tag_id "
            "WHERE t.key = ?", (key,))}
        out = rows if out is None else (out & rows)
        if not out:
            break
    return sorted(out or [])


def journal_rows(conn, limit=20):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM tag_journal ORDER BY id DESC LIMIT ?", (int(limit),))]


def _journal(conn, action, file_id=None, tag_key=None, tag_name=None, detail=None):
    conn.execute(
        "INSERT INTO tag_journal(ts, action, file_id, tag_key, tag_name, detail) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (util.now_stamp(), action, file_id, tag_key, tag_name,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None))


# ------------------------------------------------------------------ 写操作

def add_tags(conn, file_ids, names, source="manual"):
    """给一批文件打一批标签。返回统计。"""
    stamp = util.now_stamp()
    file_ids = list(dict.fromkeys(int(i) for i in file_ids))
    clean = [util.normalize_tag(n) for n in names if util.tag_key(n)]
    result = {"files": len(file_ids), "tags": clean, "added": 0,
              "existing": 0, "new_tags": []}
    if not file_ids or not clean:
        return result

    tag_ids = {}
    for name in clean:
        tag_id, created = get_or_create_tag(conn, name)
        tag_ids[tag_id] = name
        if created:
            result["new_tags"].append(name)

    for file_id in file_ids:
        for tag_id, name in tag_ids.items():
            exists = conn.execute(
                "SELECT 1 FROM file_tags WHERE file_id = ? AND tag_id = ?",
                (file_id, tag_id)).fetchone()
            if exists:
                result["existing"] += 1
                continue
            conn.execute(
                "INSERT INTO file_tags(file_id, tag_id, added_at, source) "
                "VALUES(?, ?, ?, ?)", (file_id, tag_id, stamp, source))
            _journal(conn, "add", file_id, util.tag_key(name), name)
            result["added"] += 1
    conn.commit()
    return result


def remove_tags(conn, file_ids, names):
    """移除一批文件上的若干标签（标签本身保留）。"""
    clean = [util.normalize_tag(n) for n in names if util.tag_key(n)]
    file_ids = [int(i) for i in file_ids]
    result = {"files": len(file_ids), "tags": clean, "removed": 0}
    for name in clean:
        tag = get_tag(conn, name)
        if not tag:
            continue
        for file_id in file_ids:
            cur = conn.execute(
                "DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?",
                (file_id, tag["id"]))
            if cur.rowcount:
                result["removed"] += 1
                _journal(conn, "remove", file_id, tag["key"], tag["name"])
    conn.commit()
    return result


def delete_tag(conn, name):
    """彻底删除一个标签（连同它在所有文件上的关联）。"""
    tag = get_tag(conn, name)
    if not tag:
        return None
    file_ids = [r["file_id"] for r in conn.execute(
        "SELECT file_id FROM file_tags WHERE tag_id = ?", (tag["id"],))]
    conn.execute("DELETE FROM file_tags WHERE tag_id = ?", (tag["id"],))
    conn.execute("DELETE FROM tags WHERE id = ?", (tag["id"],))
    _journal(conn, "delete_tag", None, tag["key"], tag["name"],
             {"file_ids": file_ids})
    conn.commit()
    return {"name": tag["name"], "unlinked": len(file_ids)}


def rename_tag(conn, old, new):
    """标签改名；若新名已存在则合并过去。"""
    src = get_tag(conn, old)
    dst = get_tag(conn, new)
    if not src:
        return None
    if dst and dst["id"] == src["id"]:
        return {"name": src["name"], "merged": False, "moved": 0}

    moved = 0
    if dst:                                   # 合并进已有标签
        rows = conn.execute("SELECT file_id FROM file_tags WHERE tag_id = ?",
                            (src["id"],)).fetchall()
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM file_tags WHERE file_id = ? AND tag_id = ?",
                (row["file_id"], dst["id"])).fetchone()
            if exists:
                conn.execute("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?",
                             (row["file_id"], src["id"]))
            else:
                conn.execute("UPDATE file_tags SET tag_id = ? WHERE file_id = ? AND tag_id = ?",
                             (dst["id"], row["file_id"], src["id"]))
                moved += 1
        conn.execute("DELETE FROM tags WHERE id = ?", (src["id"],))
    else:                                     # 直接改显示名
        conn.execute("UPDATE tags SET key = ?, name = ? WHERE id = ?",
                     (util.tag_key(new), util.normalize_tag(new), src["id"]))

    _journal(conn, "rename_tag", None, src["key"], src["name"],
             {"old": src["name"], "new": util.normalize_tag(new),
              "merged_into": (dst["name"] if dst else None)})
    conn.commit()
    return {"name": src["name"], "new": util.normalize_tag(new),
            "merged": bool(dst), "moved": moved}


# ------------------------------------------------------------------ 备份 / 恢复

def export_csv(conn, path):
    """把「文件 → 标签」导出成 CSV（带 BOM，Excel 直接打开不乱码）。"""
    sql = ("SELECT f.path, f.name, t.name AS tag, ft.added_at "
           "FROM file_tags ft "
           "JOIN files f ON f.id = ft.file_id "
           "JOIN tags t ON t.id = ft.tag_id "
           "ORDER BY f.path, t.name")
    rows = [[r["path"], r["name"], r["tag"], r["added_at"] or ""]
            for r in conn.execute(sql)]
    util.ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(util.long_path(path), "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(BACKUP_HEADERS)
        writer.writerows(rows)
    return {"path": path, "rows": len(rows)}


def import_csv(conn, path, mode="merge"):
    """从备份 CSV 恢复标签。

    mode=merge：只增不减（默认，安全）
    mode=replace：先清空所有标签关联，再按 CSV 重建
    """
    if not os.path.isfile(path):
        raise FileNotFoundError("找不到备份文件：%s" % path)

    records = []
    with open(util.long_path(path), "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            return {"rows": 0, "files": 0, "added": 0, "missing_paths": 0}
        # 兼容「完整路径,文件名,标签,时间」和更简单的「路径,标签」两种表头
        path_idx, tag_idx = 0, 2 if len(header) >= 3 else 1
        for row in reader:
            if len(row) <= max(path_idx, tag_idx):
                continue
            p = (row[path_idx] or "").strip()
            tag = util.normalize_tag(row[tag_idx])
            if p and tag:
                records.append((p, tag))

    if mode == "replace":
        conn.execute("DELETE FROM file_tags")
        conn.commit()

    stamp = util.now_stamp()
    added, missing, files = 0, 0, set()
    for p, tag in records:
        row = conn.execute("SELECT id FROM files WHERE path = ?", (p,)).fetchone()
        if not row:
            missing += 1
            continue
        tag_id, _ = get_or_create_tag(conn, tag)
        exists = conn.execute("SELECT 1 FROM file_tags WHERE file_id = ? AND tag_id = ?",
                              (row["id"], tag_id)).fetchone()
        if exists:
            continue
        conn.execute("INSERT INTO file_tags(file_id, tag_id, added_at, source) "
                     "VALUES(?, ?, ?, ?)", (row["id"], tag_id, stamp, "import"))
        files.add(row["id"])
        added += 1
    _journal(conn, "import", None, None, None,
             {"path": path, "mode": mode, "added": added, "missing_paths": missing})
    conn.commit()
    return {"rows": len(records), "files": len(files), "added": added,
            "missing_paths": missing}


# ------------------------------------------------------------------ 回滚

def undo(conn, steps=1) -> list:
    """按日志逆序回滚最近 N 步标签操作。"""
    entries = conn.execute(
        "SELECT * FROM tag_journal ORDER BY id DESC LIMIT ?",
        (int(steps),)).fetchall()
    done = []
    for entry in entries:
        action = entry["action"]
        detail = json.loads(entry["detail"]) if entry["detail"] else {}
        if action == "add":
            conn.execute("DELETE FROM file_tags WHERE file_id = ? AND tag_id = "
                         "(SELECT id FROM tags WHERE key = ?)",
                         (entry["file_id"], entry["tag_key"]))
            done.append("撤销打标签：%s ← %s" % (entry["tag_name"], entry["file_id"]))
        elif action == "remove":
            tag_id, _ = get_or_create_tag(conn, entry["tag_name"])
            conn.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, added_at, source) "
                         "VALUES(?, ?, ?, ?)",
                         (entry["file_id"], tag_id, util.now_stamp(), "undo"))
            done.append("撤销移除标签：%s ← %s" % (entry["tag_name"], entry["file_id"]))
        elif action == "delete_tag":
            tag_id, _ = get_or_create_tag(conn, entry["tag_name"])
            for file_id in detail.get("file_ids", []):
                conn.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, added_at, source) "
                             "VALUES(?, ?, ?, ?)",
                             (file_id, tag_id, util.now_stamp(), "undo"))
            done.append("恢复标签：%s（%d 个文件）"
                        % (entry["tag_name"], len(detail.get("file_ids", []))))
        elif action == "rename_tag":
            old_name = detail.get("old") or entry["tag_name"]
            new_name = detail.get("new")
            if detail.get("merged_into"):
                done.append("合并操作无法自动拆分：%s → %s（请手工处理）"
                            % (old_name, detail["merged_into"]))
            else:
                conn.execute("UPDATE tags SET key = ?, name = ? WHERE key = ?",
                             (util.tag_key(old_name), old_name, util.tag_key(new_name)))
                done.append("撤销改名：%s → %s" % (new_name, old_name))
        elif action == "import":
            done.append("导入操作不做自动回滚（CSV=%s），需要时用 --mode replace 重新导入"
                        % detail.get("path"))
        elif action == "relink":
            done.append("relink 只是把标签接到新路径，不做自动回滚")
        else:
            done.append("未知操作，跳过：%s" % action)
        conn.execute("DELETE FROM tag_journal WHERE id = ?", (entry["id"],))
    conn.commit()
    return done


# ------------------------------------------------------------------ 重接

def relink(conn, progress=None) -> dict:
    """把「已消失文件」上的标签，接回同 size + 同 mtime 的新文件上。

    文件被改名或在同盘移动后，扫描会得到：旧行 missing=1、新行是另一条记录。
    标签挂在旧行上，靠这一步迁移过去。
    """
    orphans = [dict(r) for r in conn.execute(
        "SELECT f.id, f.size, f.mtime FROM files f "
        "JOIN file_tags ft ON ft.file_id = f.id "
        "WHERE f.missing = 1 GROUP BY f.id")]
    if not orphans:
        return {"orphans": 0, "migrated": 0, "tags_moved": 0, "unmatched": 0}

    if progress:
        progress("发现 %d 个带标签的文件已消失，尝试用 大小+时间 重新配对…"
                 % len(orphans))

    # 现状文件建两级索引：先按精确 mtime（同盘改名/移动会完全保留），
    # 再退到整秒（跨盘拷贝可能丢亚秒精度）。
    exact, loose = {}, {}
    for row in conn.execute("SELECT id, size, mtime FROM files WHERE missing = 0"):
        mtime = float(row["mtime"] or 0)
        exact.setdefault((row["size"], round(mtime, 3)), []).append(row["id"])
        loose.setdefault((row["size"], int(mtime)), []).append(row["id"])

    migrated = moved = unmatched = 0
    for orphan in orphans:
        size = orphan["size"]
        mtime = float(orphan["mtime"] or 0)
        candidates = [i for i in exact.get((size, round(mtime, 3)), [])
                      if i != orphan["id"]]
        if len(candidates) != 1:
            candidates = [i for i in loose.get((size, int(mtime)), [])
                          if i != orphan["id"]]
        if len(candidates) != 1:
            # 找不到唯一对应，或者有歧义，就不敢乱接
            unmatched += 1
            continue
        new_id = candidates[0]
        tags = [dict(r) for r in conn.execute(
            "SELECT t.id, t.key, t.name FROM tags t JOIN file_tags ft ON ft.tag_id = t.id "
            "WHERE ft.file_id = ?", (orphan["id"],))]
        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id, added_at, source) "
                         "VALUES(?, ?, ?, ?)",
                         (new_id, tag["id"], util.now_stamp(), "relink"))
            conn.execute("DELETE FROM file_tags WHERE file_id = ? AND tag_id = ?",
                         (orphan["id"], tag["id"]))
            moved += 1
        _journal(conn, "relink", new_id, None, None,
                 {"old_file_id": orphan["id"], "tags": [t["name"] for t in tags]})
        migrated += 1
    conn.commit()
    return {"orphans": len(orphans), "migrated": migrated,
            "tags_moved": moved, "unmatched": unmatched}
