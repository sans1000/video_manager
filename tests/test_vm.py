# -*- coding: utf-8 -*-
"""video_manager 单元测试（纯标准库 unittest，不依赖 pytest）。

全部在临时目录里造假文件，绝不在真实磁盘目录上做任何写操作。

运行：
    py -m unittest discover -s video_manager\tests -v
    或
    py video_manager\tests\test_vm.py
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import config as config_mod     # noqa: E402
from core import db, dupes, rename as rename_mod, report, scan   # noqa: E402
from core import tags as tags_mod, util   # noqa: E402


def make_file(path, data=b"x" * 100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


class TempTreeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vm_test_")
        self.db_path = os.path.join(self.tmp, "index.db")
        self.conn = db.open_db(self.db_path)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestUtil(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(util.human_size(0), "0 B")
        self.assertEqual(util.human_size(1536), "1.50 KB")
        self.assertEqual(util.human_size(1024 ** 3), "1.00 GB")

    def test_parse_size(self):
        self.assertEqual(util.parse_size("1KB"), 1024)
        self.assertEqual(util.parse_size("1.5MB"), 1572864)
        self.assertEqual(util.parse_size("2048"), 2048)
        self.assertEqual(util.parse_size("2g"), 2 * 1024 ** 3)

    def test_parse_duration(self):
        self.assertEqual(util.parse_duration("10m"), 600)
        self.assertEqual(util.parse_duration("1h30m"), 5400)
        self.assertEqual(util.parse_duration("90"), 90)
        self.assertEqual(util.parse_duration("01:30"), 90)

    def test_human_duration(self):
        self.assertEqual(util.human_duration(59), "0:59")
        self.assertEqual(util.human_duration(3600), "1:00:00")
        self.assertEqual(util.human_duration(3725), "1:02:05")

    def test_classify(self):
        self.assertEqual(util.classify(".MP4"), "video")
        self.assertEqual(util.classify(".jpg"), "image")
        self.assertEqual(util.classify(".lrc"), "other")

    def test_looks_like_copy(self):
        self.assertTrue(util.looks_like_copy("MVIMG_20260730_155323_1.jpg"))
        self.assertTrue(util.looks_like_copy("视频 - 副本.mp4"))
        self.assertFalse(util.looks_like_copy("VID_20241231_181414.mp4"))

    def test_natural_key_order(self):
        names = ["TMY10.mp4", "TMY2.mp4", "TMY1.mp4"]
        self.assertEqual(sorted(names, key=util.natural_key),
                         ["TMY1.mp4", "TMY2.mp4", "TMY10.mp4"])

    def test_long_path_roundtrip(self):
        short = r"F:\a\b.mp4"
        self.assertEqual(util.strip_long(util.long_path(short)), short)


class TestConfig(unittest.TestCase):
    def test_mini_toml(self):
        text = """
        # 注释
        [library]
        roots = ['F:\\a', "F:\\b"]
        recurse = true
        [hash]
        chunk_size = 1024
        algo = 'md5'
        """
        data = config_mod._mini_toml(text)
        self.assertEqual(data["library"]["roots"], ["F:\\a", "F:\\b"])
        self.assertTrue(data["library"]["recurse"])
        self.assertEqual(data["hash"]["chunk_size"], 1024)
        self.assertEqual(data["hash"]["algo"], "md5")

    def test_defaults_merge(self):
        cfg = config_mod.Config(config_mod._deep_merge(config_mod.DEFAULTS, {}))
        self.assertEqual(cfg.hash_algo, "blake2b")
        self.assertTrue(cfg.roots)

    def test_defaults_keep_every_gui_option(self):
        """回归：DEFAULTS 里曾经写了两个 `"gui": {...}`，后面那个把前面的整个盖掉，
        于是 start_maximized / window_fraction 的默认值凭空消失
        （当时靠 Config 里另写的兜底值才没出事）。
        字典字面量的重复键不报错，只是安静地少一半内容。"""
        gui = config_mod._deep_merge(config_mod.DEFAULTS, {})["gui"]
        for key in ("frameless", "start_maximized", "window_fraction"):
            self.assertIn(key, gui)


class TestPackagedPaths(unittest.TestCase):
    """打包成 exe 之后的路径解析（PyInstaller 会设 sys.frozen）。

    这是打包最容易踩的坑：打包后 `__file__` 指向 PyInstaller 的解包目录，
    照它去算「程序根目录」的话 —— 配置文件读不到、索引库和标签备份会被写到
    临时目录里（单文件模式下每次启动还是个新目录），退出就没了。
    表现就是「每次打开都像第一次，索引和标签全空」。
    """

    def setUp(self):
        self._had_frozen = hasattr(sys, "frozen")
        self._frozen = getattr(sys, "frozen", None)
        self._exe = sys.executable

    def tearDown(self):
        if self._had_frozen:
            sys.frozen = self._frozen
        elif hasattr(sys, "frozen"):       # 这个测试自己加上去的才要删
            del sys.frozen
        sys.executable = self._exe

    def _pretend_frozen(self, exe=r"D:\tools\视频管理器\视频库管理.exe"):
        sys.frozen = True
        sys.executable = exe

    def test_source_run_uses_the_project_folder(self):
        self.assertFalse(config_mod.is_frozen())
        self.assertEqual(config_mod.project_dir(), ROOT)

    def test_frozen_run_uses_the_exe_folder(self):
        self._pretend_frozen()
        self.assertTrue(config_mod.is_frozen())
        self.assertEqual(config_mod.project_dir(), r"D:\tools\视频管理器")
        self.assertEqual(config_mod.default_config_path(),
                         os.path.join(r"D:\tools\视频管理器", "vm_config.toml"))

    def test_relative_paths_land_next_to_the_exe(self):
        self._pretend_frozen()
        self.assertEqual(
            config_mod.resolve_path("var/video_index.db"),
            os.path.join(r"D:\tools\视频管理器", "var", "video_index.db"))

    def test_tool_candidates_look_next_to_the_exe(self):
        """ffmpeg 不塞进安装包，但要能从程序旁边捡到。"""
        self._pretend_frozen()
        found = config_mod.tool_candidates("ffmpeg.exe")
        root = r"D:\tools\视频管理器"
        self.assertIn(os.path.join(root, "ffmpeg.exe"), found)
        self.assertIn(os.path.join(root, "bin", "ffmpeg.exe"), found)
        self.assertIn(os.path.join(root, "tools", "ffmpeg.exe"), found)


class TestCliDbPath(unittest.TestCase):
    """命令行 `--db 相对路径` 要相对**程序目录**解析，不是当前工作目录。

    回归：`vm.py` 里 `cfg.rebind_db(args.db)` 已经把路径解析好了，
    但各个子命令打开数据库时用的还是原始参数 —— 于是 `--db var\\x.db`
    会在**当前目录**建库，而标签备份按程序目录算，两边不是同一个文件。
    打包成 exe 之后更容易踩：快捷方式、计划任务、从别的目录调用时，
    工作目录都不是程序所在目录。
    """

    NAME = "var/_cli_dbpath_test.db"

    def setUp(self):
        self.target = os.path.join(ROOT, self.NAME)
        self.elsewhere = tempfile.mkdtemp(prefix="vm_cwd_")
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        for suffix in ("", "-wal", "-shm"):
            path = self.target + suffix
            if os.path.isfile(path):
                os.remove(path)
            self.addCleanup(lambda p=path: os.path.isfile(p) and os.remove(p))

    def test_relative_db_lands_next_to_the_program(self):
        import vm

        cwd = os.getcwd()
        os.chdir(self.elsewhere)                   # 故意换一个工作目录
        self.addCleanup(os.chdir, cwd)

        with contextlib.redirect_stdout(io.StringIO()):
            code = vm.main(["--db", self.NAME, "stats"])

        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self.target), "索引库没建在程序目录下")
        stray = os.path.join(self.elsewhere, self.NAME)
        self.assertFalse(os.path.isfile(stray), "索引库被建到了当前工作目录下")

    def test_absolute_db_is_used_as_is(self):
        import vm

        absolute = os.path.join(self.elsewhere, "somewhere.db")
        with contextlib.redirect_stdout(io.StringIO()):
            code = vm.main(["--db", absolute, "stats"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(absolute))


class TestScan(TempTreeCase):
    def test_scan_counts_and_incremental(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"a" * 10)
        make_file(os.path.join(tree, "sub", "b.mp4"), b"b" * 20)
        make_file(os.path.join(tree, "note.txt"), b"t" * 5)

        summary = scan.sync_root(self.conn, tree)
        self.assertEqual(summary["seen"], 3)
        self.assertEqual(summary["new"], 3)
        self.assertEqual(summary["by_kind"]["video"], 2)

        # 第二次扫描：全都应该判定为未变化
        summary2 = scan.sync_root(self.conn, tree)
        self.assertEqual(summary2["unchanged"], 3)
        self.assertEqual(summary2["new"], 0)

        self.assertEqual(db.count(self.conn, "kind = 'video'"), 2)
        self.assertEqual(db.count(self.conn, "kind = 'other'"), 1)

    def test_changed_file_resets_probe_state(self):
        tree = os.path.join(self.tmp, "media")
        target = os.path.join(tree, "a.mp4")
        make_file(target, b"a" * 10)
        scan.sync_root(self.conn, tree)

        row = self.conn.execute("SELECT * FROM files WHERE name='a.mp4'").fetchone()
        db.update_file(self.conn, row["id"], {"probe_state": 1, "duration": 12.0})
        self.conn.commit()

        make_file(target, b"a" * 99)          # 改内容
        summary = scan.sync_root(self.conn, tree)
        self.assertEqual(summary["updated"], 1)
        row = self.conn.execute("SELECT * FROM files WHERE name='a.mp4'").fetchone()
        self.assertEqual(row["probe_state"], 0)     # 需要重新探测
        self.assertIsNone(row["duration"])

    def test_missing_marked(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"a" * 10)
        scan.sync_root(self.conn, tree)
        os.remove(os.path.join(tree, "a.mp4"))
        summary = scan.sync_root(self.conn, tree)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(db.count(self.conn, "missing = 1"), 1)
        # 默认查询不返回已消失的文件
        rows = db.query_files(self.conn, db.build_filters(_Args()))
        self.assertEqual(len(rows), 0)

    def test_exclude_dirs(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "keep.mp4"), b"a" * 10)
        make_file(os.path.join(tree, "__pycache__", "x.mp4"), b"a" * 10)
        summary = scan.sync_root(self.conn, tree, exclude_dirs=util.DEFAULT_EXCLUDE_DIRS)
        self.assertEqual(summary["seen"], 1)


class _Args(object):
    """给 build_filters 用的空参数对象。"""

    def __getattr__(self, item):
        return None


class TestDupes(TempTreeCase):
    def _scan(self, tree):
        return scan.sync_root(self.conn, tree)

    def test_finds_identical_videos(self):
        tree = os.path.join(self.tmp, "media")
        payload = b"V" * 4096
        make_file(os.path.join(tree, "one.mp4"), payload)
        make_file(os.path.join(tree, "sub", "one_copy.mp4"), payload)
        make_file(os.path.join(tree, "other.mp4"), b"Z" * 4096)
        self._scan(tree)

        result = dupes.find_groups(self.conn, kind="video", min_size=1, workers=2)
        self.assertEqual(result["stats"]["groups"], 1)
        group = result["groups"][0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["wasted"], 4096)
        # 保留的应该是路径更浅的那个
        self.assertTrue(group["keeper_path"].endswith("one.mp4"))

    def test_same_size_different_content_not_duplicate(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"A" * 5000)
        make_file(os.path.join(tree, "b.mp4"), b"B" * 5000)
        self._scan(tree)
        result = dupes.find_groups(self.conn, kind="video", min_size=1, workers=2)
        self.assertEqual(result["stats"]["groups"], 0)

    def test_keeper_prefers_non_copy_name(self):
        rows = [
            {"id": 1, "path": r"F:\m\video - 副本.mp4", "name": "video - 副本.mp4", "mtime": 1},
            {"id": 2, "path": r"F:\m\video.mp4", "name": "video.mp4", "mtime": 9},
        ]
        self.assertEqual(dupes.pick_keeper(rows)["id"], 2)

    def test_hashes_are_cached(self):
        tree = os.path.join(self.tmp, "media")
        payload = b"V" * 2048
        make_file(os.path.join(tree, "a.mp4"), payload)
        make_file(os.path.join(tree, "b.mp4"), payload)
        self._scan(tree)
        dupes.find_groups(self.conn, kind="video", min_size=1, workers=1)
        row = self.conn.execute("SELECT full_hash FROM files WHERE name='a.mp4'").fetchone()
        self.assertTrue(row["full_hash"])


class _TagArgs(_Args):
    """带标签过滤条件的参数对象。"""

    def __init__(self, tag=None, tag_any=None):
        self.tag = tag or []
        self.tag_any = tag_any or []


class TestTags(TempTreeCase):
    def _tree(self):
        """造三个视频文件并入库，返回目录。

        故意用不同的文件大小：Windows 的文件时间戳精度会粗到同一刻，
        如果三个文件大小也一样，relink 的「大小+时间」就会歧义。
        """
        tree = os.path.join(self.tmp, "media")
        for name, size in (("a.mp4", 100), ("b.mp4", 200), ("c.mp4", 300)):
            make_file(os.path.join(tree, name), b"x" * size)
        scan.sync_root(self.conn, tree)
        return tree

    def _id_of(self, name):
        row = self.conn.execute("SELECT id FROM files WHERE name = ?", (name,)).fetchone()
        return row["id"]

    def test_add_and_query(self):
        self._tree()
        result = tags_mod.add_tags(self.conn, [self._id_of("a.mp4")], ["好片", "1080p"])
        self.assertEqual(result["added"], 2)
        self.assertEqual(sorted(result["new_tags"]), ["1080p", "好片"])

        names = [t["name"] for t in tags_mod.list_tags(self.conn)]
        self.assertEqual(sorted(names), ["1080p", "好片"])

        hit = tags_mod.tags_map(self.conn, [self._id_of("a.mp4")])
        self.assertEqual(sorted(hit[self._id_of("a.mp4")]), ["1080p", "好片"])

        # 重复打同一个标签不应该产生第二条关联
        again = tags_mod.add_tags(self.conn, [self._id_of("a.mp4")], ["好片"])
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["existing"], 1)

    def test_tag_name_is_case_and_space_insensitive(self):
        self._tree()
        tags_mod.add_tags(self.conn, [self._id_of("a.mp4")], ["1080P"])
        tags_mod.add_tags(self.conn, [self._id_of("b.mp4")], ["  1080p "])
        tags = tags_mod.list_tags(self.conn)
        self.assertEqual(len(tags), 1)          # 只应存在一个标签
        self.assertEqual(tags[0]["n"], 2)
        self.assertEqual(tags[0]["name"], "1080P")   # 显示名保留第一次的写法

    def test_and_or_filter(self):
        self._tree()
        a, b, c = (self._id_of(n) for n in ("a.mp4", "b.mp4", "c.mp4"))
        tags_mod.add_tags(self.conn, [a, b], ["红"])
        tags_mod.add_tags(self.conn, [b, c], ["大"])

        self.assertEqual(sorted(tags_mod.files_of_tags(self.conn, ["红"], "and")), sorted([a, b]))
        self.assertEqual(tags_mod.files_of_tags(self.conn, ["红", "大"], "and"), [b])
        self.assertEqual(sorted(tags_mod.files_of_tags(self.conn, ["红", "大"], "or")),
                         sorted([a, b, c]))

        # 走 SQL 过滤路径：list --tag 红 --tag 大 等价于 AND
        where, params = db.build_filters(_TagArgs(tag=["红", "大"]))
        rows = db.query_files(self.conn, (where, params))
        self.assertEqual([r["id"] for r in rows], [b])

        where, params = db.build_filters(_TagArgs(tag_any=["红", "大"]))
        rows = db.query_files(self.conn, (where, params), sort="id")
        self.assertEqual([r["id"] for r in rows], [a, b, c])

    def test_remove_and_undo(self):
        self._tree()
        a = self._id_of("a.mp4")
        tags_mod.add_tags(self.conn, [a], ["临时"])

        removed = tags_mod.remove_tags(self.conn, [a], ["临时"])
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(tags_mod.files_of_tags(self.conn, ["临时"]), [])

        tags_mod.undo(self.conn, 1)             # 撤销「移除」
        self.assertEqual(tags_mod.files_of_tags(self.conn, ["临时"]), [a])

        tags_mod.undo(self.conn, 1)             # 撤销最初的「打标签」
        self.assertEqual(tags_mod.files_of_tags(self.conn, ["临时"]), [])

    def test_delete_tag_and_undo(self):
        self._tree()
        a, b = self._id_of("a.mp4"), self._id_of("b.mp4")
        tags_mod.add_tags(self.conn, [a, b], ["要删的"])

        info = tags_mod.delete_tag(self.conn, "要删的")
        self.assertEqual(info["unlinked"], 2)
        self.assertEqual(tags_mod.list_tags(self.conn), [])

        tags_mod.undo(self.conn, 1)
        restored = tags_mod.list_tags(self.conn)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["n"], 2)   # 两个文件的关联都回来了

    def test_rename_and_merge(self):
        self._tree()
        a, b = self._id_of("a.mp4"), self._id_of("b.mp4")
        tags_mod.add_tags(self.conn, [a], ["胡桃"])
        tags_mod.add_tags(self.conn, [b], ["原神"])

        # 单纯改名
        info = tags_mod.rename_tag(self.conn, "原神", "原神角色")
        self.assertFalse(info["merged"])
        self.assertEqual([t["name"] for t in tags_mod.list_tags(self.conn)],
                         ["原神角色", "胡桃"])

        # 合并：把「原神角色」并进「胡桃」
        info = tags_mod.rename_tag(self.conn, "原神角色", "胡桃")
        self.assertTrue(info["merged"])
        merged = tags_mod.files_of_tags(self.conn, ["胡桃"], "and")
        self.assertEqual(sorted(merged), sorted([a, b]))
        self.assertEqual(len(tags_mod.list_tags(self.conn)), 1)

    def test_export_import_roundtrip(self):
        self._tree()
        a, b = self._id_of("a.mp4"), self._id_of("b.mp4")
        tags_mod.add_tags(self.conn, [a, b], ["备份测试"])

        csv_path = os.path.join(self.tmp, "tags.csv")
        info = tags_mod.export_csv(self.conn, csv_path)
        self.assertEqual(info["rows"], 2)
        with open(csv_path, encoding="utf-8-sig") as fh:
            self.assertIn("备份测试", fh.read())

        tags_mod.delete_tag(self.conn, "备份测试")
        self.assertEqual(tags_mod.list_tags(self.conn), [])

        back = tags_mod.import_csv(self.conn, csv_path)
        self.assertEqual(back["added"], 2)
        self.assertEqual(back["missing_paths"], 0)
        self.assertEqual(sorted(tags_mod.files_of_tags(self.conn, ["备份测试"])),
                         sorted([a, b]))

    def test_import_reports_unknown_paths(self):
        self._tree()
        csv_path = os.path.join(self.tmp, "orphan.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("完整路径,文件名,标签,打标签时间\n")
            fh.write("F:\\不存在的\\x.mp4,x.mp4,幽灵,\n")
        info = tags_mod.import_csv(self.conn, csv_path)
        self.assertEqual(info["added"], 0)
        self.assertEqual(info["missing_paths"], 1)

    def test_relink_after_rename(self):
        tree = self._tree()
        a = self._id_of("a.mp4")
        tags_mod.add_tags(self.conn, [a], ["跟着走"])

        # 改名（同盘 rename 保留 mtime）后重新扫描
        old_path = os.path.join(tree, "a.mp4")
        new_path = os.path.join(tree, "a改名了.mp4")
        os.rename(old_path, new_path)
        summary = scan.sync_root(self.conn, tree)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["new"], 1)

        info = tags_mod.relink(self.conn)
        self.assertEqual(info["orphans"], 1)
        self.assertEqual(info["migrated"], 1)
        self.assertEqual(info["tags_moved"], 1)

        new_id = self._id_of("a改名了.mp4")
        self.assertEqual(tags_mod.tags_map(self.conn, [new_id])[new_id], ["跟着走"])
        self.assertEqual(tags_mod.tags_map(self.conn, [a]).get(a), None)

    def test_relink_skips_ambiguous_match(self):
        """两个同 size 同 mtime 的候选时不敢乱接。"""
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"a" * 100)
        scan.sync_root(self.conn, tree)
        a = self._id_of("a.mp4")
        tags_mod.add_tags(self.conn, [a], ["有歧义"])

        old = os.path.join(tree, "a.mp4")
        stamp = os.stat(old).st_mtime
        os.rename(old, os.path.join(tree, "gone.mp4"))
        # 造两个和它 size、mtime 都一模一样的候选，制造歧义
        for name in ("x.mp4", "y.mp4"):
            fresh = make_file(os.path.join(tree, name), b"a" * 100)
            os.utime(fresh, (stamp, stamp))
        scan.sync_root(self.conn, tree)

        info = tags_mod.relink(self.conn)
        self.assertEqual(info["migrated"], 0)
        self.assertGreaterEqual(info["unmatched"], 1)


class TestRenameRules(unittest.TestCase):
    """精简规则本身（纯字符串，不碰磁盘）。"""

    def shorten(self, stem, max_len=30, folder=None):
        return rename_mod.shorten_stem(stem, max_len, folder=folder)

    def test_removes_site_tail_and_noise(self):
        src = ("SM女王-Nana详情介绍-SM女王-Nana在线观看-"
               "SM女王-Nana迅雷下载 - 黄色仓库-hsck.net")
        self.assertEqual(self.shorten(src), "SM女王-Nana")

    def test_collapses_two_copies(self):
        self.assertEqual(self.shorten("小鸟酱之水晶性工具-小鸟酱之水晶性工具"),
                         "小鸟酱之水晶性工具")

    def test_collapses_three_copies(self):
        self.assertEqual(self.shorten("COS淫色胡桃-甜心-COS淫色胡桃-甜心-COS淫色胡桃-甜心"),
                         "COS淫色胡桃-甜心")

    def test_collapses_with_leftover_tail(self):
        """三份完整重复 + 半截尾巴（真实数据里 skyhd-039 就是这种）。"""
        got = self.shorten("skyhd-039 标题-skyhd-039 标题-skyhd-039 标题-黄", 60)
        self.assertEqual(got, "skyhd-039 标题-黄")

    def test_strips_scraper_prefix(self):
        src = "Ezma_python1_pachong爬神奇的website2黑丝00后戴乳夹乘骑被内射详情介绍-黑丝00后戴乳夹乘骑被内射"
        got = self.shorten(src)
        self.assertFalse(got.startswith("Ezma_"))
        self.assertIn("黑丝00后", got)

    def test_keeps_short_ascii_prefix(self):
        """MCY-0119 / skyhd-039 这种短前缀是有效信息，不能当成爬虫前缀删掉。"""
        self.assertTrue(self.shorten("MCY-0119超辣警花", 30).startswith("MCY-0119"))
        self.assertTrue(self.shorten("skyhd-039 标题", 30).startswith("skyhd-039"))

    def test_strips_redundant_folder_prefix(self):
        got = self.shorten("Screenrecorder-2026-08-26-11-07-50-589", 30,
                           folder="ScreenRecorder")
        self.assertEqual(got, "2026-08-26-11-07-50-589")

    def test_does_not_strip_folder_prefix_mid_word(self):
        """目录叫 SM，文件名是 SM女王-Nana，'SM' 后面不是分隔符，不能切。"""
        self.assertEqual(self.shorten("SM女王-Nana", 30, folder="SM"), "SM女王-Nana")

    def test_does_not_strip_when_nothing_left(self):
        name = "Hidori Rose with Enafox Chocola and Vanilla"
        got = self.shorten(name, 30, folder=name)
        self.assertTrue(got)
        self.assertNotEqual(got, "")

    def test_truncates_to_limit(self):
        got = self.shorten("A" * 80)
        self.assertEqual(len(got), 30)

    def test_never_exceeds_limit(self):
        samples = [
            "Hidori Rose with Enafox Chocola and Vanilla",
            "天美传媒TMY0031寸止挑战6射在姐姐婚纱上详情介绍-天美传媒TMY0031寸止挑战6射在姐姐婚纱上在线观看-天美传媒TMY0031寸止挑战6射在姐姐婚纱上迅雷下载 - 黄色仓库-hsck.net",
            "Ezma_python1_pachong爬神奇的website2果冻传媒91KCM-043爆操路上遇见的黑丝女警详情介绍-果冻传媒91KCM-043爆操遇见",
            "-" * 20 + "标题" * 20,
        ]
        for stem in samples:
            for limit in (8, 16, 30, 60):
                self.assertLessEqual(len(self.shorten(stem, limit)), limit,
                                     "%s @ %d" % (stem[:20], limit))

    def test_strips_illegal_characters(self):
        got = self.shorten('标题<>:"/\\|?*测试' + "长" * 30)
        for ch in '<>:"/\\|?*':
            self.assertNotIn(ch, got)

    def test_leaves_good_names_alone(self):
        self.assertEqual(self.shorten("RBQ", 30), "RBQ")


class TestRenamePlan(TempTreeCase):
    def _tree(self):
        tree = os.path.join(self.tmp, "media")
        long_a = os.path.join(tree, "标题" * 20 + "详情介绍-" + "标题" * 20 + ".mp4")
        make_file(long_a, b"a" * 100)
        make_file(os.path.join(tree, "short.mp4"), b"b" * 100)
        scan.sync_root(self.conn, tree)
        return tree, long_a

    def test_only_touches_long_names(self):
        self._tree()
        plans = rename_mod.plan(self.conn, max_len=30)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["old_name"], "标题" * 20 + "详情介绍-" + "标题" * 20 + ".mp4")
        self.assertEqual(plans[0]["old_len"], 85)
        self.assertLessEqual(plans[0]["new_len"], 30)

    def test_collision_gets_suffix(self):
        tree = os.path.join(self.tmp, "media")
        # 两个文件精简后会撞名
        make_file(os.path.join(tree, "同样的标题" * 10 + "-甲.mp4"), b"a" * 10)
        make_file(os.path.join(tree, "同样的标题" * 10 + "-乙.mp4"), b"b" * 10)
        scan.sync_root(self.conn, tree)
        plans = rename_mod.plan(self.conn, max_len=8)
        names = [p["new_name"] for p in plans]
        self.assertEqual(len(names), len(set(n.lower() for n in names)), names)

    def test_exclude_glob(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "很长很长的名字" * 5 + ".mp4"), b"a" * 10)
        make_file(os.path.join(tree, "Screenrecorder-2026-08-26-11-07-50-589.mp4"),
                  b"b" * 10)
        scan.sync_root(self.conn, tree)
        plans = rename_mod.plan(self.conn, max_len=30, exclude_globs=["Screenrecorder*"])
        self.assertEqual([p["old_name"] for p in plans],
                         ["很长很长的名字" * 5 + ".mp4"])


class TestRenameApply(TempTreeCase):
    def test_apply_updates_db_in_place_and_keeps_tags(self):
        tree = os.path.join(self.tmp, "media")
        old_name = "很长的标题" * 8 + ".mp4"
        old_path = make_file(os.path.join(tree, old_name), b"a" * 500)
        scan.sync_root(self.conn, tree)

        row = self.conn.execute("SELECT id FROM files").fetchone()
        file_id = row["id"]
        tags_mod.add_tags(self.conn, [file_id], ["要跟着走"])

        plans = rename_mod.plan(self.conn, max_len=30)
        self.assertEqual(len(plans), 1)
        journal = os.path.join(self.tmp, "journal.csv")
        info = rename_mod.apply(self.conn, plans, journal)
        self.assertEqual(info["renamed"], 1)
        self.assertEqual(info["failed"], [])

        new_path = plans[0]["new_path"]
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.isfile(new_path))

        # 关键：文件 ID 不变，标签和哈希都跟着走
        after = self.conn.execute("SELECT id, path, name FROM files").fetchone()
        self.assertEqual(after["id"], file_id)
        self.assertEqual(after["path"], new_path)
        self.assertEqual(tags_mod.tags_map(self.conn, [file_id])[file_id], ["要跟着走"])

        # 再扫一遍不应该出现「消失 + 新增」
        summary = scan.sync_root(self.conn, tree)
        self.assertEqual(summary["missing"], 0)
        self.assertEqual(summary["new"], 0)
        self.assertEqual(summary["unchanged"], 1)

    def test_undo_restores_everything(self):
        tree = os.path.join(self.tmp, "media")
        old_name = "很长的标题" * 8 + ".mp4"
        old_path = make_file(os.path.join(tree, old_name), b"a" * 500)
        scan.sync_root(self.conn, tree)
        file_id = self.conn.execute("SELECT id FROM files").fetchone()["id"]
        tags_mod.add_tags(self.conn, [file_id], ["标签"])

        journal = os.path.join(self.tmp, "journal.csv")
        plans = rename_mod.plan(self.conn, max_len=30)
        rename_mod.apply(self.conn, plans, journal)
        self.assertTrue(os.path.isfile(plans[0]["new_path"]))

        info = rename_mod.undo(self.conn, journal)
        self.assertEqual(info["restored"], 1)
        self.assertEqual(info["failed"], [])
        self.assertTrue(os.path.isfile(old_path))
        self.assertFalse(os.path.exists(plans[0]["new_path"]))

        row = self.conn.execute("SELECT id, path FROM files").fetchone()
        self.assertEqual(row["id"], file_id)
        self.assertEqual(row["path"], old_path)
        self.assertEqual(tags_mod.tags_map(self.conn, [file_id])[file_id], ["标签"])

        # 日志清空，避免重复回滚
        self.assertEqual(rename_mod.read_journal(journal), [])

    def test_apply_skips_when_target_exists(self):
        tree = os.path.join(self.tmp, "media")
        old_name = "很长的标题" * 8 + ".mp4"
        make_file(os.path.join(tree, old_name), b"a" * 100)
        scan.sync_root(self.conn, tree)
        plans = rename_mod.plan(self.conn, max_len=30)

        # 计划做好之后，目标名被占用了
        make_file(plans[0]["new_path"], b"b" * 100)
        journal = os.path.join(self.tmp, "journal.csv")
        info = rename_mod.apply(self.conn, plans, journal)
        self.assertEqual(info["renamed"], 0)
        self.assertEqual(len(info["failed"]), 1)
        self.assertIn("目标已存在", info["failed"][0][1])

    def test_apply_skips_file_that_changed_after_planning(self):
        """模拟「计划做好后文件还在被写入」，绝不能动它。"""
        tree = os.path.join(self.tmp, "media")
        old_name = "很长的标题" * 8 + ".mp4"
        old_path = make_file(os.path.join(tree, old_name), b"a" * 100)
        scan.sync_root(self.conn, tree)
        plans = rename_mod.plan(self.conn, max_len=30)

        with open(old_path, "ab") as fh:
            fh.write(b"more data")            # 文件变大了

        journal = os.path.join(self.tmp, "journal.csv")
        info = rename_mod.apply(self.conn, plans, journal)
        self.assertEqual(info["renamed"], 0)
        self.assertIn("仍在变化", info["failed"][0][1])
        self.assertTrue(os.path.isfile(old_path))


class TestReport(TempTreeCase):
    def test_csv_and_html(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"a" * 10)
        make_file(os.path.join(tree, "b.mp4"), b"b" * 10)
        scan.sync_root(self.conn, tree)
        rows = db.query_files(self.conn, db.build_filters(_Args()))

        csv_path = os.path.join(self.tmp, "out.csv")
        report.export_index_csv(rows, csv_path)
        with open(csv_path, encoding="utf-8-sig") as fh:
            text = fh.read()
        self.assertIn("a.mp4", text)

        html_path = os.path.join(self.tmp, "index.html")
        report.render_html(rows, html_path, title="测试", stats=db.stats(self.conn))
        with open(html_path, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("a.mp4", page)
        self.assertIn("<table", page)
        self.assertNotIn("__ROWS__", page)      # 占位符都要被替换掉

    def test_stats(self):
        tree = os.path.join(self.tmp, "media")
        make_file(os.path.join(tree, "a.mp4"), b"a" * 10)
        scan.sync_root(self.conn, tree)
        st = db.stats(self.conn)
        self.assertEqual(st["total"], 1)
        self.assertEqual(st["video_count"], 1)
        self.assertIn("索引概况", report.format_stats(st))


if __name__ == "__main__":
    unittest.main(verbosity=2)
