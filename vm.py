# -*- coding: utf-8 -*-
"""vm —— 视频库管理工具（索引 / 报表 / 查重 / 标签）

用法示例：
    py vm.py roots                                 查看当前配置
    py vm.py scan                                  扫描入库（增量、带元数据探测）
    py vm.py stats                                 看整体统计（含标签概况）
    py vm.py list --min-duration 20m --sort size   列出 20 分钟以上的视频
    py vm.py dupes --min-size 1MB                  查重（只报告，不动文件）
    py vm.py report --out var/report/index.html    生成 HTML 索引页

    # 标签
    py vm.py tag add 胡桃 --name 胡桃                按文件名批量打标签
    py vm.py tag add 收藏 --id 3 --id 7             按索引 ID 打标签
    py vm.py tag list                              列出所有标签
    py vm.py tag show 胡桃                          按标签查全部视频
    py vm.py list --tag 胡桃 --tag 1080p            多标签「同时具备」
    py vm.py tag export / import / undo / relink   备份、恢复、回滚、重接

    # 文件名精简（唯一会改动视频文件的功能）
    py vm.py rename                                试运行，只看会怎么改
    py vm.py rename --max-length 30 --apply        确认后执行
    py vm.py rename --undo                         原样还原
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config as config_mod          # noqa: E402
from core import db, dupes as dupes_mod, rename as rename_mod, report, scan   # noqa: E402
from core import tags as tags_mod, util        # noqa: E402

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


# ------------------------------------------------------------------ 输出小工具

class Console(object):
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.isatty = False
        try:
            self.isatty = bool(sys.stdout.isatty())
        except Exception:
            self.isatty = False
        self._last_progress = 0.0

    def log(self, message=""):
        if not self.quiet:
            sys.stdout.write(str(message) + "\n")
            sys.stdout.flush()

    def progress(self, message):
        """终端里原地刷新；重定向到文件时按时间节流，避免刷屏。"""
        if self.quiet:
            return
        text = util.truncate(str(message), 100)
        if self.isatty:
            sys.stdout.write("\r" + text + " " * max(0, 100 - util.disp_width(text)))
            sys.stdout.flush()
            return
        import time
        now = time.time()
        if now - self._last_progress >= 3.0:
            self._last_progress = now
            sys.stdout.write(text + "\n")
            sys.stdout.flush()

    def progress_done(self):
        if not self.quiet and self.isatty:
            sys.stdout.write("\r" + " " * 102 + "\r")
            sys.stdout.flush()


def dump_json(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


# ------------------------------------------------------------------ 各子命令

def cmd_roots(args, cfg, console):
    info = {
        "配置文件": cfg.source,
        "数据库": cfg.db_path,
        "根目录": cfg.roots,
        "递归": cfg.recurse,
        "ffprobe": cfg.ffprobe or "（未找到）",
        "ffmpeg": cfg.ffmpeg or "（未找到）",
        "哈希算法": cfg.hash_algo,
        "排除目录": sorted(cfg.exclude_dirs),
    }
    if args.json:
        dump_json(info)
        return EXIT_OK
    for key, value in info.items():
        if isinstance(value, list):
            console.log("%-10s: %s" % (key, ", ".join(value) if value else "（空）"))
        else:
            console.log("%-10s: %s" % (key, value))
    for root in cfg.roots:
        console.log("  %s 目录存在: %s" % (root, "是" if os.path.isdir(root) else "否 ← 注意"))
    return EXIT_OK


def cmd_scan(args, cfg, console):
    roots = cfg.roots
    if not roots:
        console.log("没有配置任何根目录，请用 --root 指定，或编辑 vm_config.toml。")
        return EXIT_USAGE
    conn = db.open_db(cfg.db_path)
    total = {"seen": 0, "new": 0, "updated": 0, "unchanged": 0, "missing": 0,
             "errors": 0}
    try:
        for root in roots:
            if not os.path.isdir(root):
                console.log("跳过不存在的目录：%s" % root)
                continue
            console.log("扫描：%s" % root)
            summary = scan.sync_root(
                conn, root,
                recurse=(False if args.no_recurse else cfg.recurse),
                exclude_dirs=cfg.exclude_dirs,
                exclude_globs=cfg.exclude_globs,
                progress=console.progress,
                limit=args.limit,
            )
            console.progress_done()
            console.log("  扫到 %d 个文件：新增 %d，有变化 %d，未变 %d，已消失 %d，读取失败 %d"
                        % (summary["seen"], summary["new"], summary["updated"],
                           summary["unchanged"], summary["missing"], summary["errors"]))
            kinds = "，".join("%s %d" % (k, v)
                             for k, v in sorted(summary["by_kind"].items()))
            console.log("  分类：%s" % (kinds or "无"))
            for key in total:
                total[key] += summary.get(key, 0)

        if args.no_probe:
            console.log("已按 --no-probe 跳过元数据探测。")
        else:
            if not cfg.ffprobe:
                console.log("警告：找不到 ffprobe，跳过元数据探测（仍可用 list/dupes）。")
            else:
                console.log("探测元数据（时长/分辨率/编码/封面）…")
                result = scan.probe_pending(conn, cfg, roots,
                                            force=args.force_probe,
                                            limit=args.probe_limit,
                                            progress=console.progress)
                console.progress_done()
                console.log("  探测 %d/%d，失败 %d"
                            % (result["probed"], result["total"], result["failed"]))

        # 文件若被改名/移动，把标签接回新路径
        relinked = tags_mod.relink(conn, console.progress)
        console.progress_done()
        if relinked["orphans"]:
            console.log("  标签重接：%d 个文件已消失，成功迁移 %d 个（%d 个标签），"
                        "无法唯一配对 %d 个"
                        % (relinked["orphans"], relinked["migrated"],
                           relinked["tags_moved"], relinked["unmatched"]))
            if relinked["migrated"]:
                save_backup(conn, cfg, console)

        counts = db.stats(conn)
        console.log("-" * 60)
        console.log("入库合计：视频 %d 个 / %s，图片 %d 个，全部 %d 个 / %s"
                    % (counts["video_count"], util.human_size(counts["video_size"]),
                       dict((k, n) for k, n, _ in counts["by_kind"]).get("image", 0),
                       counts["total"], util.human_size(counts["total_size"])))
        return EXIT_OK
    finally:
        conn.close()


def cmd_stats(args, cfg, console):
    conn = db.open_db(cfg.db_path)
    try:
        st = db.stats(conn)
        st["tags"] = tags_mod.list_tags(conn)
        if args.json:
            dump_json(st)
        else:
            console.log(report.format_stats(st))
            if st["tags"]:
                console.log("")
                console.log("标签（%d 个）" % len(st["tags"]))
                for tag in st["tags"]:
                    console.log("  %-14s %4d 个文件  %s"
                                % (tag["name"], tag["n"], util.human_size(tag["size"])))
        return EXIT_OK
    finally:
        conn.close()


def attach_tags(conn, rows):
    """把标签拼进每条记录，供列表和报表显示。"""
    mapping = tags_mod.tags_map(conn, [r["id"] for r in rows])
    for row in rows:
        row["tags"] = "、".join(mapping.get(row["id"], []))
    return rows


def cmd_list(args, cfg, console):
    conn = db.open_db(cfg.db_path)
    try:
        args.has_cover = {"yes": True, "no": False, "any": None}.get(args.cover)
        filters = db.build_filters(args)
        limit = None if args.all else args.limit
        rows = db.query_files(conn, filters, sort=args.sort, desc=args.desc, limit=limit)
        attach_tags(conn, rows)

        if args.csv:
            path = report.export_index_csv(rows, args.csv)
            console.log("已导出 %d 条到 %s" % (len(rows), path))
            return EXIT_OK
        if args.json:
            dump_json(rows)
            return EXIT_OK

        if not rows:
            console.log("没有符合条件的记录。先跑一次 scan？")
            return EXIT_OK

        table_rows = []
        for i, row in enumerate(rows, 1):
            res = ("%sx%s" % (row["width"], row["height"])
                   if row["width"] and row["height"] else "-")
            table_rows.append([
                row["id"],
                util.truncate(row["name"], 46),
                util.truncate(row.get("tags") or "", 18),
                util.human_size(row["size"]),
                util.human_duration(row["duration"]),
                res,
                row["vcodec"] or "-",
                "有" if row["has_cover"] else ("无" if row["probe_state"] == 1 else "-"),
                report.mtime_text(row["mtime"])[:10],
            ])
        console.log(util.render_table(
            ["ID", "文件名", "标签", "大小", "时长", "分辨率", "编码", "封面", "日期"],
            table_rows))
        total = db.count(conn, filters[0], filters[1])
        console.log("-" * 60)
        console.log("共命中 %d 条%s" % (total, "" if args.all or total <= len(rows)
                                     else "，这里只显示 %d 条（--all 看全部）" % len(rows)))
        return EXIT_OK
    finally:
        conn.close()


def cmd_dupes(args, cfg, console):
    conn = db.open_db(cfg.db_path)
    try:
        result = dupes_mod.find_groups(
            conn, kind=args.kind,
            min_size=util.parse_size(args.min_size),
            use_quick=not args.no_quick,
            use_full=not args.no_full,
            workers=args.workers or cfg.hash_workers,
            algo=cfg.hash_algo,
            chunk_size=cfg.hash_chunk,
            progress=console.progress,
            max_groups=args.limit_groups,
        )
        console.progress_done()
        groups, st = result["groups"], result["stats"]

        if args.csv:
            path = report.export_dupes_csv(groups, args.csv)
            console.log("已导出重复清单到 %s" % path)
        if args.json:
            dump_json(result)
            return EXIT_OK

        console.log("查重范围：kind=%s，最小 %s；第 1 级同尺寸 %d 组，"
                    "第 2 级同哈希 %d 组，最终确认 %d 组"
                    % (args.kind, util.human_size(util.parse_size(args.min_size)),
                       st["size_groups"], st["quick_groups"], st["groups"]))
        if st["hash_errors"]:
            console.log("有 %d 个文件哈希失败（可能被占用或损坏），未计入重复。"
                        % st["hash_errors"])
        if not groups:
            console.log("没有发现重复文件。")
            return EXIT_OK

        for gi, group in enumerate(groups, 1):
            console.log("")
            console.log("第 %d 组：%d 个文件，每个 %s，可回收 %s"
                        % (gi, group["count"], util.human_size(group["size"]),
                           util.human_size(group["wasted"])))
            for row in group["files"]:
                mark = "★保留" if row["id"] == group["keeper_id"] else "  重复"
                console.log("  %s  %s" % (mark, util.truncate(row["path"], 96)))

        console.log("")
        console.log("-" * 60)
        console.log("合计：%d 组重复，多余 %d 个文件，可回收 %s"
                    % (st["groups"], st["files"], util.human_size(st["wasted"])))
        console.log("注意：本命令只做判定和报告，没有删除或移动任何文件。")
        return EXIT_OK
    finally:
        conn.close()


def cmd_report(args, cfg, console):
    conn = db.open_db(cfg.db_path)
    try:
        args.has_cover = None
        args.cover = "any"
        filters = db.build_filters(args)
        rows = db.query_files(conn, filters, sort=args.sort, desc=args.desc,
                              limit=None if args.all else args.limit)
        attach_tags(conn, rows)
        out = args.out or os.path.join(cfg.report_dir, "index.html")
        st = db.stats(conn)
        path = report.render_html(rows, out, title=args.title, stats=st,
                                  source="、".join(cfg.roots))
        console.log("已生成 HTML 索引：%s（%d 条）" % (path, len(rows)))
        if args.csv:
            csv_path = report.export_index_csv(rows, args.csv)
            console.log("已导出 CSV：%s" % csv_path)
        return EXIT_OK
    finally:
        conn.close()


# ------------------------------------------------------------------ 改名

def cmd_rename(args, cfg, console):
    journal = args.journal or os.path.join(os.path.dirname(cfg.db_path),
                                           "rename_journal.csv")
    conn = db.open_db(cfg.db_path)
    try:
        if args.undo:
            info = rename_mod.undo(conn, journal, steps=args.steps,
                                   progress=console.progress)
            console.progress_done()
            if not info["restored"]:
                console.log("没有可还原的改名记录（日志：%s）。" % journal)
                return EXIT_OK
            console.log("已还原 %d 个文件名。" % info["restored"])
            for path, reason in info["failed"]:
                console.log("  跳过：%s（%s）" % (util.truncate(path, 80), reason))
            if info["remaining"]:
                console.log("  日志里还剩 %d 条记录。" % info["remaining"])
            return EXIT_OK

        plans = []
        seen = set()
        roots = cfg.roots or [None]
        for root in roots:
            for plan in rename_mod.plan(conn, max_len=args.max_length,
                                        min_len=args.min_length, root=root,
                                        limit=args.limit,
                                        exclude_globs=args.exclude):
                if plan["id"] not in seen:
                    seen.add(plan["id"])
                    plans.append(plan)

        if not plans:
            console.log("没有名字超过 %d 个字符的文件，无需精简。" % args.max_length)
            return EXIT_OK

        if args.csv:
            report.write_csv(args.csv, ["原名", "新名", "原长度", "新长度", "原路径",
                                        "新路径"],
                             [[p["old_name"], p["new_name"], p["old_len"],
                               p["new_len"], p["old_path"], p["new_path"]]
                              for p in plans])
            console.log("改名计划已导出：%s" % args.csv)

        if args.json:
            dump_json(plans)
            return EXIT_OK

        rows = []
        for plan in plans:
            rows.append([plan["id"], plan["old_len"], plan["new_len"],
                         util.truncate(plan["old_name"], 52),
                         util.truncate(plan["new_name"], 34)])
        console.log(util.render_table(["ID", "原长", "新长", "原名", "精简后"], rows))
        truncated = sum(1 for p in plans if p["new_len"] >= args.max_length)
        console.log("-" * 70)
        console.log("共 %d 个文件需要精简（名字超过 %d 个字符）；"
                    "其中 %d 个只靠去重/去营销词就够了，%d 个仍需截断到 %d 字符。"
                    % (len(plans), args.max_length,
                       len(plans) - truncated, truncated, args.max_length))

        if not args.apply:
            console.log("")
            console.log("以上是试运行，没有改动任何文件。确认无误后加 --apply 执行：")
            console.log("  py vm.py rename --max-length %d --apply" % args.max_length)
            return EXIT_OK

        info = rename_mod.apply(conn, plans, journal, progress=console.progress)
        console.progress_done()
        console.log("")
        console.log("已精简 %d/%d 个文件名。" % (info["renamed"], info["total"]))
        console.log("操作日志：%s" % info["journal"])
        console.log("不满意可以原样还原：py vm.py rename --undo")
        for path, reason in info["failed"]:
            console.log("  失败：%s（%s）" % (util.truncate(path, 80), reason))
        return EXIT_OK
    finally:
        conn.close()


# ------------------------------------------------------------------ 标签命令

SELECTOR_FILTERS = ("kind", "ext", "name", "path_like", "min_duration",
                    "max_duration", "min_size", "max_size", "year", "min_height")


def add_selector_args(parser):
    """给 tag 命令加上「选中哪些文件」的参数。"""
    parser.add_argument("--id", action="append", type=int, default=[],
                        help="按文件 ID 选中（vm list 输出的第一列），可重复")
    parser.add_argument("--path", action="append", default=[],
                        help="按完整路径选中，可重复")
    parser.add_argument("--name", default=None, help="文件名包含的关键词（批量选中）")
    parser.add_argument("--kind", choices=["video", "image", "audio", "other"],
                        default=None)
    parser.add_argument("--ext", action="append", default=[], help="扩展名，可重复")
    parser.add_argument("--path-like", dest="path_like", default=None,
                        help="路径包含的关键词")
    parser.add_argument("--min-duration", dest="min_duration", default=None)
    parser.add_argument("--max-duration", dest="max_duration", default=None)
    parser.add_argument("--min-size", dest="min_size", default=None)
    parser.add_argument("--max-size", dest="max_size", default=None)
    parser.add_argument("--year", default=None)
    parser.add_argument("--min-height", dest="min_height", type=int, default=None)
    parser.add_argument("--all", action="store_true",
                        help="对索引里所有文件生效（可回滚，但请配合 --dry-run 先看）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只列出将受影响的文件，不写入")


def resolve_targets(conn, args):
    """把各种选择条件解析成具体的文件行。返回 (rows, 没匹配上的选择器)。"""
    rows, seen, unmatched = [], set(), []

    def push(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            rows.append(row)

    for file_id in getattr(args, "id", None) or []:
        found = list(db.iter_rows(conn, "SELECT * FROM files WHERE id = ?", (int(file_id),)))
        if found:
            push(found[0])
        else:
            unmatched.append("id=%s" % file_id)

    for raw in getattr(args, "path", None) or []:
        norm = os.path.normpath(raw)
        found = list(db.iter_rows(conn, "SELECT * FROM files WHERE path = ?", (norm,)))
        if found:
            push(found[0])
        else:
            unmatched.append("path=%s" % raw)

    if any(getattr(args, key, None) for key in SELECTOR_FILTERS):
        args.has_cover = None
        args.cover = "any"
        where, params = db.build_filters(args)
        for row in db.query_files(conn, (where, params), sort="name", limit=None):
            push(row)

    if getattr(args, "all", False):
        for row in db.query_files(conn, ("1=1", []), sort="name", limit=None):
            push(row)

    return rows, unmatched


def save_backup(conn, cfg, console=None):
    """标签是手工数据，每次写完都重新导出备份。"""
    info = tags_mod.export_csv(conn, cfg.tags_backup)
    if console:
        console.log("  已备份 %d 条「文件-标签」关系到 %s"
                    % (info["rows"], info["path"]))
    return info


def _print_targets(console, rows, unmatched):
    if unmatched:
        console.log("以下选择条件在索引里没找到对应文件：%s" % "，".join(unmatched[:5]))
    if rows:
        console.log("命中 %d 个文件：" % len(rows))
        for row in rows[:10]:
            console.log("  [%d] %s" % (row["id"], util.truncate(row["path"], 88)))
        if len(rows) > 10:
            console.log("  … 另有 %d 个" % (len(rows) - 10))


def cmd_tag(args, cfg, console):
    action = getattr(args, "tag_cmd", None)
    if not action:
        console.log("请指定 tag 的子命令：add / rm / del / list / show / rename / "
                    "export / import / undo / relink / journal")
        return EXIT_USAGE

    conn = db.open_db(cfg.db_path)
    try:
        handler = {
            "add": _tag_add, "rm": _tag_remove, "del": _tag_delete,
            "list": _tag_list, "show": _tag_show, "rename": _tag_rename,
            "export": _tag_export, "import": _tag_import,
            "undo": _tag_undo, "relink": _tag_relink, "journal": _tag_journal,
        }[action]
        return handler(conn, args, cfg, console)
    finally:
        conn.close()


def _tag_add(conn, args, cfg, console):
    rows, unmatched = resolve_targets(conn, args)
    _print_targets(console, rows, unmatched)
    if not rows:
        console.log("没有选中任何文件。用 --id / --path / --name / --kind 等指定目标，"
                    "或加 --all。")
        return EXIT_USAGE
    if args.dry_run:
        console.log("（试运行，未写入）将给以上 %d 个文件打标签：%s"
                    % (len(rows), "、".join(args.names)))
        return EXIT_OK

    result = tags_mod.add_tags(conn, [r["id"] for r in rows], args.names)
    console.log("已给 %d 个文件打标签：%s"
                % (result["files"], "、".join(result["tags"])))
    console.log("  新增关联 %d 条，本来就有 %d 条" % (result["added"], result["existing"]))
    if result["new_tags"]:
        console.log("  新创建的标签：%s" % "、".join(result["new_tags"]))
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_remove(conn, args, cfg, console):
    rows, unmatched = resolve_targets(conn, args)
    _print_targets(console, rows, unmatched)
    if not rows:
        console.log("没有选中任何文件。")
        return EXIT_USAGE
    if args.dry_run:
        console.log("（试运行，未写入）将从以上 %d 个文件移除标签：%s"
                    % (len(rows), "、".join(args.names)))
        return EXIT_OK
    result = tags_mod.remove_tags(conn, [r["id"] for r in rows], args.names)
    console.log("已从 %d 个文件上移除标签，共删除 %d 条关联"
                % (result["files"], result["removed"]))
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_delete(conn, args, cfg, console):
    for name in args.names:
        info = tags_mod.delete_tag(conn, name)
        if not info:
            console.log("标签不存在：%s" % name)
        else:
            console.log("已删除标签「%s」，解除 %d 个文件的关联（标签本身不再存在）"
                        % (info["name"], info["unlinked"]))
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_list(conn, args, cfg, console):
    tags = tags_mod.list_tags(conn)
    if args.json:
        dump_json(tags)
        return EXIT_OK
    if not tags:
        console.log("还没有任何标签。用 vm tag add <标签> --id N 开始吧。")
        return EXIT_OK
    table = [[t["name"], t["n"], util.human_size(t["size"]),
              util.human_duration(t["duration"]), (t["created_at"] or "")[:10]]
             for t in tags]
    console.log(util.render_table(["标签", "文件数", "占用空间", "总时长", "创建日期"], table))
    console.log("-" * 60)
    console.log("共 %d 个标签，覆盖 %d 个文件"
                % (len(tags), len(tags_mod.files_of_tags(conn, [t["name"] for t in tags], "or"))))
    return EXIT_OK


def _tag_show(conn, args, cfg, console):
    """按标签查全部视频（本功能的核心）。"""
    args.has_cover = None
    args.cover = "any"
    args.tag = [] if getattr(args, "any", False) else list(args.names)
    args.tag_any = list(args.names) if getattr(args, "any", False) else []
    where, params = db.build_filters(args)
    rows = db.query_files(conn, (where, params), sort=args.sort,
                          desc=args.desc, limit=None if args.all else args.limit)
    attach_tags(conn, rows)

    if args.csv:
        path = report.export_index_csv(rows, args.csv)
        console.log("已导出 %d 条到 %s" % (len(rows), path))
    if args.json:
        dump_json(rows)
        return EXIT_OK

    console.log("标签：%s（%s）"
                % ("、".join(args.names), "任一命中" if args.any else "同时具备"))
    if not rows:
        console.log("没有文件带这些标签。")
        return EXIT_OK

    table = []
    for row in rows:
        table.append([
            row["id"],
            util.truncate(row["name"], 44),
            util.truncate(row.get("tags") or "", 18),
            util.human_size(row["size"]),
            util.human_duration(row["duration"]),
            row["path"],
        ])
    console.log(util.render_table(["ID", "文件名", "标签", "大小", "时长", "路径"], table))
    console.log("-" * 60)
    console.log("共 %d 个文件，合计 %s"
                % (len(rows), util.human_size(sum(r["size"] or 0 for r in rows))))
    return EXIT_OK


def _tag_rename(conn, args, cfg, console):
    info = tags_mod.rename_tag(conn, args.old, args.new)
    if not info:
        console.log("标签不存在：%s" % args.old)
        return EXIT_ERROR
    if info["merged"]:
        console.log("已把「%s」合并进「%s」，迁移 %d 条关联"
                    % (info["name"], info["new"], info["moved"]))
    else:
        console.log("标签已改名：%s → %s" % (info["name"], info["new"]))
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_export(conn, args, cfg, console):
    info = tags_mod.export_csv(conn, args.out or cfg.tags_backup)
    console.log("已导出 %d 条「文件-标签」关系到 %s" % (info["rows"], info["path"]))
    return EXIT_OK


def _tag_import(conn, args, cfg, console):
    info = tags_mod.import_csv(conn, args.csv, mode=args.mode)
    console.log("从 %s 导入：CSV 里 %d 条记录，新建 %d 条关联，涉及 %d 个文件"
                % (args.csv, info["rows"], info["added"], info["files"]))
    if info["missing_paths"]:
        console.log("  有 %d 条记录在索引里找不到对应文件（可能还没 scan，"
                    "或文件已被移动）" % info["missing_paths"])
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_undo(conn, args, cfg, console):
    steps = max(1, int(args.steps))
    done = tags_mod.undo(conn, steps)
    if not done:
        console.log("没有可回滚的标签操作。")
        return EXIT_OK
    for line in done:
        console.log("  " + line)
    console.log("已回滚 %d 步。" % len(done))
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_relink(conn, args, cfg, console):
    info = tags_mod.relink(conn, console.progress)
    console.progress_done()
    if not info["orphans"]:
        console.log("没有带标签的文件消失，无需重接。")
        return EXIT_OK
    console.log("带标签却已消失的文件 %d 个：成功重接 %d 个（%d 个标签），"
                "无法唯一配对 %d 个"
                % (info["orphans"], info["migrated"], info["tags_moved"],
                   info["unmatched"]))
    if not info["migrated"] and info["orphans"]:
        console.log("  提示：重接靠「大小 + 修改时间」完全一致来配对；"
                    "如果文件是重新编码或拷贝时改了时间，就配不上。")
    save_backup(conn, cfg, console)
    return EXIT_OK


def _tag_journal(conn, args, cfg, console):
    rows = tags_mod.journal_rows(conn, args.limit)
    if not rows:
        console.log("还没有标签操作记录。")
        return EXIT_OK
    console.log(util.render_table(
        ["时间", "动作", "文件ID", "标签"],
        [[r["ts"], r["action"], r["file_id"] if r["file_id"] is not None else "-",
          r["tag_name"] or "-"] for r in rows]))
    return EXIT_OK


# ------------------------------------------------------------------ 参数解析

def build_parser():
    parser = argparse.ArgumentParser(
        prog="vm",
        description="视频库管理工具（索引 / 报表 / 查重 / 标签）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", default="", help="配置文件路径，默认 vm_config.toml")
    parser.add_argument("--db", default="", help="索引数据库路径，默认取配置")
    parser.add_argument("--root", action="append", default=[],
                        help="覆盖配置里的根目录，可重复指定")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("-q", "--quiet", action="store_true", help="安静模式")

    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("roots", help="显示当前配置与工具路径")
    p.set_defaults(func=cmd_roots)

    p = sub.add_parser("scan", help="扫描目录并更新索引（只读，不会改动被管理的文件）")
    p.add_argument("--no-recurse", action="store_true", help="不进入子目录")
    p.add_argument("--no-probe", action="store_true", help="跳过 ffprobe 元数据探测")
    p.add_argument("--force-probe", action="store_true", help="连已探测过的文件也重新探测")
    p.add_argument("--probe-limit", type=int, default=None, help="本次最多探测多少个文件")
    p.add_argument("--limit", type=int, default=None, help="本次最多扫描多少个文件")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("stats", help="显示索引统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("list", help="按条件查询索引")
    p.add_argument("--kind", choices=["video", "image", "audio", "other"], default=None)
    p.add_argument("--ext", action="append", default=[], help="扩展名，可重复或逗号分隔")
    p.add_argument("--name", default=None, help="文件名包含的关键词")
    p.add_argument("--path-like", dest="path_like", default=None, help="路径包含的关键词")
    p.add_argument("--min-duration", dest="min_duration", default=None, help="如 10m / 1h30m")
    p.add_argument("--max-duration", dest="max_duration", default=None)
    p.add_argument("--min-size", dest="min_size", default=None, help="如 100MB")
    p.add_argument("--max-size", dest="max_size", default=None)
    p.add_argument("--min-height", dest="min_height", type=int, default=None,
                   help="最小视频高度，如 1080")
    p.add_argument("--year", default=None, help="按修改年份过滤，如 2024")
    p.add_argument("--cover", choices=["yes", "no", "any"], default="any",
                   help="内嵌封面：有 / 无 / 不限")
    p.add_argument("--no-cover", dest="cover", action="store_const", const="no",
                   help="等价于 --cover no")
    p.add_argument("--has-cover", dest="cover", action="store_const", const="yes",
                   help="等价于 --cover yes")
    p.add_argument("--missing", action="store_true", help="只看索引里已消失的文件")
    p.add_argument("--include-missing", dest="include_missing", action="store_true",
                   help="包含已消失的文件")
    p.add_argument("--probe-failed", dest="probe_failed", action="store_true",
                   help="只看探测失败的文件")
    p.add_argument("--tag", action="append", default=[],
                   help="按标签过滤，多个标签是「同时具备」，可重复")
    p.add_argument("--tag-any", dest="tag_any", action="append", default=[],
                   help="按标签过滤，多个标签是「具备任一」，可重复")
    p.add_argument("--sort", choices=["name", "size", "duration", "mtime", "path", "height"],
                   default="name")
    p.add_argument("--desc", action="store_true", help="倒序")
    p.add_argument("--limit", type=int, default=50, help="显示条数，默认 50")
    p.add_argument("--all", action="store_true", help="不限制条数")
    p.add_argument("--csv", default=None, help="把结果导出到 CSV")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("dupes", help="查重（只报告，绝不删除文件）")
    p.add_argument("--kind", choices=["video", "image", "audio", "all"], default="video")
    p.add_argument("--min-size", dest="min_size", default="1MB",
                   help="小于该大小的文件不参与查重，默认 1MB")
    p.add_argument("--no-quick", action="store_true", help="跳过首尾哈希这一级")
    p.add_argument("--no-full", action="store_true", help="跳过全文哈希（更快但可能误判）")
    p.add_argument("--workers", type=int, default=None, help="哈希并发数")
    p.add_argument("--limit-groups", dest="limit_groups", type=int, default=None,
                   help="最多输出多少组")
    p.add_argument("--csv", default=None, help="导出重复清单 CSV")
    p.set_defaults(func=cmd_dupes)

    p = sub.add_parser("report", help="生成 HTML / CSV 索引报表")
    p.add_argument("--out", default=None, help="HTML 输出路径")
    p.add_argument("--csv", default=None, help="同时导出 CSV")
    p.add_argument("--title", default="视频索引")
    p.add_argument("--kind", choices=["video", "image", "audio", "other"], default=None)
    p.add_argument("--ext", action="append", default=[])
    p.add_argument("--name", default=None)
    p.add_argument("--path-like", dest="path_like", default=None)
    p.add_argument("--min-duration", dest="min_duration", default=None)
    p.add_argument("--max-duration", dest="max_duration", default=None)
    p.add_argument("--min-size", dest="min_size", default=None)
    p.add_argument("--max-size", dest="max_size", default=None)
    p.add_argument("--year", default=None)
    p.add_argument("--missing", action="store_true")
    p.add_argument("--include-missing", dest="include_missing", action="store_true")
    p.add_argument("--probe-failed", dest="probe_failed", action="store_true")
    p.add_argument("--min-height", dest="min_height", type=int, default=None)
    p.add_argument("--tag", action="append", default=[],
                   help="按标签过滤，多个是「同时具备」")
    p.add_argument("--tag-any", dest="tag_any", action="append", default=[],
                   help="按标签过滤，多个是「具备任一」")
    p.add_argument("--sort", choices=["name", "size", "duration", "mtime", "path", "height"],
                   default="name")
    p.add_argument("--desc", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="默认不限")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_report)

    # ---------------------------------------------------------- rename
    p = sub.add_parser("rename", help="精简过长的文件名（默认试运行，可回滚）")
    p.add_argument("--max-length", dest="max_length", type=int, default=30,
                   help="名字超过这个长度才精简，默认 30")
    p.add_argument("--min-length", dest="min_length", type=int, default=0,
                   help="只处理长于这个长度的文件（可选）")
    p.add_argument("--apply", action="store_true", help="真正执行；不加则只试运行")
    p.add_argument("--undo", action="store_true", help="按日志还原改名")
    p.add_argument("--steps", type=int, default=0, help="--undo 时还原多少条，0=全部")
    p.add_argument("--journal", default=None, help="日志路径，默认 var/rename_journal.csv")
    p.add_argument("--limit", type=int, default=None, help="本次最多处理多少个")
    p.add_argument("--exclude", action="append", default=[],
                   help="按通配符排除文件名，可重复，如 --exclude \"*录屏*\"")
    p.add_argument("--csv", default=None, help="把改名计划导出成 CSV")
    p.set_defaults(func=cmd_rename)

    # ---------------------------------------------------------- tag 子命令组
    p_tag = sub.add_parser("tag", help="标签管理：打标签 / 按标签查找 / 备份恢复")
    tag_sub = p_tag.add_subparsers(dest="tag_cmd")

    p = tag_sub.add_parser("add", help="给文件打标签（标签名可写多个）")
    p.add_argument("names", nargs="+", help="标签名")
    add_selector_args(p)
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("rm", help="移除文件上的标签（标签本身保留）")
    p.add_argument("names", nargs="+", help="标签名")
    add_selector_args(p)
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("del", help="彻底删除标签（连同它所有的关联）")
    p.add_argument("names", nargs="+", help="标签名")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("list", help="列出所有标签及各自的文件数")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("show", help="按标签查文件（默认同时具备多个标签）")
    p.add_argument("names", nargs="+", help="标签名")
    p.add_argument("--any", action="store_true", help="改成「具备任一」")
    p.add_argument("--sort", choices=["name", "size", "duration", "mtime", "path", "height"],
                   default="name")
    p.add_argument("--desc", action="store_true")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true")
    p.add_argument("--csv", default=None, help="导出结果到 CSV")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("rename", help="标签改名；新名已存在则合并两个标签")
    p.add_argument("old", help="原标签名")
    p.add_argument("new", help="新标签名")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("export", help="导出「文件-标签」关系到 CSV")
    p.add_argument("--out", default=None, help="默认写到配置里的备份路径")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("import", help="从 CSV 恢复标签")
    p.add_argument("csv", help="CSV 路径")
    p.add_argument("--mode", choices=["merge", "replace"], default="merge",
                   help="merge=只增不减（默认），replace=清空后重建")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("undo", help="回滚最近的标签操作")
    p.add_argument("--steps", type=int, default=1, help="回滚几步，默认 1")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("relink", help="文件改名/移动后，把标签接回新路径")
    p.set_defaults(func=cmd_tag)

    p = tag_sub.add_parser("journal", help="查看标签操作日志")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_tag)

    return parser


def main(argv=None):
    util.setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return EXIT_USAGE

    cfg = config_mod.load(args.config)
    if args.root:
        cfg.roots = [config_mod.resolve_path(r) for r in args.root]
    if args.db:
        cfg.rebind_db(args.db)

    console = Console(quiet=args.quiet)
    try:
        return args.func(args, cfg, console)
    except KeyboardInterrupt:
        console.progress_done()
        console.log("已中断。")
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        console.progress_done()
        console.log("出错：%s" % exc)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
