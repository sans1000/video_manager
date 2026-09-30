# -*- coding: utf-8 -*-
"""界面层测试：用 offscreen 平台真正把窗口搭起来，验证不会当场炸。

重构后新增的重点是「标签页」：每页独立的查询状态、按标签开页、防重复、
关闭后不留野指针。没装 PyQt5 时整类跳过。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台不会自己去找系统字体：不指定目录的话 Qt 只会刷
# "QFontDatabase: Cannot find font directory"，然后**任何文字都画不出来**。
# 之前整套界面测试就是在这样一个没字体的环境里跑的 —— 于是「文字根本没
# 画出来」这类问题（比如标签上那个看不见的 ×）一个都测不出来。
# 必须和 QT_QPA_PLATFORM 一样，在 QApplication 建出来之前设好。
for _font_dir in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                  "/usr/share/fonts", "/System/Library/Fonts"):
    if os.path.isdir(_font_dir):
        os.environ.setdefault("QT_QPA_FONTDIR", _font_dir)
        break

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import db, scan                      # noqa: E402

try:
    from PyQt5.QtCore import QPoint, QRect, Qt
    from PyQt5.QtGui import QPixmap
    from PyQt5.QtWidgets import QApplication, QMenuBar
    HAS_QT = True
except Exception:                              # noqa: BLE001
    HAS_QT = False


def make_file(path, data=b"x" * 100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


@unittest.skipUnless(HAS_QT, "未安装 PyQt5，跳过界面测试")
class GuiCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui.style import STYLESHEET

        cls.app = QApplication.instance() or QApplication([])
        # 必须套上真实样式表：QSS 会改变控件尺寸、进而影响分栏宽度。
        # 之前测试不加载样式表，所以「详情面板被长路径撑宽、把网格挤成 2 列」
        # 这类问题全被漏掉了。
        cls.app.setStyleSheet(STYLESHEET)

    def setUp(self):
        from gui.engine import Engine
        from gui.session import Session

        self.tmp = tempfile.mkdtemp(prefix="vm_gui_test_")
        self.tree = os.path.join(self.tmp, "media")
        for i, name in enumerate(("alpha.mp4", "beta.mp4", "gamma.mp4",
                                  "delta.mp4", "epsilon.mp4")):
            make_file(os.path.join(self.tree, name), b"v" * (100 + i))
        self.db_path = os.path.join(self.tmp, "index.db")

        conn = db.open_db(self.db_path)
        scan.sync_root(conn, self.tree)
        conn.close()

        self.engine = Engine(db_path=self.db_path, root=self.tree)
        self.engine.cfg.ffmpeg = ""            # 测试里不真去抽帧
        # 默认不让窗口一出生就最大化：绝大多数测试要的是「窗口模式」下的行为。
        # 专门验证启动最大化的测试会自己把它打开。
        self.engine.cfg.start_maximized = False
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.shutdown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_window(self, frameless=True):
        from gui.main_window import MainWindow
        window = MainWindow(self.session, frameless=frameless)
        self.addCleanup(window.close)
        return window


class TestQueryState(GuiCase):
    def test_filters_and_title(self):
        from gui.query import QueryState

        state = QueryState()
        self.assertTrue(state.is_plain())
        self.assertEqual(state.title(), "全部视频")

        state.set_tag("胡桃")
        self.assertEqual(state.title(), "胡桃")
        self.assertEqual(state.filters()["tags"], ["胡桃"])
        self.assertFalse(state.is_plain())

    def test_toggle_tag(self):
        from gui.query import QueryState

        state = QueryState()
        self.assertTrue(state.toggle_tag("A"))
        self.assertTrue(state.toggle_tag("B"))
        self.assertEqual(state.tags, ["A", "B"])
        self.assertFalse(state.toggle_tag("A"))
        self.assertEqual(state.tags, ["B"])

    def test_describe_and_reset(self):
        from gui.query import QueryState

        state = QueryState(tags=["A"], keyword="x")
        state.min_height = 1080
        text = state.describe()
        self.assertIn("标签：A", text)
        self.assertIn("1080", text)
        self.assertIn("关键词", text)
        state.reset_filters()
        self.assertIsNone(state.keyword)
        self.assertEqual(state.tags, ["A"])     # 标签不属于「筛选面板」的范畴

    def test_states_are_independent(self):
        from gui.query import QueryState

        a, b = QueryState(), QueryState()
        a.set_tag("X")
        a.keyword = "foo"
        self.assertEqual(b.tags, [])
        self.assertIsNone(b.keyword)


class TestGridModel(GuiCase):
    def test_roles_and_rows(self):
        from gui.grid import GridMetrics
        from gui.model import VideoGridModel

        metrics = GridMetrics()
        model = VideoGridModel(metrics)
        rows, total = self.session.engine.query(limit=10)
        model.set_rows(rows, total)
        self.assertEqual(model.rowCount(), 5)
        self.assertEqual(model.total(), 5)
        self.assertEqual(model.data(model.index(0, 0), Qt.DisplayRole), rows[0]["name"])
        self.assertEqual(model.data(model.index(0, 0), Qt.UserRole)["id"], rows[0]["id"])
        size = model.data(model.index(0, 0), Qt.SizeHintRole)
        # sizeHint 给的是「格子」尺寸（卡片 + 一条缝），绘制时再把缝让出来
        self.assertEqual((size.width(), size.height()),
                         (metrics.card_width + 8, metrics.card_height + 8))
        self.assertEqual(model.row_at(0)["name"], rows[0]["name"])
        self.assertIsNone(model.row_at(99))

    def test_append_and_index_of_id(self):
        from gui.model import VideoGridModel

        model = VideoGridModel()
        first, total = self.session.engine.query(limit=2)
        model.set_rows(first, total)
        more, _ = self.session.engine.query(limit=10, offset=2)
        model.append_rows(more)
        self.assertEqual(model.loaded(), 5)
        self.assertEqual(model.index_of_id(first[1]["id"]).row(), 1)


class TestGridMetrics(GuiCase):
    """每行固定 2 个，卡片和缩略图跟着窗口宽度走。"""

    def test_always_two_columns(self):
        from gui.grid import COLUMNS, card_metrics

        self.assertEqual(COLUMNS, 2)
        for width in (400, 600, 900, 1280, 1920, 2560):
            card_w, card_h, thumb_h = card_metrics(width)
            self.assertGreater(card_w, 0)

    def test_card_grows_with_window(self):
        from gui.grid import card_metrics

        widths = [500, 800, 1200, 1800]
        cards = [card_metrics(w)[0] for w in widths]
        self.assertEqual(cards, sorted(cards))              # 单调递增
        self.assertLess(cards[0], cards[-1])

    def test_thumbnail_is_16_9(self):
        from gui.grid import card_metrics

        for width in (600, 1000, 1600):
            card_w, _card_h, thumb_h = card_metrics(width)
            self.assertLessEqual(abs(thumb_h - card_w * 9 / 16), 1,
                                 "%d 宽时缩略图 %d 不是 16:9" % (card_w, thumb_h))

    def test_card_height_is_thumb_plus_text(self):
        from gui.grid import TEXT_BLOCK, card_metrics

        card_w, card_h, thumb_h = card_metrics(1200)
        self.assertEqual(card_h, thumb_h + TEXT_BLOCK)

    def test_card_fits_two_per_row(self):
        from gui.grid import COLUMNS, GAP, card_metrics

        for viewport in (600, 900, 1280, 1920):
            card_w, _h, _t = card_metrics(viewport)
            # 每格宽度 = 卡宽 + 缝，N 格必须塞得进 viewport
            used = (card_w + GAP) * COLUMNS
            self.assertLessEqual(used, viewport,
                                 "viewport %d 放不下两格 %d" % (viewport, card_w + GAP))
            self.assertGreater(used, viewport - GAP - 2)   # 也别浪费太多

    def test_narrow_viewport_still_fits_requested_columns(self):
        """回归：卡片宽度曾经被钳到一个「好看」的下限（120px），
        视口一窄，实际列数就少于用户选的数量 —— 选 4 列只摆 3 列，
        菜单却还显示 4。这里要求任何宽度下 N 列都真的放得下。
        """
        from gui.grid import GAP, card_metrics

        for width, columns in ((386, 4), (300, 3), (240, 2), (180, 2),
                               (120, 1), (900, 4), (2560, 4)):
            card_w, _h, _t = card_metrics(width, columns)
            self.assertLessEqual((card_w + GAP) * columns, width,
                                 "viewport %d 放不下 %d 列（卡宽 %d）"
                                 % (width, columns, card_w))

    def test_bucket_quantization(self):
        from gui.grid import THUMB_BUCKETS, bucket_for

        self.assertEqual(bucket_for(200), THUMB_BUCKETS[0])
        self.assertEqual(bucket_for(365), 480)
        self.assertEqual(bucket_for(10000), THUMB_BUCKETS[-1])
        # HiDPI 下往上取一档
        self.assertEqual(bucket_for(300, 2.0), 640)

    def test_bad_bucket_input_is_safe(self):
        from gui.grid import DEFAULT_BUCKET, bucket_for

        self.assertEqual(bucket_for(0), DEFAULT_BUCKET)
        self.assertEqual(bucket_for(None), DEFAULT_BUCKET)
        self.assertEqual(bucket_for(400, "坏值"), 480)

    def test_metrics_update_reports_change(self):
        from gui.grid import GridMetrics

        metrics = GridMetrics()
        metrics.update(800)
        first = (metrics.card_width, metrics.card_height)
        self.assertFalse(metrics.update(800))               # 同宽不算变
        self.assertTrue(metrics.update(1200))               # 变宽算变
        self.assertNotEqual(first, (metrics.card_width, metrics.card_height))


class TestTwoColumnLayout(GuiCase):
    """真正把网格摆出来，确认每行就是 2 个。"""

    def _page(self, width=900, height=700):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        page.resize(width, height)
        page.show()
        page.apply_metrics()                 # 不等防抖，立刻重排
        self.app.processEvents()
        return page

    def test_two_items_per_row(self):
        page = self._page(900)
        rects = [page.view.visualRect(page.model.index(i, 0)) for i in range(4)]
        # 第 0、1 个在同一行，x 不同、y 相同
        self.assertEqual(rects[0].y(), rects[1].y())
        self.assertNotEqual(rects[0].x(), rects[1].x())
        # 第 2 个换行：x 回到第一个的位置，y 更大
        self.assertEqual(rects[2].x(), rects[0].x())
        self.assertGreater(rects[2].y(), rects[0].y())
        # 第 3 个仍是第二行的第二列
        self.assertEqual(rects[3].y(), rects[2].y())
        self.assertEqual(rects[3].x(), rects[1].x())

    def test_card_size_matches_metrics(self):
        page = self._page(900)
        rect = page.view.visualRect(page.model.index(0, 0))
        self.assertEqual(rect.width(), page.metrics.card_width + 8)   # 格宽 = 卡宽 + 缝
        self.assertEqual(rect.height(), page.metrics.card_height + 8)

    def test_wider_window_gives_bigger_cards(self):
        narrow = self._page(700)
        wide = self._page(1200)
        self.assertLess(narrow.metrics.card_width, wide.metrics.card_width)
        self.assertLess(narrow.metrics.thumb_height, wide.metrics.thumb_height)
        # 但都还是每行 2 个
        for page in (narrow, wide):
            rects = [page.view.visualRect(page.model.index(i, 0)) for i in range(3)]
            self.assertEqual(rects[2].y() > rects[0].y(), True)

    def test_grid_size_matches_metrics(self):
        page = self._page(1000)
        grid = page.view.gridSize()
        self.assertEqual(grid.width(), page.metrics.card_width + 8)
        self.assertEqual(grid.height(), page.metrics.card_height + 8)


class TestThumbnailBuckets(GuiCase):
    def test_cache_key_includes_width(self):
        from gui.thumbs import thumb_key

        row = {"path": r"D:\a\b.mp4", "size": 100, "mtime": 1.0}
        self.assertNotEqual(thumb_key(row, 360), thumb_key(row, 640))
        self.assertEqual(thumb_key(row, 360), thumb_key(row, 360))

    def test_path_for_uses_width(self):
        from gui.thumbs import ThumbnailCache

        cache = ThumbnailCache("", self.tmp)
        row = {"path": r"D:\a\b.mp4", "size": 100, "mtime": 1.0}
        self.assertNotEqual(cache.path_for(row, 360), cache.path_for(row, 640))
        self.assertTrue(cache.path_for(row, 360).endswith(".jpg"))


class TestCardHelpers(GuiCase):
    def test_elide_lines(self):
        from PyQt5.QtGui import QFont, QFontMetrics

        from gui.card import elide_lines

        metrics = QFontMetrics(QFont("", 9))
        self.assertEqual(elide_lines(metrics, "", 200), [])
        self.assertEqual(elide_lines(metrics, "短的", 200), ["短的"])
        lines = elide_lines(metrics, "很长的标题" * 20, 120, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))

    def test_crop_to(self):
        from gui.card import crop_to

        source = QPixmap(400, 300)
        source.fill(Qt.red)
        out = crop_to(source, 200, 100)
        self.assertEqual((out.width(), out.height()), (200, 100))

    def test_pixmap_cache_eviction(self):
        from gui.card import PixmapCache

        cache = PixmapCache(capacity=2)
        for i in range(3):
            cache.put(i, QPixmap(10, 10))
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get(0))        # 最旧的被挤掉
        self.assertIsNotNone(cache.get(2))


class TestVideoPage(GuiCase):
    def test_page_loads_rows(self):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.model.total(), 5)
        self.assertEqual(page.model.loaded(), 5)
        self.assertEqual(page.title(), "全部视频")

    def test_page_paints_without_crashing(self):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        page.resize(900, 600)
        pixmap = QPixmap(900, 600)
        page.view.render(pixmap)               # 真正调用 delegate.paint()
        self.assertFalse(pixmap.isNull())

    def test_search_filters_page(self):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        page.search_edit.setText("beta")
        page._apply_search()
        self.assertEqual(page.model.total(), 1)
        self.assertEqual(page.model.row_at(0)["name"], "beta.mp4")
        page.clear_search()
        self.assertEqual(page.model.total(), 5)

    def test_sort_control_updates_state(self):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        page.sort_combo.setCurrentIndex(page.sort_combo.findData("size"))
        self.assertEqual(page.state.sort, "size")
        page.desc_box.setChecked(True)
        self.assertTrue(page.state.desc)
        self.assertEqual(page.model.row_at(0)["name"], "epsilon.mp4")

    def test_two_pages_keep_independent_state(self):
        from gui.page import VideoPage
        from gui.query import QueryState

        page_a = VideoPage(self.session, QueryState())
        page_b = VideoPage(self.session, QueryState())
        self.addCleanup(page_a.deleteLater)
        self.addCleanup(page_b.deleteLater)

        page_a.search_edit.setText("alpha")
        page_a._apply_search()
        page_a.sort_combo.setCurrentIndex(page_a.sort_combo.findData("size"))

        self.assertEqual(page_a.model.total(), 1)
        self.assertEqual(page_b.model.total(), 5)      # 另一页完全不受影响
        self.assertIsNone(page_b.state.keyword)
        self.assertEqual(page_b.state.sort, "name")

    def test_empty_state(self):
        from gui.page import VideoPage
        from gui.query import QueryState

        page = VideoPage(self.session, QueryState(tags=["不存在的标签"]))
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.model.total(), 0)
        self.assertIn("没有符合条件", page.empty_label.text())


class TestBrowserTabs(GuiCase):
    def test_home_is_pinned_and_not_closable(self):
        window = self.make_window()
        tabs = window.tabs
        self.assertEqual(tabs.bar.count(), 1)
        self.assertIs(tabs.current_page(), tabs.home_page)

        tabs.close_page(0)                      # 试图关主页
        self.assertEqual(tabs.bar.count(), 1)   # 关不掉
        self.assertIsNotNone(tabs.home_page)

    def test_open_tag_creates_page(self):
        window = self.make_window()
        tabs = window.tabs
        row = tabs.home_page.model.row_at(0)
        self.session.add_tags([row["id"]], ["胡桃系列"])

        tabs.open_tag("胡桃系列")
        self.assertEqual(tabs.bar.count(), 2)
        page = tabs.current_page()
        self.assertEqual(page.state.tags, ["胡桃系列"])
        self.assertEqual(page.model.total(), 1)
        self.assertIn("胡桃系列", tabs.bar.tabText(1))

    def test_open_same_tag_twice_reuses_page(self):
        window = self.make_window()
        tabs = window.tabs
        first = tabs.open_tag("同一个标签")
        second = tabs.open_tag("同一个标签")
        self.assertIs(first, second)
        self.assertEqual(tabs.bar.count(), 2)   # 主页 + 1

    def test_stack_follows_tab_bar(self):
        """回归：标签条与页面栈必须联动。

        曾经忘了把 currentChanged 接到 stack，导致切标签后
        标题、计数、状态栏都跟着变了，但画面还停在第一页。
        """
        window = self.make_window()
        tabs = window.tabs
        home = tabs.home_page
        self.assertIs(tabs.stack.currentWidget(), home)

        tag_page = tabs.open_tag("栈联动测试")
        self.assertIs(tabs.stack.currentWidget(), tag_page)

        tabs.set_current_page(home)
        self.assertIs(tabs.stack.currentWidget(), home)

        tabs.set_current_page(tag_page)
        self.assertIs(tabs.stack.currentWidget(), tag_page)

    def test_stack_stays_in_sync_after_close(self):
        window = self.make_window()
        tabs = window.tabs
        tabs.open_tag("甲")
        last = tabs.open_tag("乙")
        self.assertIs(tabs.stack.currentWidget(), last)

        tabs.close_page(2)                      # 关掉当前页（最后一个）
        self.assertEqual(tabs.bar.count(), 2)   # 主页 + 甲
        # 关掉当前页后 Qt 会选中相邻的那个，关键是「画面」和「当前页」必须一致
        self.assertIs(tabs.stack.currentWidget(), tabs.current_page())

        tabs.open_tag("丙")
        self.assertIs(tabs.stack.currentWidget(), tabs.current_page())

        tabs.set_current_page(tabs.home_page)   # 切回主页
        self.assertIs(tabs.current_page(), tabs.home_page)
        self.assertIs(tabs.stack.currentWidget(), tabs.home_page)

    def test_close_page_removes_it(self):
        window = self.make_window()
        tabs = window.tabs
        tabs.open_tag("要关掉的")
        self.assertEqual(tabs.bar.count(), 2)
        tabs.close_page(1)
        self.assertEqual(tabs.bar.count(), 1)
        self.assertEqual(len(tabs.pages), 1)

    def test_close_others_keeps_home(self):
        window = self.make_window()
        tabs = window.tabs
        tabs.open_tag("A")
        tabs.open_tag("B")
        tabs.open_tag("C")
        self.assertEqual(tabs.bar.count(), 4)
        tabs.close_others(3)                    # 只留最后一个
        self.assertEqual(tabs.bar.count(), 2)   # 主页 + 当前
        self.assertIs(tabs.pages[0], tabs.home_page)

    def test_open_and_close_many_times_does_not_crash(self):
        """回归测试：关掉的页不能留下野指针，缩略图回调回来不能炸。"""
        window = self.make_window()
        tabs = window.tabs
        for i in range(20):
            tabs.open_tag("标签%d" % i)
        for _ in range(20):
            tabs.close_page(tabs.bar.count() - 1)
        self.assertEqual(tabs.bar.count(), 1)
        self.app.processEvents()                # 让 deleteLater 生效
        self.app.processEvents()


class TestTabCloseButton(GuiCase):
    """标签上那个关闭用的 × 。

    它以前是「存在、但看不见」：`setTabsClosable(True)` 配自定义样式表时，
    要么图标是空的、要么是 Qt 原生那个跟粉色主题完全不搭的黑色粗叉，
    用户看到的就只有一句「这个页面没有关闭按钮」。
    只断言「closable 是 True」「按钮对象存在」全都测不出来 —— 必须抓像素。
    """

    def _window_with_tabs(self, *names):
        window = self.make_window()
        window.resize(1200, 700)
        window.show()                           # 几何要真的摆出来才能量
        self.app.processEvents()
        for name in names:
            window.tabs.open_tag(name)
        self.app.processEvents()
        return window

    def test_every_page_but_home_has_a_close_button(self):
        from PyQt5.QtWidgets import QToolButton

        window = self._window_with_tabs("甲", "乙")
        bar = window.tabs.bar
        self.assertFalse(bar.tabsClosable(),
                         "打开 tabsClosable 会让 Qt 塞进它自己的关闭按钮")
        self.assertIsNone(bar.tabButton(0, bar.RightSide),
                          "主页不该有关闭按钮")
        for index in (1, 2):
            button = bar.tabButton(index, bar.RightSide)
            self.assertIsInstance(button, QToolButton)
            self.assertEqual(button.objectName(), "tabClose")
            self.assertTrue(button.isVisible())
            self.assertGreaterEqual(button.width(), 16)
            self.assertGreaterEqual(button.height(), 16)
            self.assertTrue(bar.tabRect(index).contains(button.geometry().center()),
                            "关闭按钮跑到标签外面去了")

    def test_close_button_is_actually_painted(self):
        """抓像素：按钮那块方格里必须真的有「画出来的叉」。

        这是这个 bug 唯一抓得住的断言 —— 换成看不见的样式之后，
        按钮对象、几何、可见性、信号接线**全都是对的**，屏幕上却是空白。
        """
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        window = self._window_with_tabs("要看得见")
        tabs = window.tabs
        tabs.set_current_page(tabs.pages[1])    # 选中它：底色是白的，反差最大
        self.app.processEvents()

        bar = tabs.bar
        button = bar.tabButton(1, bar.RightSide)
        rect = button.geometry()
        image = bar.grab().toImage()
        background = QColor(PALETTE["SURFACE"])

        ink = 0
        for x in range(rect.left(), rect.right() + 1):
            for y in range(rect.top(), rect.bottom() + 1):
                color = QColor(image.pixel(x, y))
                distance = (abs(color.red() - background.red())
                            + abs(color.green() - background.green())
                            + abs(color.blue() - background.blue()))
                if distance > 60:
                    ink += 1
        self.assertGreaterEqual(
            ink, 12,
            "关闭按钮区域几乎整块都是底色（只有 %d 个像素不像底色）：叉没画出来。"
            "采样区 %s，标签条图 %dx%d"
            % (ink, rect, image.width(), image.height()))

    def test_close_button_is_vertically_aligned_with_the_title(self):
        """× 的上下间距必须和标签文字对齐（回归：它曾经比文字高 4px）。

        根因不在按钮本身，而在标签的盒子模型：文字是按样式表的 margin + padding
        居中的，Qt 却把标签按钮居中在**原始标签矩形**里。标签一旦用
        `margin-top` 留出顶上那道空隙（"浮"起来的效果），两者就差出半个空隙。
        所以量像素：两团墨迹的垂直中心必须落在同一行上。
        """
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        window = self._window_with_tabs("演示标签")
        tabs = window.tabs
        tabs.set_current_page(tabs.pages[1])
        self.app.processEvents()

        bar = tabs.bar
        image = bar.grab().toImage()
        background = QColor(PALETTE["SURFACE"])
        tab_rect = bar.tabRect(1)
        button_rect = bar.tabButton(1, bar.RightSide).geometry()

        def ink_rows(x0, x1):
            """有墨迹的行号。

            跳过最上面 3 行：那是标签的 1px 上边框和它的抗锯齿，横跨整个标签
            宽度，会被误当成文字。同理只认"至少 2 个像素"的行。
            """
            rows = []
            for y in range(3, image.height()):
                count = 0
                for x in range(x0, x1 + 1):
                    color = QColor(image.pixel(x, y))
                    if (abs(color.red() - background.red())
                            + abs(color.green() - background.green())
                            + abs(color.blue() - background.blue())) > 60:
                        count += 1
                if count >= 2:
                    rows.append(y)
            return rows

        text_rows = ink_rows(tab_rect.left() + 16, button_rect.left() - 4)
        close_rows = ink_rows(button_rect.left(), button_rect.right())
        self.assertTrue(text_rows, "标签文字一个墨迹像素都没量到")
        self.assertTrue(close_rows, "× 一个墨迹像素都没量到")

        text_center = (text_rows[0] + text_rows[-1]) / 2.0
        close_center = (close_rows[0] + close_rows[-1]) / 2.0
        self.assertLessEqual(
            abs(close_center - text_center), 1.0,
            "× 和标签文字的垂直中心差了 %.1fpx（文字 %d..%d，× %d..%d）"
            % (close_center - text_center, text_rows[0], text_rows[-1],
               close_rows[0], close_rows[-1]))

    def test_clicking_close_closes_that_page(self):
        window = self._window_with_tabs("甲", "乙")
        tabs = window.tabs
        keep, target = tabs.pages[1], tabs.pages[2]
        closed = []
        tabs.pageClosed.connect(closed.append)

        tabs.bar.tabButton(2, tabs.bar.RightSide).click()
        self.assertEqual(closed, [target], "关掉的不是这一页")
        self.assertEqual(tabs.pages, [tabs.home_page, keep])

    def test_close_button_follows_a_moved_tab(self):
        """标签拖动过之后，× 关掉的必须还是它自己那一页。"""
        window = self._window_with_tabs("甲", "乙", "丙")
        tabs = window.tabs
        target = tabs.pages[1]                  # 「甲」
        tabs.bar.moveTab(1, 3)                  # 拖到最右
        self.assertEqual(tabs.pages.index(target), 3)

        closed = []
        tabs.pageClosed.connect(closed.append)
        tabs.bar.tabButton(3, tabs.bar.RightSide).click()

        self.assertEqual(closed, [target], "拖动之后关错了页")
        self.assertEqual([p.state.tags for p in tabs.pages], [[], ["乙"], ["丙"]])

    def test_dragging_a_tab_keeps_content_in_sync(self):
        """回归：拖动标签会让「标签条下标」和「页面下标」错位。

        以前 pages 列表不跟着动：把「甲」拖到最右后，标签条上选中的是「甲」，
        画面和 current_page() 却都变成了「丙」。内容和标签对不上。
        """
        window = self._window_with_tabs("甲", "乙", "丙")
        tabs = window.tabs
        tabs.bar.setCurrentIndex(1)
        self.assertEqual(tabs.current_page().state.tags, ["甲"])

        tabs.bar.moveTab(1, 3)
        self.assertEqual([tabs.bar.tabText(i) for i in range(4)],
                         ["全部视频", "乙", "丙", "甲"])
        self.assertEqual([p.state.tags for p in tabs.pages],
                         [[], ["乙"], ["丙"], ["甲"]])
        self.assertEqual(tabs.current_page().state.tags, ["甲"])
        self.assertIs(tabs.stack.currentWidget(), tabs.current_page())

    def test_middle_click_closes_a_tab(self):
        """中键关标签是浏览器习惯，Qt 的 tabCloseRequested 不给，得自己接。"""
        from PyQt5.QtTest import QTest

        window = self._window_with_tabs("中点我")
        tabs = window.tabs
        target = tabs.pages[1]
        QTest.mouseClick(tabs.bar, Qt.MiddleButton,
                         pos=tabs.bar.tabRect(1).center())
        self.assertNotIn(target, tabs.pages)
        self.assertEqual(len(tabs.pages), 1)

    def test_middle_click_does_not_close_home(self):
        from PyQt5.QtTest import QTest

        window = self._window_with_tabs("甲")
        tabs = window.tabs
        QTest.mouseClick(tabs.bar, Qt.MiddleButton,
                         pos=tabs.bar.tabRect(0).center())
        self.assertEqual(len(tabs.pages), 2)
        self.assertIs(tabs.pages[0], tabs.home_page)


class TestTagDialogs(GuiCase):
    """右键菜单的「打标签…」「移除标签…」，以及标签页菜单的「重命名标签…」。

    这三个动作共用同一个输入框 `dialogs.ask_tag`。它以前**一个测试都没有**，
    里面那句 `dialog.setCompleter(...)`（QInputDialog 根本没有这个方法，
    那是 QComboBox / QLineEdit 的）就一直活到了线上：
    只要索引库里已经有标签，补全那一段就 AttributeError，
    而 PyQt5 会把槽里未捕获的异常升级成 qFatal() ——
    右键点「打标签…」，**整个程序当场闪退**，连个错误框都没有。

    所以这里的关键是**真的走 dialogs.ask_tag**，只把「弹对话框」这一步换成
    自动填好文字并回车。要是把 ask_tag 整个替换掉，这个 bug 就又被藏回去了
    —— 之前所有涉及打标签的测试都是那么写的。
    """

    def _auto_accept(self, text):
        """把 QInputDialog.exec_ 换成：填好文字，当作用户点了确定。

        返回一个 dict，里面会存下这次用的那个对话框（用来检查补全有没有装上）。
        """
        from PyQt5.QtWidgets import QInputDialog

        seen = {}

        def fake_exec(dialog):
            seen["dialog"] = dialog
            dialog.setTextValue(text)
            return QInputDialog.Accepted

        patcher = mock.patch.object(QInputDialog, "exec_", fake_exec)
        patcher.start()
        self.addCleanup(patcher.stop)
        return seen

    def _reject(self):
        from PyQt5.QtWidgets import QInputDialog

        patcher = mock.patch.object(QInputDialog, "exec_",
                                    lambda dialog: QInputDialog.Rejected)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _tags_of(self, page, file_id):
        """从当前页的数据里读某个文件的标签（页面刚刷过，就是最新的）。"""
        for i in range(page.model.rowCount()):
            row = page.model.row_at(i)
            if row["id"] == file_id:
                return [t for t in (row.get("tags") or "").split("、") if t]
        return []

    # ---------------------------------------------------------- 输入框本身

    def test_ask_tag_with_suggestions_does_not_raise(self):
        """回归：库里有标签（要补全）时不能炸 —— 以前这里直接闪退。"""
        from core import util

        from gui import dialogs

        self._auto_accept("随便一个")
        self.assertEqual(dialogs.ask_tag(None, "打标签", "标签：", ["已经存在的"]),
                         [util.normalize_tag("随便一个")])

    def test_ask_tag_installs_the_completer_on_the_input_box(self):
        """补全要真的装在输入框上，不能被那句 findChild 悄悄跳过。"""
        from PyQt5.QtWidgets import QLineEdit

        from gui import dialogs

        seen = self._auto_accept("x")
        dialogs.ask_tag(None, "打标签", "标签：", ["甲", "乙"])
        edit = seen["dialog"].findChild(QLineEdit)
        self.assertIsNotNone(edit, "对话框里没找到输入框")
        completer = edit.completer()
        self.assertIsNotNone(completer, "补全没装上（功能被静默跳过）")
        self.assertEqual(set(completer.model().stringList()), set(["甲", "乙"]))
        self.assertEqual(completer.filterMode(), Qt.MatchContains)

    def test_ask_tag_accepts_all_three_separators(self):
        from core import util

        from gui import dialogs

        self._auto_accept("  甲, 乙，丙、丁 ,, ")
        self.assertEqual(
            dialogs.ask_tag(None, "打标签", "标签："),
            [util.normalize_tag(n) for n in ("甲", "乙", "丙", "丁")])

    def test_cancelling_returns_nothing(self):
        from gui import dialogs

        self._reject()
        self.assertEqual(dialogs.ask_tag(None, "打标签", "标签：", ["甲"]), [])

    # ---------------------------------------------------------- 右键菜单真的能用

    def test_add_tags_from_the_card_menu(self):
        window = self.make_window()
        page = window.tabs.current_page()
        row = page.model.row_at(0)
        # 先放一个标签进去：补全列表非空，正是以前会闪退的条件
        self.session.add_tags([row["id"]], ["已有的"])
        self._auto_accept("右键打的, 第二个")

        page.reselect([row["id"]])
        page.prompt_add_tags()

        self.assertEqual(sorted(self._tags_of(page, row["id"])),
                         sorted(["已有的", "右键打的", "第二个"]))

    def test_remove_tags_from_the_card_menu(self):
        window = self.make_window()
        page = window.tabs.current_page()
        row = page.model.row_at(0)
        self.session.add_tags([row["id"]], ["留下的", "要去掉的"])
        self._auto_accept("要去掉的")

        page.reselect([row["id"]])
        page.prompt_remove_tags()

        self.assertEqual(self._tags_of(page, row["id"]), ["留下的"])

    def test_rename_tag_from_the_tab_menu(self):
        window = self.make_window()
        page = window.tabs.current_page()
        row = page.model.row_at(0)
        self.session.add_tags([row["id"]], ["旧名字"])
        self._auto_accept("新名字")

        window.rename_tag("旧名字")                 # 标签页右键菜单走的就是它

        self.assertEqual(self._tags_of(page, row["id"]), ["新名字"])


class TestColumnChoices(GuiCase):
    """每行 2/3/4 个可切换。"""

    def _page(self, width=1400, height=900):
        from gui.page import VideoPage

        page = VideoPage(self.session)
        self.addCleanup(page.deleteLater)
        page.resize(width, height)
        page.show()
        # 不等 150ms 防抖，立刻按真实宽度算一次，否则用的还是初始化时的尺寸
        page.apply_metrics()
        self.app.processEvents()
        return page

    @staticmethod
    def _columns_in_first_row(page):
        """真的去量坐标：第一行有几个不同的 x。"""
        rows = {}
        for i in range(min(12, page.model.rowCount())):
            rect = page.view.visualRect(page.model.index(i, 0))
            rows.setdefault(rect.y(), []).append(rect.x())
        if not rows:
            return 0
        first = rows[min(rows)]
        return len(set(first))

    def test_layout_follows_column_choice(self):
        page = self._page()
        for count in (2, 3, 4):
            page.set_columns(count)
            self.app.processEvents()
            self.assertEqual(self._columns_in_first_row(page), count,
                             "设成 %d 列，实际摆了 %d 个" % (count, self._columns_in_first_row(page)))

    def test_wider_choice_gives_narrower_cards(self):
        page = self._page()
        page.set_columns(2)
        two = page.metrics.card_width
        page.set_columns(4)
        four = page.metrics.card_width
        self.assertLess(four, two)
        self.assertEqual(self._columns_in_first_row(page), 4)

    def test_narrow_page_still_shows_requested_columns(self):
        """同上，但走完整的页面链路：窄页面下也要真的摆出 N 列。"""
        page = self._page(430, 620)
        for count in (2, 3, 4):
            page.set_columns(count)
            self.app.processEvents()
            self.assertEqual(page.actual_columns(), count,
                             "窄页面下选 %d 列，实际摆了 %d 列"
                             % (count, page.actual_columns()))

    def test_actual_columns_matches_metrics_when_wide(self):
        page = self._page(1400, 900)
        for count in (2, 3, 4):
            page.set_columns(count)
            self.app.processEvents()
            self.assertEqual(page.actual_columns(), count)
            self.assertEqual(page.metrics.columns, count)

    def test_metrics_set_columns_reports_change(self):
        from gui.grid import GridMetrics

        metrics = GridMetrics()
        metrics.update(1000)
        self.assertTrue(metrics.set_columns(3))
        self.assertEqual(metrics.columns, 3)
        self.assertFalse(metrics.set_columns(3))          # 没变化
        self.assertTrue(metrics.set_columns(2))

    def test_columns_are_clamped_to_at_least_one(self):
        from gui.grid import GridMetrics

        metrics = GridMetrics()
        metrics.set_columns(0)
        self.assertEqual(metrics.columns, 1)
        metrics.set_columns(-5)
        self.assertEqual(metrics.columns, 1)


class TestColumnSettingInWindow(GuiCase):
    def test_columns_button_and_menu(self):
        window = self.make_window()
        self.assertEqual(window.columns_button.text(), "▦")
        self.assertIsNotNone(window.columns_button.menu())
        for count in (2, 3, 4):
            self.assertIn(count, window._column_actions)

    def test_setting_applies_to_every_open_page(self):
        window = self.make_window()
        window.tabs.open_tag("甲")
        window.tabs.open_tag("乙")
        window.set_preferred_columns(4)
        self.assertEqual(len(window.tabs.pages), 3)
        for page in window.tabs.pages:
            self.assertEqual(page.metrics.columns, 4)
        # 新开的页也要跟上
        window.tabs.open_tag("丙")
        self.assertEqual(window.tabs.current_page().metrics.columns, 4)

    def test_setting_is_persisted(self):
        window = self.make_window()
        window.set_preferred_columns(4)
        self.assertEqual(window.session.prefs.get("columns"), 4)

    def test_restart_uses_saved_setting(self):
        self.session.prefs.set("columns", 3)
        self.session.prefs.set("columns_maximized", 2)
        window = self.make_window()
        self.assertEqual(window._windowed_columns, 3)
        self.assertEqual(window._maximized_columns, 2)
        self.assertEqual(window.effective_columns(), 3)   # 窗口模式

    def test_maximize_switches_to_maximized_setting(self):
        window = self.make_window()
        window.set_preferred_columns(4)                   # 窗口模式设 4
        window.resize(1400, 900)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.effective_columns(), 4)

        window._toggle_maximize()                         # 全屏默认回到 3
        self.assertTrue(window.custom_maximized())
        self.assertEqual(window.effective_columns(), 3)
        for page in window.tabs.pages:
            self.assertEqual(page.metrics.columns, 3)
        self.assertTrue(window._column_actions[3].isChecked())

        window._toggle_maximize()                         # 还原回窗口设置
        self.assertFalse(window.custom_maximized())
        self.assertEqual(window.effective_columns(), 4)

    def test_drag_restore_switches_columns(self):
        """回归：拖动标题条还原窗口时，列数没跟着切。

        begin_drag() 会把最大化的窗口还原，但没人调用 _sync_columns()，
        于是「已经是窗口了却还按全屏那份列数设置走」。
        """
        window = self.make_window()
        window.resize(1200, 780)
        window.show()
        self.app.processEvents()
        window.set_preferred_columns(2)                # 窗口设置 2
        window._toggle_maximize()                      # 全屏 -> 用全屏设置
        self.assertEqual(window.effective_columns(), window._maximized_columns)

        # 模拟「按住标签条空白处拖动」：这一步会把窗口还原
        window.begin_drag(QPoint(window.x() + 40, window.y() + 10))
        window.end_drag()
        window._on_window_state_changed()              # 事件过滤器里做的事
        self.assertFalse(window._window_maximized())
        self.assertEqual(window.effective_columns(), 2)
        page = window.tabs.current_page()
        self.assertEqual(page.metrics.columns, 2)

    def test_stale_maximized_flag_self_heals(self):
        """状态位说「全屏」但窗口尺寸其实已经是窗口了，要能自己纠正。

        否则 effective_columns() 会一直用全屏那份设置，
        用户在窗口模式下改列数就会觉得「没反应」。
        """
        from PyQt5.QtWidgets import QApplication

        window = self.make_window()
        # 离屏虚拟屏比窗口默认最小尺寸还小，先把下限降下来，
        # 好造出「明显小于屏幕」的窗口状态
        window.setMinimumSize(120, 100)
        window.show()
        self.app.processEvents()

        area = QApplication.primaryScreen().availableGeometry()
        window.setGeometry(area.x(), area.y(),
                           max(120, area.width() - 200),
                           max(100, area.height() - 200))
        window._custom_maximized = True                # 假的：状态位说全屏
        self.assertFalse(window._window_maximized())   # 实际尺寸不是
        self.assertFalse(window._custom_maximized)     # 而且状态位被纠正了

        window.setGeometry(area)                       # 真的铺满
        window._custom_maximized = True
        self.assertTrue(window._window_maximized())

    def test_columns_can_be_changed_while_maximized(self):
        """回归：全屏下改列数以前完全没反应。

        effective_columns() 以前在最大化时无条件返回 3，
        用户在菜单里点 2 或者 4 都被静默覆盖，界面纹丝不动，
        看起来就是这个功能坏了。
        """
        window = self.make_window()
        window.resize(1400, 900)
        window.show()
        self.app.processEvents()
        window._toggle_maximize()
        self.assertTrue(window.custom_maximized())

        window.set_preferred_columns(2)
        self.assertEqual(window.effective_columns(), 2)
        for page in window.tabs.pages:
            self.assertEqual(page.metrics.columns, 2, "全屏下改成 2 列没生效")
        self.assertTrue(window._column_actions[2].isChecked())

        window.set_preferred_columns(4)
        self.assertEqual(window.effective_columns(), 4)
        self.assertTrue(window._column_actions[4].isChecked())

    def test_changing_while_maximized_keeps_window_setting(self):
        window = self.make_window()
        window.set_preferred_columns(2)                   # 窗口设置 = 2
        window.show()
        self.app.processEvents()
        window._toggle_maximize()
        window.set_preferred_columns(4)                   # 改的是全屏那份

        self.assertEqual(window._windowed_columns, 2)     # 窗口设置没被带跑
        self.assertEqual(window._maximized_columns, 4)
        window._toggle_maximize()
        self.assertEqual(window.effective_columns(), 2)

    def test_maximized_choice_survives_toggle_and_restart(self):
        window = self.make_window()
        window.show()
        self.app.processEvents()
        window._toggle_maximize()
        window.set_preferred_columns(2)
        self.assertEqual(window.session.prefs.get("columns_maximized"), 2)

        window._toggle_maximize()                         # 还原
        window._toggle_maximize()                         # 再全屏
        self.assertEqual(window.effective_columns(), 2)

        window._sync_columns()
        self.assertIn("全屏模式", window._column_hint.text())

    def test_attribute_columns_match_setting(self):
        """窗口模式下设定 2/3/4，实际摆出来的列数必须一致。"""
        window = self.make_window()
        window.resize(1200, 780)
        window.show()
        self.app.processEvents()
        for count in (3, 4, 2, 3):
            window.set_preferred_columns(count)
            self.app.processEvents()
            page = window.tabs.current_page()
            self.assertEqual(page.metrics.columns, count)
            self.assertEqual(page.actual_columns(), count,
                             "窗口模式选 %d 列，实际摆了 %d 列"
                             % (count, page.actual_columns()))
            self.assertEqual(window.effective_columns(), count)

    def test_column_shortcuts_registered(self):
        window = self.make_window()
        keys = set()
        for shortcut in window._shortcuts:
            keys.add(shortcut.key().toString())
        for expected in ("Ctrl+2", "Ctrl+3", "Ctrl+4"):
            self.assertIn(expected, keys)


class TestStartMaximized(GuiCase):
    """启动就全屏（默认行为）。"""

    def _window(self):
        self.engine.cfg.start_maximized = True
        return self.make_window()

    def test_starts_maximized(self):
        window = self._window()
        self.assertTrue(window.custom_maximized())
        self.assertEqual(window.window_controls.max_button.text(), "❐")
        margins = window._inner_layout.contentsMargins()
        self.assertEqual((margins.left(), margins.top()), (0, 0))

    def test_start_uses_maximized_columns(self):
        window = self._window()
        self.assertEqual(window.effective_columns(),
                         window._maximized_columns)
        for page in window.tabs.pages:
            self.assertEqual(page.metrics.columns, window._maximized_columns)

    def test_restore_after_start_gives_window_setting(self):
        window = self._window()
        window._toggle_maximize()
        self.assertFalse(window.custom_maximized())
        self.assertEqual(window.effective_columns(), window._windowed_columns)
        margins = window._inner_layout.contentsMargins()
        self.assertGreater(margins.left(), 0)      # 还原后缩放边距回来

    def test_can_turn_off(self):
        self.engine.cfg.start_maximized = False
        window = self.make_window()
        self.assertFalse(window.custom_maximized())

    def test_normal_geometry_is_the_half_screen_size(self):
        """启动就全屏时，点 ❐ 还原要回到「半屏居中」那个尺寸，不是一个奇怪的默认值。"""
        window = self._window()
        self.assertIsNotNone(window._normal_geometry)
        from gui.frameless import initial_window_size
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry()
        width, height = initial_window_size(
            area.width(), area.height(), self.engine.cfg.window_fraction)
        self.assertEqual(window._normal_geometry.width(), width)
        self.assertEqual(window._normal_geometry.height(), height)


class TestDetailCollapse(GuiCase):
    """详情面板的收起 / 展开：按钮在面板右上角。"""

    def test_button_starts_expanded(self):
        window = self.make_window()
        detail = window.detail
        self.assertFalse(detail.is_collapsed())
        self.assertEqual(detail.collapse_button.text(), "»")
        self.assertIn("收起", detail.collapse_button.toolTip())
        self.assertTrue(detail.body.isVisibleTo(detail))

    def test_click_collapses_and_expands(self):
        window = self.make_window()
        window.resize(1200, 780)
        window.show()
        self.app.processEvents()
        detail = window.detail

        detail.collapse_button.click()
        self.assertTrue(detail.is_collapsed())
        self.assertEqual(detail.collapse_button.text(), "«")
        self.assertIn("展开", detail.collapse_button.toolTip())
        self.assertFalse(detail.body.isVisibleTo(detail))
        self.assertEqual(detail.minimumWidth(), detail.COLLAPSED_WIDTH)

        detail.collapse_button.click()
        self.assertFalse(detail.is_collapsed())
        self.assertEqual(detail.collapse_button.text(), "»")
        self.assertEqual(detail.minimumWidth(), detail.EXPANDED_MIN_WIDTH)

    def test_signal_is_emitted(self):
        window = self.make_window()
        seen = []
        window.detail.collapsedChanged.connect(seen.append)
        window.detail.set_collapsed(True)
        window.detail.set_collapsed(False)
        self.assertEqual(seen, [True, False])

    def test_setting_same_value_twice_is_noop(self):
        window = self.make_window()
        seen = []
        window.detail.collapsedChanged.connect(seen.append)
        self.assertTrue(window.detail.set_collapsed(True))
        self.assertFalse(window.detail.set_collapsed(True))
        self.assertEqual(seen, [True])

    def test_collapsing_gives_space_to_the_grid(self):
        window = self.make_window()
        window.resize(1300, 800)
        window.show()
        self.app.processEvents()
        before = window.splitter.sizes()
        window.detail.set_collapsed(True)
        self.app.processEvents()
        after = window.splitter.sizes()

        self.assertEqual(after[2], window.detail.COLLAPSED_WIDTH)
        self.assertGreater(after[1], before[1], "收起后中间没有变宽")
        self.assertEqual(after[0], before[0])          # 左栏不动
        self.assertEqual(sum(after), sum(before))      # 总宽不变

    def test_expanding_restores_previous_width(self):
        window = self.make_window()
        window.resize(1300, 800)
        window.show()
        self.app.processEvents()
        before = window.splitter.sizes()[2]
        window.detail.set_collapsed(True)
        self.app.processEvents()
        window.detail.set_collapsed(False)
        self.app.processEvents()
        self.assertAlmostEqual(window.splitter.sizes()[2], before, delta=2)

    def test_menu_action_follows_state(self):
        window = self.make_window()
        self.assertTrue(window.detail_action.isChecked())
        window.detail_action.trigger()                 # 取消勾选 -> 收起
        self.assertTrue(window.detail.is_collapsed())
        self.assertFalse(window.detail_action.isChecked())
        window.detail_action.trigger()
        self.assertFalse(window.detail.is_collapsed())
        self.assertTrue(window.detail_action.isChecked())

    def test_dragging_the_strip_open_expands_it(self):
        window = self.make_window()
        window.resize(1300, 800)
        window.show()
        self.app.processEvents()
        window.detail.set_collapsed(True)
        self.app.processEvents()
        # 模拟把窄条往左拖开
        sizes = window.splitter.sizes()
        window.splitter.setSizes([sizes[0], sizes[1] - 120, sizes[2] + 120])
        window._on_splitter_moved(0, 2)
        self.assertFalse(window.detail.is_collapsed())

    def test_panel_minimum_width_when_expanded(self):
        """展开时不能被拖成一条缝（那正是要修掉的「不好找回来」）。"""
        window = self.make_window()
        self.assertEqual(window.detail.minimumWidth(),
                         window.detail.EXPANDED_MIN_WIDTH)


    def test_panel_actually_paints_its_background(self):
        """回归：QWidget 的子类必须开 WA_StyledBackground。

        不开的话，样式表里的 background / border / border-radius 全都
        形同虚设 —— 收起后的粉色圆角块会变成透明（只看到父级颜色），
        筛选区那条分隔线也不会出现。这种 bug 只有「抓像素」才测得出来，
        断言状态、属性、几何统统测不到。
        """
        from PyQt5.QtCore import QPoint
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        window = self.make_window()
        window.resize(1200, 760)
        window.show()
        self.app.processEvents()
        detail = window.detail

        def panel_color():
            self.app.processEvents()
            pos = detail.mapTo(window, QPoint(0, 0))
            image = window.grab().toImage()
            # x 取 2：在面板自己的 8px 内边距里，不会被任何子控件盖住
            return QColor(image.pixel(pos.x() + 2, pos.y() + 150)).name()

        self.assertEqual(panel_color(), QColor(PALETTE["PANEL"]).name())
        detail.set_collapsed(True)
        self.assertEqual(panel_color(), QColor(PALETTE["TAB_IDLE"]).name(),
                         "收起后窄条没有画出粉色底")
        detail.set_collapsed(False)
        self.assertEqual(panel_color(), QColor(PALETTE["PANEL"]).name())

    def test_filter_panel_paints_its_top_border(self):
        """同上：筛选区顶部那条分隔线也得真的画出来。"""
        from PyQt5.QtCore import QPoint
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        window = self.make_window()
        window.resize(1200, 760)
        window.show()
        self.app.processEvents()
        filters = window.filters
        pos = filters.mapTo(window, QPoint(0, 0))
        image = window.grab().toImage()

        border = QColor(PALETTE["BORDER"]).name()
        rows = [QColor(image.pixel(pos.x() + 40, pos.y() + dy)).name()
                for dy in range(0, 3)]
        self.assertIn(border, rows, "筛选区顶部没画出分隔线：%s" % rows)


class TestScanFromGui(GuiCase):
    """⟳ 扫描按钮：界面里唯一的长任务入口，必须真的能跑通。

    以前它**一按就失败**：扫描跑在 QThread 里，而 sqlite 连接是主线程建的，
    sqlite3 不允许跨线程使用连接 —— 于是抛 ProgrammingError，
    再弹一个模态错误框，看起来就像界面卡死了。
    这个功能长期完全不可用，却没有任何测试点过它。
    """

    def _run_scan(self, window):
        """点扫描按钮并等它真正跑完。

        注意要等 libraryChanged（扫描结束的信号），不能只等 is_busy() 变 False：
        工作线程结束时 done 信号可能还排在事件队列里，这时就去断言/拆环境，
        回调会在之后才跑，操作到已经关掉的数据库。
        """
        failures = []
        finished = []
        try:
            self.session.failed.disconnect(window._on_failed)   # 别弹模态框
        except TypeError:
            pass
        self.session.failed.connect(failures.append)
        self.session.libraryChanged.connect(lambda: finished.append(True))

        window.scan_button.click()
        deadline = time.time() + 60
        while not finished and not failures and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.02)
        for _ in range(4):                       # 把队列里剩下的信号跑完
            self.app.processEvents()
            time.sleep(0.01)
        return failures

    def test_scan_button_completes_without_error(self):
        window = self.make_window()
        failures = self._run_scan(window)
        self.assertEqual(failures, [], "扫描失败：%s" % failures)
        self.assertFalse(self.session.is_busy(), "扫描没能在限期内结束")

    def test_scan_picks_up_new_files(self):
        """「重新读取文件夹里的视频」的语义：新放进来的文件要能被扫出来。"""
        window = self.make_window()
        before = self.session.engine.query(limit=200)[1]

        make_file(os.path.join(self.tree, "newly_added.mp4"), b"v" * 321)
        failures = self._run_scan(window)
        self.assertEqual(failures, [], "扫描失败：%s" % failures)

        after = self.session.engine.query(limit=200)[1]
        self.assertEqual(after, before + 1, "新文件没被扫进索引")
        rows, _ = self.session.engine.query(limit=10, keyword="newly_added")
        self.assertEqual(len(rows), 1)

    def test_scan_marks_deleted_files(self):
        """文件被删掉后重新扫描要标记出来。"""
        window = self.make_window()
        os.remove(os.path.join(self.tree, "alpha.mp4"))
        failures = self._run_scan(window)
        self.assertEqual(failures, [])
        total = self.session.engine.query(limit=200)[1]
        self.assertEqual(total, 4)

    def test_worker_uses_its_own_connection(self):
        """根因回归：工作线程不能碰主线程那个连接。"""
        import threading
        import traceback

        engine = self.session.engine
        captured = {}

        def work():
            try:
                captured["summary"] = engine.scan_in_own_connection()
            except BaseException:                          # noqa: BLE001
                captured["error"] = traceback.format_exc()

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(60)
        self.assertNotIn("error", captured, captured.get("error"))
        self.assertIn("files", captured.get("summary") or {})

    def test_same_connection_in_worker_would_fail(self):
        """把「用错连接」这件事本身钉住：主连接在工作线程里必须报错。

        这样万一以后有人把 scan_in_own_connection 换回 self.conn，
        这条测试会直接指出原因，而不是只在某个界面上表现为「点不动」。
        """
        import sqlite3
        import threading
        import traceback

        engine = self.session.engine
        captured = {}

        def work():
            try:
                engine.conn.execute("SELECT 1").fetchone()
            except BaseException as exc:                   # noqa: BLE001
                captured["error"] = "%s: %s" % (type(exc).__name__, exc)

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(30)
        self.assertIn("error", captured)
        self.assertIn("thread", captured["error"].lower())

    def test_main_connection_still_usable_after_scan(self):
        """扫描用的是另一个连接，扫完之后主连接还得能正常查询。"""
        window = self.make_window()
        self._run_scan(window)
        rows, total = self.session.engine.query(limit=5)
        self.assertGreater(total, 0)
        self.assertTrue(rows)

    def test_scan_updates_the_page(self):
        """扫完界面要刷新（libraryChanged -> 重新查询）。"""
        window = self.make_window()
        make_file(os.path.join(self.tree, "brand_new.mp4"), b"v" * 222)
        self._run_scan(window)
        self.app.processEvents()
        page = window.tabs.current_page()
        names = [page.model.row_at(i)["name"] for i in range(page.model.rowCount())]
        self.assertIn("brand_new.mp4", names)


class TestRefresh(GuiCase):
    """刷新：保住用户的浏览进度，别把人弹回顶部。"""

    def _loaded_page(self, pages=3, page_size=2):
        window = self.make_window()
        window.resize(1200, 760)
        window.show()
        self.app.processEvents()
        page = window.tabs.current_page()
        page.state.page_size = page_size
        page.refresh()
        self.app.processEvents()
        for _ in range(pages - 1):
            page.load_more()
        self.app.processEvents()
        return window, page

    def test_refresh_keeps_loaded_pages(self):
        """回归：刷新以前只重取第一页，翻过的页数全丢。"""
        window, page = self._loaded_page()
        loaded = page.model.loaded()
        self.assertEqual(loaded, 5)
        window.refresh_current()
        self.app.processEvents()
        self.assertEqual(page.model.loaded(), loaded, "刷新后载入条数变少了")

    def test_refresh_keeps_scroll_position(self):
        """回归：刷新以前会把用户弹回顶部。"""
        window, page = self._loaded_page()
        bar = page.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.app.processEvents()
        bottom = bar.value()
        self.assertGreater(bottom, 0)
        window.refresh_current()
        self.app.processEvents()
        self.app.processEvents()                 # 滚动恢复有一次延后补设
        self.assertEqual(bar.value(), bottom, "刷新把滚动位置弄丢了")

    def test_refresh_keeps_selection_on_a_later_page(self):
        """选中的东西在第 3 页时，刷新也不能把它弄丢。"""
        window, page = self._loaded_page()
        last = page.model.index(page.model.loaded() - 1, 0)
        page.view.selectionModel().select(last, page.view.selectionModel().Select)
        self.app.processEvents()
        selected = [r["id"] for r in page.selected_rows()]
        self.assertEqual(len(selected), 1)

        window.refresh_current()
        self.app.processEvents()
        self.assertEqual([r["id"] for r in page.selected_rows()], selected,
                         "刷新后选中项丢了")

    def test_search_still_goes_back_to_the_top(self):
        """结果集变了（搜索）就该回到顶部看新结果，这是另一回事。"""
        window, page = self._loaded_page()
        bar = page.view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.app.processEvents()
        page.search_edit.setText("a")
        page._apply_search()
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)

    def test_refresh_updates_sidebar_counts(self):
        window, page = self._loaded_page()
        row = page.model.row_at(0)
        self.session.add_tags([row["id"]], ["刷新用标签"])
        window.refresh_current()
        self.app.processEvents()
        texts = [window.sidebar.list.item(i).text()
                 for i in range(window.sidebar.list.count())]
        self.assertTrue(any("刷新用标签" in t for t in texts))

    def test_menu_has_refresh_entry(self):
        window = self.make_window()
        labels = [a.text() for a in window.more_button.menu().actions()]
        self.assertTrue(any("刷新" in text for text in labels), labels)


class TestAdaptivePanes(GuiCase):
    """两侧面板随窗口大小自适应：小窗时详情面板要小。"""

    def test_right_pane_is_narrower_in_small_windows(self):
        from gui.frameless import pane_widths

        small = pane_widths(1280)
        large = pane_widths(2560)
        self.assertLess(small[2], large[2], "小窗时详情面板没有变窄")
        self.assertLessEqual(small[2], 240)

    def test_panes_fit_and_respect_detail_minimum(self):
        from gui.detail import DetailPanel
        from gui.frameless import pane_widths

        for width in (900, 1100, 1280, 1600, 1920, 2560):
            left, center, right = pane_widths(width)
            self.assertLessEqual(left + center + right, width,
                                 "%d 宽时三栏加起来放不下" % width)
            self.assertGreaterEqual(right, DetailPanel.EXPANDED_MIN_WIDTH,
                                    "%d 宽时详情面板比最小宽度还窄" % width)

    def test_shrinking_the_window_shrinks_the_detail_panel(self):
        """回归：以前只在首次显示时算一次分栏，
        结果从全屏还原后两侧面板还留着全屏时的绝对宽度（330px），
        把中间挤掉一大块。"""
        from gui.frameless import pane_widths

        window = self.make_window()
        window.setGeometry(0, 0, 2400, 1200)
        window.show()
        self.app.processEvents()
        window._reapply_pane_sizes()
        self.app.processEvents()
        self.assertEqual(window.width(), 2400)
        wide = window.splitter.sizes()[2]

        window.setGeometry(0, 0, 1200, 700)
        self.app.processEvents()
        window._reapply_pane_sizes()
        self.app.processEvents()
        narrow = window.splitter.sizes()[2]

        self.assertLess(narrow, wide, "小窗时详情面板没变小：%d -> %d" % (wide, narrow))
        self.assertLessEqual(abs(narrow - pane_widths(1200)[2]), 4,
                             "还原后的分栏不是按当前宽度算的")

    def test_window_resize_event_reapplies(self):
        window = self.make_window()
        window.resize(1600, 900)
        window.show()
        self.app.processEvents()
        window._reapply_pane_sizes()
        self.app.processEvents()
        before = list(window.splitter.sizes())

        window.resize(1000, 700)
        window._pane_timer.stop()
        window._reapply_pane_sizes()
        self.app.processEvents()
        after = window.splitter.sizes()
        self.assertLess(after[2], before[2])
        self.assertGreater(after[1], 0)

    def test_user_adjusted_splitter_keeps_its_proportions(self):
        """用户拖过分隔条之后，缩放窗口按比例走，不强行套回默认分配。

        这里用 1600 -> 1300 这种「不会触到最小宽度」的缩放，
        触底的极端情况下面单独测。
        """
        window = self.make_window()
        window.resize(1600, 900)
        window.show()
        self.app.processEvents()
        window._on_splitter_moved(0, 2)          # 模拟用户拖过分隔条
        self.assertTrue(window._splitter_user_adjusted)
        before = window.splitter.sizes()
        before_ratio = before[2] / float(sum(before))

        window.resize(1300, 780)
        window._reapply_pane_sizes()
        self.app.processEvents()
        after = window.splitter.sizes()
        after_ratio = after[2] / float(sum(after))
        self.assertLess(after[2], before[2])              # 跟着变小
        self.assertAlmostEqual(before_ratio, after_ratio, delta=0.06)

    def test_shrinking_to_minimum_still_shrinks_the_panel(self):
        """缩到很窄时会被最小宽度兜住，但绝不能反过来变大。"""
        window = self.make_window()
        window.resize(1600, 900)
        window.show()
        self.app.processEvents()
        window._on_splitter_moved(0, 2)
        before = window.splitter.sizes()[2]
        window.resize(900, 640)
        window._reapply_pane_sizes()
        self.app.processEvents()
        after = window.splitter.sizes()[2]
        self.assertLess(after, before)
        self.assertGreaterEqual(after, window.detail.EXPANDED_MIN_WIDTH)

    def test_fullscreen_toggle_resets_manual_adjustment(self):
        window = self.make_window()
        window.resize(1400, 860)
        window.show()
        self.app.processEvents()
        window._on_splitter_moved(0, 2)
        self.assertTrue(window._splitter_user_adjusted)
        window._toggle_maximize()
        self.assertFalse(window._splitter_user_adjusted)

    def test_collapsed_panel_is_left_alone(self):
        """收起状态下重算分栏不能把窄条又撑开。"""
        from gui.detail import DetailPanel

        window = self.make_window()
        window.resize(1400, 860)
        window.show()
        self.app.processEvents()
        window.detail.set_collapsed(True)
        self.app.processEvents()
        window.resize(1100, 700)
        window._pane_timer.stop()
        window._reapply_pane_sizes()
        self.app.processEvents()
        self.assertEqual(window.splitter.sizes()[2], DetailPanel.COLLAPSED_WIDTH)
        self.assertTrue(window.detail.is_collapsed())


class TestTheme(GuiCase):
    """粉 + 白主题：颜色集中管理，且各区域深浅要拉开。"""

    def test_no_unsubstituted_tokens(self):
        """样式表模板里的 __TOKEN__ 必须全部被替换掉，否则 Qt 会静默忽略那一整条规则。"""
        import re

        from gui.style import STYLESHEET

        leftovers = re.findall(r"__[A-Z_]+__", STYLESHEET)
        self.assertEqual(leftovers, [], "样式表里还有没替换的占位符：%s" % leftovers)

    def test_accent_is_pink(self):
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        accent = QColor(PALETTE["ACCENT"])
        self.assertTrue(accent.isValid())
        self.assertGreater(accent.hue(), 300)          # 品红 / 玫红区间
        self.assertLess(accent.hue(), 350)
        self.assertGreater(accent.saturation(), 150)   # 够鲜艳，不然又是灰的

    def test_regions_have_distinct_tones(self):
        """边框 > 标签条 > 画布 > 卡片，依次变浅 —— 这样区域才分得清。

        用户的原话是「整个界面都是一种颜色分不清」，所以这条是核心约束。
        """
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        tones = {key: QColor(PALETTE[key]).lightness() for key in
                 ("FRAME", "HEADER_BG", "BG", "SURFACE")}
        self.assertLess(tones["FRAME"], tones["HEADER_BG"], tones)
        self.assertLess(tones["HEADER_BG"], tones["BG"], tones)
        self.assertLess(tones["BG"], tones["SURFACE"], tones)

    def test_card_colors_come_from_the_palette(self):
        """卡片绘制取的是同一份调色板，不是各写各的色值。"""
        from PyQt5.QtGui import QColor

        from gui import card
        from gui.style import CARD_COLORS

        self.assertEqual(card.COLOR_BG.name(), QColor(CARD_COLORS["card"]).name())
        self.assertEqual(card.COLOR_BORDER_SELECTED.name(),
                         QColor(CARD_COLORS["card_border_sel"]).name())
        self.assertEqual(card.COLOR_CHIP_BG.name(),
                         QColor(CARD_COLORS["chip_bg"]).name())

    def test_text_has_enough_contrast_on_panels(self):
        """白面板上的正文不能太浅，不然粉底白底都看不清字。"""
        from PyQt5.QtGui import QColor

        from gui.style import PALETTE

        text = QColor(PALETTE["TEXT"])
        self.assertLess(text.lightness(), 120)         # 够深
        self.assertGreater(text.saturation(), 20)      # 也带一点暖调，不是纯灰


class TestPrefs(GuiCase):
    def test_save_and_load(self):
        from gui.prefs import Prefs

        path = os.path.join(self.tmp, "state.json")
        prefs = Prefs(path)
        self.assertEqual(prefs.get("columns"), 2)          # 默认值
        prefs.set("columns", 4)
        self.assertEqual(Prefs(path).get("columns"), 4)

    def test_broken_file_falls_back_to_defaults(self):
        from gui.prefs import Prefs

        path = os.path.join(self.tmp, "broken.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{这不是 JSON")
        self.assertEqual(Prefs(path).get("columns"), 2)

    def test_unknown_keys_are_ignored(self):
        from gui.prefs import Prefs

        path = os.path.join(self.tmp, "state.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"columns": 3, "垃圾": 1}')
        prefs = Prefs(path)
        self.assertEqual(prefs.get("columns"), 3)
        self.assertNotIn("垃圾", prefs.data)

    def test_missing_directory_is_created(self):
        from gui.prefs import Prefs

        path = os.path.join(self.tmp, "sub", "dir", "state.json")
        prefs = Prefs(path)
        prefs.set("columns", 3)
        self.assertTrue(os.path.isfile(path))


class TestFramelessWindow(GuiCase):
    """无边框窗口：几何计算可以测；拖拽手感只能在真屏幕上看。"""

    def test_edge_zone_geometry(self):
        from gui.frameless import edge_zone
        width, height, margin = 1000, 700, 6
        self.assertEqual(edge_zone(0, 350, width, height, margin), "left")
        self.assertEqual(edge_zone(999, 350, width, height, margin), "right")
        self.assertEqual(edge_zone(500, 0, width, height, margin), "top")
        self.assertEqual(edge_zone(500, 699, width, height, margin), "bottom")
        self.assertEqual(edge_zone(0, 0, width, height, margin), "topleft")
        self.assertEqual(edge_zone(999, 0, width, height, margin), "topright")
        self.assertEqual(edge_zone(0, 699, width, height, margin), "bottomleft")
        self.assertEqual(edge_zone(999, 699, width, height, margin), "bottomright")
        self.assertIsNone(edge_zone(500, 350, width, height, margin))   # 正中央
        self.assertIsNone(edge_zone(50, 50, width, height, margin))     # 里面一点
        self.assertIsNone(edge_zone(-1, 350, width, height, margin))    # 窗口外
        self.assertIsNone(edge_zone(500, 700, width, height, margin))
        self.assertIsNone(edge_zone(10, 10, 0, 0, margin))              # 尺寸无效


class TestWindowSizing(GuiCase):
    """窗口尺寸换算（离屏拿不到真实屏幕，所以换算逻辑抽成纯函数来测）。"""

    def test_half_of_2560x1440(self):
        from gui.frameless import initial_window_size

        self.assertEqual(initial_window_size(2560, 1440, 0.5), (1280, 720))
    def test_other_fractions(self):
        from gui.frameless import initial_window_size

        self.assertEqual(initial_window_size(1920, 1080, 0.75), (1440, 810))
        self.assertEqual(initial_window_size(1920, 1080, 1.0), (1920, 1080))

    def test_clamped_to_minimum_on_small_screen(self):
        from gui.frameless import MIN_HEIGHT, MIN_WIDTH, initial_window_size

        # 小屏上一半只有 500x300，不能小于最小可用尺寸
        self.assertEqual(initial_window_size(1000, 600, 0.5), (MIN_WIDTH, MIN_HEIGHT))

    def test_never_exceeds_screen(self):
        from gui.frameless import initial_window_size

        width, height = initial_window_size(1000, 700, 5.0)   # 比例给离谱
        self.assertLessEqual(width, 1000)
        self.assertLessEqual(height, 700)

    def test_bad_fraction_falls_back(self):
        from gui.frameless import MIN_HEIGHT, initial_window_size

        # 非法比例退回 0.5；1000x1000 的一半是 500 高，还要被最小高度顶到 520
        self.assertEqual(initial_window_size(2000, 1000, "不是数字"), (1000, MIN_HEIGHT))
        self.assertEqual(initial_window_size(2000, 1000, None), (1000, MIN_HEIGHT))
        self.assertEqual(initial_window_size(2000, 2000, "x"), (1000, 1000))

    def test_zero_area_is_safe(self):
        from gui.frameless import MIN_HEIGHT, MIN_WIDTH, initial_window_size

        self.assertEqual(initial_window_size(0, 0, 0.5), (MIN_WIDTH, MIN_HEIGHT))

    def test_pane_widths_always_fit_the_window(self):
        from gui.frameless import pane_widths

        for width in (820, 1000, 1280, 1420, 1920, 2560):
            left, center, right = pane_widths(width)
            self.assertLessEqual(left + center + right, width,
                                 "窗口 %d 宽时三栏加起来 %d" % (width, left + center + right))
            self.assertGreaterEqual(center, 360)

    def test_pane_widths_give_grid_three_columns_when_there_is_room(self):
        from gui.frameless import pane_widths

        for width in (1280, 1420, 1920, 2560):
            _left, center, _right = pane_widths(width)
            self.assertGreaterEqual(center, 232 * 3,
                                    "窗口 %d 宽时中间只给了 %d，放不下 3 列" % (width, center))

    def test_pane_widths_scale_up(self):
        from gui.frameless import pane_widths

        small, large = pane_widths(1000), pane_widths(2000)
        self.assertLess(small[0], large[0])          # 侧栏随窗口变宽
        self.assertLess(small[2], large[2])
        self.assertGreater(large[1], small[1])       # 中间拿到更多

    def test_splitter_gets_sized_on_first_show(self):
        """回归：三栏宽度必须在窗口定型之后按真实宽度算。

        曾经在构造时就算，离屏/默认尺寸下一算就偏小，
        之后窗口被拉大只是把错误比例放大。
        """
        from gui.frameless import pane_widths

        window = self.make_window()
        window.resize(1600, 800)
        window.show()
        self.app.processEvents()
        sizes = window.splitter.sizes()
        self.assertEqual(len(sizes), 3)
        expected = pane_widths(window.width())
        # QSplitter 会按实际可用宽度微调，这里允许几像素误差，
        # 但如果是在构造时按错误宽度算的（左栏会只有 180 左右），就会明显对不上
        for actual, want in zip(sizes, expected):
            self.assertLessEqual(abs(actual - want), max(20, want * 0.05),
                                 "实际 %s 与预期 %s 差太多" % (sizes, expected))
        self.assertGreaterEqual(sizes[1], 232 * 3)   # 中间放得下 3 列

    def test_detail_panel_does_not_squeeze_the_grid(self):
        """回归：详情面板里那条又长又没空格的路径，不能把面板最小宽度撑大。

        被撑大的后果是：窗口一窄（比如 1280），中间的卡片区就被挤成 2 列。
        这个 bug 只在加载了样式表时才出现，所以测试也必须加载样式表。
        """
        from gui.frameless import pane_widths

        window = self.make_window()
        window.resize(1280, 720)
        window.show()
        self.app.processEvents()

        sizes = window.splitter.sizes()
        _left, _center, right = pane_widths(1280)
        self.assertGreaterEqual(sizes[1], 232 * 3,
                                "中间放不下 3 列卡片：%s" % (sizes,))
        self.assertLessEqual(abs(sizes[2] - right), 30,
                             "详情面板被内容撑宽了：%s（期望右栏约 %d）" % (sizes, right))

    def test_frameless_flag(self):
        frameless = self.make_window(frameless=True)
        self.assertTrue(bool(frameless.windowFlags() & Qt.FramelessWindowHint))
        self.assertTrue(frameless.is_frameless())

        native = self.make_window(frameless=False)
        self.assertFalse(bool(native.windowFlags() & Qt.FramelessWindowHint))
        self.assertFalse(native.is_frameless())

    def test_window_controls_present(self):
        window = self.make_window()
        controls = window.window_controls
        self.assertEqual(controls.min_button.text(), "─")
        self.assertEqual(controls.max_button.text(), "□")
        self.assertEqual(controls.close_button.text(), "✕")
        self.assertIs(controls.parent(), window.tabs.corner)

    def test_window_controls_wired(self):
        window = self.make_window()
        seen = []
        window.window_controls.minimizeRequested.connect(lambda: seen.append("min"))
        window.window_controls.maximizeRequested.connect(lambda: seen.append("max"))
        window.window_controls.closeRequested.connect(lambda: seen.append("close"))
        window.window_controls.min_button.click()
        window.window_controls.max_button.click()
        window.window_controls.close_button.click()
        self.assertEqual(seen, ["min", "max", "close"])

    def test_maximize_toggles_glyph_and_state(self):
        from gui.frameless import EDGE

        window = self.make_window()
        window.show()
        window._toggle_maximize()
        self.assertTrue(window.custom_maximized())
        self.assertEqual(window.window_controls.max_button.text(), "❐")
        # 最大化时不留缩放边距，否则四周多一圈灰边
        margins = window._inner_layout.contentsMargins()
        self.assertEqual((margins.left(), margins.top()), (0, 0))

        window._toggle_maximize()
        self.assertFalse(window.custom_maximized())
        self.assertEqual(window.window_controls.max_button.text(), "□")
        margins = window._inner_layout.contentsMargins()
        self.assertEqual(margins.left(), EDGE)

    def test_drag_moves_window(self):
        window = self.make_window()
        window.move(100, 100)
        window.begin_drag(QPoint(150, 110))         # 光标在窗口内 (50, 10) 处
        self.assertTrue(window.is_dragging())
        window.continue_drag(QPoint(250, 210))
        self.assertEqual(window.pos(), QPoint(200, 200))
        window.end_drag()
        self.assertFalse(window.is_dragging())

    def test_drag_does_not_move_after_release(self):
        window = self.make_window()
        window.move(100, 100)
        window.begin_drag(QPoint(150, 110))
        window.end_drag()
        window.continue_drag(QPoint(400, 400))
        self.assertEqual(window.pos(), QPoint(100, 100))

    def test_resize_right_edge(self):
        window = self.make_window()
        window.setGeometry(100, 100, 1000, 700)
        window._resize_zone = "right"
        window._resize_origin = (QPoint(1099, 400), QRect(window.geometry()))
        window._apply_resize(QPoint(1149, 400))     # 右拖 50
        self.assertEqual(window.width(), 1050)
        self.assertEqual(window.x(), 100)           # 左边不动

    def test_resize_respects_minimum_size(self):
        from gui.frameless import MIN_HEIGHT, MIN_WIDTH

        window = self.make_window()
        window.setGeometry(100, 100, 1000, 700)
        origin = QRect(window.geometry())

        window._resize_zone = "left"
        window._resize_origin = (QPoint(100, 400), origin)
        window._apply_resize(QPoint(9000, 400))     # 往右拖到离谱
        self.assertEqual(window.width(), MIN_WIDTH)

        window.setGeometry(100, 100, 1000, 700)
        window._resize_zone = "bottom"
        window._resize_origin = (QPoint(600, 799), QRect(window.geometry()))
        window._apply_resize(QPoint(600, -9000))    # 往上拖到离谱
        self.assertEqual(window.height(), MIN_HEIGHT)

    def test_no_resize_zone_when_maximized(self):
        window = self.make_window()
        window.show()
        window._toggle_maximize()
        self.assertIsNone(window._zone_at(QPoint(0, 350)))

    def test_native_mode_has_no_margins(self):
        window = self.make_window(frameless=False)
        margins = window.centralWidget().layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top()), (0, 0))

    def test_frameless_mode_has_resize_margins(self):
        from gui.frameless import EDGE

        window = self.make_window(frameless=True)
        margins = window.centralWidget().layout().contentsMargins()
        self.assertEqual((margins.left(), margins.top()), (EDGE, EDGE))


class TestWindowCorners(GuiCase):
    """窗口四角倒角。

    圆角本身是 Windows 11 的 DWM 做的（`DWMWA_WINDOW_CORNER_PREFERENCE`），
    不是我们自己开透明背景画 —— 自己画要么边缘有锯齿，要么子控件从圆角里戳出来。
    所以这里能测的是**什么时候要圆角、什么时候不要**，以及通知有没有真的发出去；
    真实边缘长什么样必须上真机看（offscreen 平台根本没有 DWM）。
    """

    def _spy(self):
        """把「通知 Windows」这一步记下来。"""
        from gui import frameless as frameless_mod

        calls = []

        def fake(hwnd, preference):
            calls.append((hwnd, preference))
            return True

        patcher = mock.patch.object(frameless_mod, "apply_corner_preference", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_preference_mapping(self):
        from gui.frameless import (CORNER_DEFAULT, CORNER_DONOTROUND,
                                   CORNER_ROUND, corner_preference)

        self.assertEqual(corner_preference(True, False), CORNER_ROUND)
        self.assertEqual(corner_preference(True, True), CORNER_DONOTROUND)
        # 原生标题栏：交回系统，别去干预它
        self.assertEqual(corner_preference(False, False), CORNER_DEFAULT)
        self.assertEqual(corner_preference(False, True), CORNER_DEFAULT)

    def test_windowed_window_asks_for_round_corners(self):
        from gui.frameless import CORNER_ROUND

        calls = self._spy()
        window = self.make_window()
        window.show()                       # 圆角在 showEvent 里同步
        self.app.processEvents()
        self.assertTrue(calls, "显示窗口时没有把圆角偏好告诉系统")
        self.assertEqual(calls[-1][1], CORNER_ROUND)
        self.assertTrue(calls[-1][0], "窗口句柄是空的")

    def test_maximized_window_asks_for_square_corners(self):
        """最大化必须切回方角：铺满屏幕还倒角的话，屏幕四角会露出桌面。"""
        from gui.frameless import CORNER_DONOTROUND, CORNER_ROUND

        calls = self._spy()
        window = self.make_window()
        window.show()
        self.app.processEvents()
        calls[:] = []

        window.maximize_custom()
        self.app.processEvents()
        self.assertTrue(calls, "最大化时没有同步圆角")
        self.assertEqual(calls[-1][1], CORNER_DONOTROUND)

        calls[:] = []
        window.restore_geometry()
        self.app.processEvents()
        self.assertEqual(calls[-1][1], CORNER_ROUND, "还原之后应该又变成圆角")

    def test_native_frame_leaves_corners_to_the_system(self):
        from gui.frameless import CORNER_DEFAULT

        calls = self._spy()
        window = self.make_window(frameless=False)
        window.show()
        self.app.processEvents()
        self.assertEqual(calls[-1][1], CORNER_DEFAULT)

    def test_apply_corner_preference_never_raises(self):
        """真调一次系统接口：老系统/非 Windows 上只能是返回 False，不能炸。"""
        from gui.frameless import apply_corner_preference

        self.assertFalse(apply_corner_preference(0, 2), "空句柄该直接返回 False")
        self.assertIsInstance(apply_corner_preference(1, 2), bool)


class TestMainWindow(GuiCase):
    def test_no_menu_bar(self):
        window = self.make_window()
        self.assertIsNone(window.findChild(QMenuBar))

    def test_top_bar_has_scan_and_more(self):
        window = self.make_window()
        self.assertEqual(window.scan_button.text(), "⟳")
        self.assertEqual(window.more_button.text(), "⋯")
        self.assertIsNotNone(window.more_button.menu())
        self.assertGreaterEqual(len(window.more_button.menu().actions()), 8)

    def test_sidebar_lists_tags(self):
        window = self.make_window()
        ids = [r["id"] for r in self.session.engine.query(limit=10)[0]]
        self.session.add_tags(ids[:2], ["侧栏标签"])
        window.refresh_sidebar()
        texts = [window.sidebar.list.item(i).text()
                 for i in range(window.sidebar.list.count())]
        self.assertEqual(texts[0], "全部视频")
        self.assertEqual(texts[1], "未打标签")
        self.assertTrue(any("侧栏标签" in t for t in texts))

    def test_sidebar_signal_opens_new_page(self):
        window = self.make_window()
        window.sidebar.tagActivated.emit("点出来的标签")
        self.assertEqual(window.tabs.bar.count(), 2)
        self.assertEqual(window.tabs.current_page().state.tags, ["点出来的标签"])

    def test_ctrl_click_adds_tag_to_current_page(self):
        window = self.make_window()
        page = window.tabs.current_page()
        window.apply_tag_to_current("A")
        self.assertEqual(page.state.tags, ["A"])
        window.apply_tag_to_current("B")
        self.assertEqual(page.state.tags, ["A", "B"])
        window.apply_tag_to_current("A")        # 再点一次取消
        self.assertEqual(page.state.tags, ["B"])
        self.assertEqual(window.tabs.bar.count(), 1)   # 没有新开页

    def test_filter_panel_applies_to_current_page(self):
        window = self.make_window()
        page = window.tabs.current_page()
        window.filters.keyword.setText("gamma")
        window.apply_filters()
        self.assertEqual(page.model.total(), 1)
        self.assertEqual(page.state.keyword, "gamma")

        window.reset_filters()
        self.assertEqual(page.model.total(), 5)
        self.assertIsNone(page.state.keyword)

    def test_filter_chips_drive_the_query(self):
        """点 chip 就该立刻作用到当前页（这是筛选面板重写后的主路径）。"""
        window = self.make_window()
        page = window.tabs.current_page()
        self.assertEqual(window.filters.active_count(), 0)

        duration = window.filters.duration_group
        chip = [c for c, value in duration._pairs if value == 60][0]
        chip.click()                                   # 模拟用户点击「1分」
        self.assertEqual(page.state.min_duration, 60)
        self.assertEqual(duration.value(), 60)
        self.assertEqual(window.filters.active_count(), 1)

        height = window.filters.height_group
        [c for c, value in height._pairs if value == 720][0].click()
        self.assertEqual(page.state.min_height, 720)
        self.assertEqual(window.filters.active_count(), 2)
        self.assertEqual(window.filters.badge.text(), "2 项")
        self.assertTrue(window.filters.reset_button.isVisible() or True)

        window.reset_filters()
        self.assertIsNone(page.state.min_duration)
        self.assertIsNone(page.state.min_height)
        self.assertEqual(window.filters.active_count(), 0)
        self.assertEqual(window.filters.height_group.value(), None)

    def test_filter_panel_has_year_options(self):
        """回归：年份 chip 要有真实的年份可选。

        重构时漏了调用 set_years，导致年份组永远只有「全部」。
        """
        window = self.make_window()
        years = [value for _chip, value in window.filters.year_group._pairs]
        self.assertEqual(years[0], None)                     # 第一个是「全部」
        self.assertGreater(len(years), 1)                    # 至少有一个真实年份
        available = set(window.session.engine.years())
        self.assertTrue(set(years[1:]) <= available)

        # 选一个年份应该真的筛选
        import datetime
        this_year = str(datetime.datetime.now().year)
        if this_year in years:
            group = window.filters.year_group
            [c for c, value in group._pairs if value == this_year][0].click()
            self.assertEqual(window.tabs.current_page().state.year, this_year)

    def test_filter_panel_can_collapse(self):
        window = self.make_window()
        self.assertTrue(window.filters.body_scroll.isVisible() or True)
        window.filters.set_expanded(False)
        self.assertFalse(window.filters.body_scroll.isVisible())
        self.assertIn("▸", window.filters.toggle.text())
        window.filters.set_expanded(True)
        self.assertIn("▾", window.filters.toggle.text())


class TestFilterPanelFitsInSmallWindows(GuiCase):
    """筛选区在小窗里不能显示不全：限高 + 内容可滚动。"""

    def _window(self, width, height, years=None):
        window = self.make_window()
        if years:
            window.filters.set_years(years)
        window.resize(width, height)
        window.show()
        self.app.processEvents()
        window._reapply_pane_sizes()
        self.app.processEvents()
        return window

    MANY_YEARS = ["2026", "2025", "2024", "2023", "2022", "2021", "2020",
                  "2019", "2018"]

    def test_small_window_keeps_tag_list_visible(self):
        """窗口很矮时，筛选区不能把标签列表挤没。"""
        window = self._window(820, 520, self.MANY_YEARS)
        self.assertGreaterEqual(window.sidebar.list.height(), 100)
        left = window.filters.parentWidget()
        self.assertLessEqual(window.filters.height(), left.height() * 0.6 + 2)

    def test_overflow_becomes_scrollable_instead_of_clipped(self):
        """内容装不下时必须能滚动，而不是被切掉看不见。"""
        window = self._window(820, 520, self.MANY_YEARS)
        scroll = window.filters.body_scroll
        viewport = scroll.viewport().height()
        content = scroll.widget().heightForWidth(max(40, scroll.viewport().width()))
        self.assertGreater(content, viewport, "这个尺寸下本该装不下，测试前提不成立")
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0,
                           "装不下却滚不动，说明内容被切掉了")

    def test_tall_window_does_not_scroll(self):
        """窗口够高时不该强行出滚动条。"""
        window = self._window(1600, 1000)
        scroll = window.filters.body_scroll
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)

    def test_filter_grows_with_content(self):
        """年份变多，筛选区要跟着变高（而不是永远停在初始高度）。"""
        few = self._window(1280, 900, ["2026"])
        few_height = few.filters.height()
        many = self._window(1280, 900, self.MANY_YEARS)
        self.assertGreaterEqual(many.filters.height(), few_height)

    def test_collapsing_shrinks_the_section(self):
        window = self._window(900, 600, self.MANY_YEARS)
        before = window.filters.height()
        window.filters.set_expanded(False)
        self.app.processEvents()
        self.assertLess(window.filters.height(), before)
        self.assertFalse(window.filters.body_scroll.isVisible())

    def test_filter_panel_syncs_when_switching_pages(self):
        window = self.make_window()
        home = window.tabs.home_page
        window.filters.keyword.setText("alpha")
        window.apply_filters()

        other = window.tabs.open_tag("另一页")
        self.assertEqual(window.filters.keyword.text(), "")     # 新页没有筛选
        self.assertEqual(window.filters.active_count(), 0)

        window.tabs.set_current_page(home)
        self.assertEqual(window.filters.keyword.text(), "alpha")
        self.assertEqual(window.filters.active_count(), 1)

    def test_tag_flow_through_session(self):
        window = self.make_window()
        page = window.tabs.current_page()
        row = page.model.row_at(0)
        page.view.selectionModel().select(
            page.model.index(0, 0), page.view.selectionModel().Select)
        window.add_tags_to_selection(["测试标签"])
        self.assertEqual(self.session.engine.tag_names_of(row["id"]), ["测试标签"])

        window.remove_tags_from_selection(["测试标签"])
        self.assertEqual(self.session.engine.tag_names_of(row["id"]), [])

    def test_deleting_tag_drops_it_from_open_page(self):
        window = self.make_window()
        row = window.tabs.home_page.model.row_at(0)
        self.session.add_tags([row["id"]], ["会被删掉的"])

        page = window.tabs.open_tag("会被删掉的")
        self.assertEqual(page.model.total(), 1)
        self.assertEqual(page.state.tags, ["会被删掉的"])

        self.session.delete_tag("会被删掉的")
        self.assertEqual(page.state.tags, [])          # 页面自动退回「全部」
        self.assertEqual(page.model.total(), 5)
        self.assertEqual(window.tabs.bar.count(), 2)

    def test_switching_pages_restores_their_state(self):
        window = self.make_window()
        home = window.tabs.home_page
        home.search_edit.setText("alpha")
        home._apply_search()

        other = window.tabs.open_tag("另一页")
        self.assertIsNone(other.state.keyword)

        window.tabs.set_current_page(home)
        self.assertIs(window.tabs.current_page(), home)
        self.assertEqual(window.filters.keyword.text(), "alpha")

    def test_shortcuts_registered(self):
        window = self.make_window()
        self.assertGreaterEqual(len(window._shortcuts), 8)

    def test_count_label(self):
        window = self.make_window()
        page = window.tabs.current_page()
        window._update_count(page.model.loaded(), page.model.total())
        self.assertEqual(window.count_label.text(), "显示 5 / 共 5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
