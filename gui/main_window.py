# -*- coding: utf-8 -*-
"""主窗口：只做「组装 + 应用级操作」。

重构前这里是 615 行的万能类；现在查询状态下沉到了 VideoPage、
标签业务收敛到了 Session、面板拆成了独立文件，
所以这里只剩下拼装标签页、⟳/⋯ 按钮、状态栏和快捷键。

顶部没有菜单栏 —— 高频操作在卡片右键菜单里，低频全局操作收进 ⋯。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import QEvent, QRect, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QAction, QFileDialog, QFrame, QHBoxLayout, QLabel,
                             QMenu, QMessageBox, QProgressBar, QShortcut,
                             QSplitter, QToolButton, QVBoxLayout, QWidget)

from core import report

from . import dialogs
from .detail import DetailPanel
from .filters import FilterPanel
from .frameless import EDGE, FramelessWindow, initial_window_size, pane_widths
from .sidebar import UNTAGGED_KEY, TagSidebar
from .tabs import BrowserTabs
from .window_controls import WindowControls

COLUMN_CHOICES = (2, 3, 4)
# 最大化时自动切到这个列数：窗口都铺满了，3 列看着最舒服
AUTO_COLUMNS_WHEN_MAXIMIZED = 3
# 筛选区最多占左栏高度的这个比例，其余留给标签列表
FILTER_MAX_SHARE = 0.55


class LeftPanel(QWidget):
    """左侧栏容器：标签导航 + 筛选。

    负责把「可用高度」告诉筛选区 —— 窗口一矮就把筛选区压到不超过一半，
    超出的内容由筛选区内部的滚动条承接，不会被切掉看不见。
    """

    def __init__(self, sidebar, filters, parent=None):
        super(LeftPanel, self).__init__(parent)
        self.sidebar = sidebar
        self.filters = filters
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar, 1)
        layout.addWidget(filters, 0)
        self._layout = layout

    def resizeEvent(self, event):
        super(LeftPanel, self).resizeEvent(event)
        if self.height() > 0:
            self.filters.set_max_height(int(self.height() * FILTER_MAX_SHARE))


class MainWindow(FramelessWindow):
    def __init__(self, session, parent=None, frameless=True):
        super(MainWindow, self).__init__(parent, frameless=frameless)
        self.session = session
        self.tabs = BrowserTabs(session)
        self.sidebar = TagSidebar()
        self.filters = FilterPanel()
        self.detail = DetailPanel()
        self._windowed_columns, self._maximized_columns = self._load_preferred_columns()
        self._detail_expanded_width = 0
        self._splitter_user_adjusted = False
        # 窗口尺寸变化后重新分配三栏（防抖，拖窗口时不每帧重算）
        self._pane_timer = QTimer(self)
        self._pane_timer.setSingleShot(True)
        self._pane_timer.setInterval(140)
        self._pane_timer.timeout.connect(self._reapply_pane_sizes)

        self._build_ui()
        self._build_shortcuts()
        self._wire()

        self.refresh_sidebar()
        self.filters.set_years(self.session.engine.years())
        self.detail.set_tag_suggestions(session.tag_names())
        self.tabs.open_home()
        self._sync_columns()
        self.window_controls.set_maximized(self._window_maximized())
        self._sync_titles()

    # ------------------------------------------------------------ 界面

    def _build_ui(self):
        self.setWindowTitle("视频库管理")
        self._apply_initial_geometry()

        # 顶部只有：标签页 + ⟳ + ⋯
        self.scan_button = QToolButton()
        self.scan_button.setText("⟳")
        self.scan_button.setToolTip("扫描 / 更新索引（Ctrl+R）")
        self.scan_button.setAutoRaise(True)
        self.scan_button.clicked.connect(self.session.start_scan)

        self.more_button = QToolButton()
        self.more_button.setText("⋯")
        self.more_button.setToolTip("更多")
        self.more_button.setAutoRaise(True)
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        self.more_button.setMenu(self._build_more_menu())

        # 每行显示几个：一个按钮带菜单（2/3/4），不额外占地方
        self._column_actions = {}
        self.columns_button = QToolButton()
        self.columns_button.setText("▦")
        self.columns_button.setAutoRaise(True)
        self.columns_button.setPopupMode(QToolButton.InstantPopup)
        self.columns_button.setMenu(self._build_columns_menu())

        # 窗口控制按钮：并到标签条右端（无边框模式下这是唯一的窗口入口）
        self.window_controls = WindowControls()
        self.window_controls.minimizeRequested.connect(self.showMinimized)
        self.window_controls.maximizeRequested.connect(self._toggle_maximize)
        self.window_controls.closeRequested.connect(self.close)

        self.tabs.add_corner_widget(self.scan_button)
        self.tabs.add_corner_widget(self.columns_button)
        self.tabs.add_corner_widget(self.more_button)

        # 工具按钮和窗口按钮之间加条竖线，避免误点关闭
        separator = QFrame()
        separator.setObjectName("cornerSeparator")
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        self.tabs.add_corner_widget(separator)
        self.tabs.add_corner_widget(self.window_controls)

        # 无边框时：标签条空白处可以拖动窗口 / 双击最大化
        self.tabs.bar.installEventFilter(self)

        left = LeftPanel(self.sidebar, self.filters)
        left.setObjectName("leftPanel")

        inner = QWidget()
        inner.setObjectName("windowBody")
        inner_layout = QHBoxLayout(inner)
        # 无边框时四周留出热区给缩放用；原生标题栏时不需要
        self._inner_layout = inner_layout
        self._apply_body_margins()
        inner_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.splitter = splitter
        self._panes_initialized = False
        inner_layout.addWidget(splitter)
        self.setCentralWidget(inner)

        self.status_label = QLabel("")
        self.count_label = QLabel("")
        self.root_label = QLabel(self.session.root_label)
        self.root_label.setObjectName("muted")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        bar = self.statusBar()
        bar.addWidget(self.status_label, 1)
        bar.addPermanentWidget(self.count_label)
        bar.addPermanentWidget(self.root_label)
        bar.addPermanentWidget(self.progress)
        self.status_label.setText("就绪。点左侧标签开新页，Ctrl+F 搜索。")

    def _build_columns_menu(self):
        menu = QMenu(self)
        for count in COLUMN_CHOICES:
            action = QAction("每行 %d 个" % count, self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked, value=count: self.set_preferred_columns(value))
            menu.addAction(action)
            self._column_actions[count] = action
        menu.addSeparator()
        self._column_hint = menu.addAction("")
        self._column_hint.setEnabled(False)       # 只当说明用
        return menu

    def _build_more_menu(self):
        menu = QMenu(self)
        menu.addAction("刷新当前页（F5）", self.refresh_current)
        menu.addAction("扫描 / 更新索引", self.session.start_scan)
        menu.addAction("选择根目录…", self.choose_root)
        menu.addAction("打开数据库所在文件夹", self.open_db_folder)
        menu.addSeparator()
        menu.addAction("导出当前列表为 CSV…", self.export_csv)
        menu.addAction("生成 HTML 索引报表…", self.export_html)
        menu.addSeparator()
        menu.addAction("导出标签备份…", self.export_tags)
        menu.addAction("从备份导入…", self.import_tags)
        menu.addAction("撤销上一步标签操作", self.session.undo_tag)
        menu.addSeparator()
        self.detail_action = QAction("显示详情面板", self)
        self.detail_action.setCheckable(True)
        self.detail_action.setChecked(True)
        self.detail_action.triggered.connect(
            lambda checked: self.detail.set_collapsed(not checked))
        menu.addAction(self.detail_action)
        menu.addSeparator()
        menu.addAction("关于", self.about)
        menu.addAction("退出", self.close)
        return menu

    def _build_shortcuts(self):
        bindings = [
            ("Ctrl+F", self.toggle_search),
            ("Ctrl+T", lambda: self.tabs.open_home()),
            ("Ctrl+W", self.close_current_tab),
            ("Ctrl+R", self.session.start_scan),
            ("F5", self.refresh_current),
            ("Ctrl+Z", self.session.undo_tag),
            ("Ctrl+Q", self.close),
            ("Ctrl+Tab", lambda: self.tabs.next_page(1)),
            ("Ctrl+Shift+Tab", lambda: self.tabs.next_page(-1)),
            ("Ctrl+2", lambda: self.set_preferred_columns(2)),
            ("Ctrl+3", lambda: self.set_preferred_columns(3)),
            ("Ctrl+4", lambda: self.set_preferred_columns(4)),
            ("Esc", self.on_escape),
        ]
        self._shortcuts = []
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _wire(self):
        self.sidebar.homeActivated.connect(lambda: self.tabs.open_home())
        self.sidebar.untaggedActivated.connect(self.tabs.open_untagged)
        self.sidebar.tagActivated.connect(self.tabs.open_tag)
        self.sidebar.tagFilterRequested.connect(self.apply_tag_to_current)
        self.sidebar.renameRequested.connect(self.rename_tag)
        self.sidebar.deleteRequested.connect(self.delete_tag)
        self.sidebar.exportRequested.connect(self.export_tags)

        self.tabs.pageAdded.connect(self._on_page_added)
        self.tabs.currentPageChanged.connect(self._on_page_changed)
        self.tabs.statusMessage.connect(self.status_label.setText)
        self.tabs.renameTagRequested.connect(self.rename_tag)
        self.tabs.deleteTagRequested.connect(self.delete_tag)

        self.filters.filtersChanged.connect(self.apply_filters)
        self.filters.resetRequested.connect(self.reset_filters)

        self.detail.addTagsRequested.connect(self.add_tags_to_selection)
        self.detail.removeTagsRequested.connect(self.remove_tags_from_selection)
        self.detail.openRequested.connect(self.open_file)
        self.detail.revealRequested.connect(self.reveal_file)
        self.detail.collapsedChanged.connect(self._on_detail_collapsed)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        self.session.status.connect(self.status_label.setText)
        self.session.progress.connect(self.status_label.setText)
        self.session.busy.connect(self._on_busy)
        self.session.failed.connect(self._on_failed)
        self.session.tagsChanged.connect(self._on_tags_changed)
        self.session.libraryChanged.connect(self._on_library_changed)
        # 缩略图好了要通知详情面板；网格由各页自己重画
        self.session.thumbs.signals.done.connect(self._on_thumb_ready)

    # ------------------------------------------------------------ 页面联动

    def _on_page_added(self, page):
        page.selectionChanged.connect(self._on_selection)
        page.set_columns(self.effective_columns())     # 新页要用当前的列数

    def _on_page_changed(self, page):
        self.detail.set_files([])
        if page is None:
            return
        self.filters.sync_from(page.state)
        self.sidebar.set_tags(self.session.engine.tags(), page.state.tags)
        self._update_count(page.model.loaded(), page.model.total())
        self._sync_titles()

    def _on_selection(self, rows):
        self.detail.set_files(rows)
        if len(rows) == 1 and rows[0].get("available", True):
            page = self.current_page()
            bucket = page.metrics.bucket if page else None
            self.session.thumbs.request(rows[0], bucket)

    def _on_thumb_ready(self, file_id, path):
        """缩略图完成：详情面板显示它（网格由所属页面自己重画）。"""
        self.detail.set_thumbnail(file_id, path)

    def _update_count(self, loaded, total):
        self.count_label.setText("显示 %d / 共 %d" % (loaded, total))

    def _sync_titles(self):
        page = self.tabs.current_page()
        if page is not None:
            self.setWindowTitle("视频库管理 — %s" % page.state.describe())

    def refresh_sidebar(self):
        page = self.tabs.current_page()
        active = page.state.tags if page else []
        self.sidebar.set_tags(self.session.engine.tags(), active)

    # ------------------------------------------------------------ 当前页操作

    def current_page(self):
        return self.tabs.current_page()

    def refresh_current(self):
        """刷新当前页：保住浏览进度，同时把侧栏计数也刷一遍。"""
        page = self.current_page()
        if page:
            page.refresh(keep_selection=True)
            self.refresh_sidebar()
            self.status_label.setText("已刷新当前页。")

    def toggle_search(self):
        page = self.current_page()
        if page:
            page.toggle_search()

    def on_escape(self):
        page = self.current_page()
        if page and page.search_bar.isVisible():
            page.hide_search()

    def close_current_tab(self):
        page = self.current_page()
        if page is not None:
            self.tabs.close_page_object(page)

    def apply_filters(self):
        page = self.current_page()
        if page is None:
            return
        values = self.filters.values()
        page.state.keyword = values["keyword"]
        page.state.year = values["year"]
        page.state.min_height = values["min_height"]
        page.state.min_duration = values["min_duration"]
        page.state.has_cover = values["has_cover"]
        page.sync_controls()
        page.refresh(keep_selection=True)
        self._sync_titles()

    def reset_filters(self):
        page = self.current_page()
        if page is None:
            return
        self.filters.reset()
        page.state.reset_filters()
        page.state.keyword = None
        page.sync_controls()
        page.refresh(keep_selection=True)
        self.status_label.setText("已重置当前页的筛选条件。")

    def apply_tag_to_current(self, name):
        """Ctrl+单击标签：加到当前页筛选（多个标签 = 同时具备）。"""
        page = self.current_page()
        if page is None:
            return
        if name == UNTAGGED_KEY:
            page.state.untagged = not page.state.untagged
            page.state.clear_tags()
        else:
            if page.state.toggle_tag(name):
                page.state.untagged = False
        page.sync_controls()
        page.refresh(keep_selection=True)
        self.refresh_sidebar()
        self._sync_titles()
        self.status_label.setText("当前页筛选：%s" % page.state.describe())

    # ------------------------------------------------------------ 详情面板收起

    def _reapply_pane_sizes(self):
        """按当前窗口宽度重新分配三栏。

        之前只在首次显示时算过一次，于是**从全屏还原后，两侧面板还留着
        全屏时的绝对宽度**（1280 宽的窗口里详情面板占 330px），把中间的
        卡片区挤掉一大块。现在窗口尺寸一变就重算。

        用户自己拖过分隔条的话，就按他的比例缩放，不强行覆盖他的选择。
        """
        if self.detail.is_collapsed():
            return                          # 收起时不动它，免得又给撑开
        width = self.width()
        if width <= 0:
            return
        if getattr(self, "_splitter_user_adjusted", False):
            sizes = self.splitter.sizes()
            old_total = sum(sizes)
            if old_total > 0:
                factor = float(width - 30) / old_total
                target = [max(120, int(s * factor)) for s in sizes]
            else:
                target = list(pane_widths(width))
        else:
            target = list(pane_widths(width))
        self.splitter.setSizes(target)

    def _detail_full_width(self):
        """展开时详情面板该有多宽：优先用上次记住的，否则按分栏比例算。"""
        remembered = getattr(self, "_detail_expanded_width", 0)
        if remembered:
            return max(DetailPanel.EXPANDED_MIN_WIDTH, remembered)
        return pane_widths(self.width())[2]

    def _on_detail_collapsed(self, collapsed):
        """收起/展开详情面板：改的是分栏宽度，不是把控件藏起来就完事。"""
        sizes = self.splitter.sizes()
        if len(sizes) != 3:
            return
        total = sum(sizes) or self.width()
        collapsed_width = DetailPanel.COLLAPSED_WIDTH
        if collapsed:
            self._detail_expanded_width = max(DetailPanel.EXPANDED_MIN_WIDTH, sizes[2])
            target = collapsed_width
        else:
            target = self._detail_full_width()
        center = max(320, total - sizes[0] - target)
        self.splitter.setSizes([sizes[0], center, target])
        if hasattr(self, "detail_action"):
            self.detail_action.setChecked(not collapsed)
        self.status_label.setText(
            "详情面板已收起（点面板右上角的 « 可以展开）。" if collapsed
            else "详情面板已展开。")

    def _on_splitter_moved(self, pos, index):
        """用户自己拖过分隔条：记住这个偏好，之后按比例缩放而不是覆盖掉。"""
        self._splitter_user_adjusted = True
        # 把收起的窄条往右拖开，就当作要展开它
        if (self.detail.is_collapsed()
                and self.detail.width() > DetailPanel.COLLAPSED_WIDTH + 40):
            self.detail.set_collapsed(False)

    def resizeEvent(self, event):
        super(MainWindow, self).resizeEvent(event)
        self._pane_timer.start()

    # ------------------------------------------------------------ 文件操作

    def _page_selection(self):
        page = self.current_page()
        return page.selected_rows() if page else []

    def open_file(self):
        page = self.current_page()
        if page:
            page.open_file()

    def reveal_file(self):
        page = self.current_page()
        if page:
            page.reveal_file()

    # ------------------------------------------------------------ 标签操作

    def add_tags_to_selection(self, names):
        rows = self._page_selection()
        if not rows:
            return
        self.session.add_tags([r["id"] for r in rows], names)
        page = self.current_page()
        if page:
            page.refresh(keep_selection=True)

    def remove_tags_from_selection(self, names):
        rows = self._page_selection()
        if not rows:
            return
        self.session.remove_tags([r["id"] for r in rows], names)
        page = self.current_page()
        if page:
            page.refresh(keep_selection=True)

    def rename_tag(self, name):
        names = dialogs.ask_tag(self, "重命名标签",
                                "把「%s」改成（若已存在则合并）：" % name,
                                self.session.tag_names())
        if names:
            self.session.rename_tag(name, names[0])

    def delete_tag(self, name):
        if not dialogs.confirm(self, "删除标签",
                               "确定要删除标签「%s」吗？\n"
                               "它会在所有文件上被移除（可用 Ctrl+Z 撤销）。" % name):
            return
        self.session.delete_tag(name)

    def export_tags(self):
        default = self.session.engine.cfg.tags_backup
        path, _ = QFileDialog.getSaveFileName(self, "导出标签备份", default,
                                              "CSV 文件 (*.csv)")
        if path:
            self.session.export_tags(path)

    def import_tags(self):
        start = os.path.dirname(self.session.engine.cfg.tags_backup)
        path, _ = QFileDialog.getOpenFileName(self, "从备份导入标签", start,
                                              "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            self.session.import_tags(path)
        except Exception as exc:                       # noqa: BLE001
            QMessageBox.critical(self, "导入失败", str(exc))

    def _on_tags_changed(self):
        names = set(self.session.tag_names())
        self.tabs.on_tags_changed(names)
        self.refresh_sidebar()
        self.detail.set_tag_suggestions(sorted(names))
        page = self.current_page()
        if page:
            self._update_count(page.model.loaded(), page.model.total())
        self._sync_titles()

    def _on_library_changed(self):
        page = self.current_page()
        if page:
            page.refresh(keep_selection=True)
        self.filters.set_years(self.session.engine.years())
        self.refresh_sidebar()

    def _on_busy(self, busy):
        self.progress.setVisible(busy)
        self.scan_button.setEnabled(not busy)

    # ------------------------------------------------------------ 每行显示几个

    def _load_preferred_columns(self):
        """窗口模式和全屏模式各存一份 —— 这样全屏下改列数才改得动。"""
        return (self._load_columns("columns"),
                self._load_columns("columns_maximized"))

    def _load_columns(self, key):
        default = COLUMN_CHOICES[0] if key == "columns" else AUTO_COLUMNS_WHEN_MAXIMIZED
        try:
            saved = int(self.session.prefs.get(key))
        except (TypeError, ValueError):
            saved = default
        return saved if saved in COLUMN_CHOICES else default

    def _window_maximized(self):
        if not self.is_frameless():
            return bool(self.isMaximized())
        if not self._custom_maximized:
            return False
        # 自愈：用「拖动标题条」或系统方式把窗口还原时，状态位不一定跟得上。
        # 拿实际几何对一下，免得「明明已经是窗口了却被当成全屏」，
        # 结果用错了列数那份设置（点了 3 个却按 2 个排）。
        screen = None
        try:
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.screenAt(self.frameGeometry().center())
        except Exception:
            screen = None
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            return True
        area = screen.availableGeometry()
        geometry = self.geometry()
        # 屏幕比窗口的最小尺寸还小时，几何判断没有意义（窗口根本铺不满
        # 也缩不下去），这时只能信状态位。真实屏幕上不会出现这种情况，
        # 但离屏环境（测试）的虚拟屏经常比最小窗口还小。
        if (area.width() < self.minimumWidth()
                or area.height() < self.minimumHeight()):
            return True
        # 「全屏」的定义就是精确铺满可用区域；只要尺寸对不上，就说明窗口
        # 已经被还原（可能没走我们的按钮），把状态位纠正回来。
        if (abs(geometry.width() - area.width()) > 2
                or abs(geometry.height() - area.height()) > 2):
            self._custom_maximized = False
            return False
        return True

    def effective_columns(self):
        """当前实际用几个：全屏用全屏那份设置，窗口模式用窗口那份。"""
        return (self._maximized_columns if self._window_maximized()
                else self._windowed_columns)

    def set_preferred_columns(self, columns):
        """改列数。

        全屏时改的是「全屏那份」，窗口模式下改的是「窗口那份」——
        以前不管三七二十一都写窗口那份，结果全屏时怎么点都没反应。
        """
        if columns not in COLUMN_CHOICES:
            return
        maximized = self._window_maximized()
        if maximized:
            self._maximized_columns = columns
            self.session.prefs.set("columns_maximized", columns)
        else:
            self._windowed_columns = columns
            self.session.prefs.set("columns", columns)
        self._sync_columns()
        self.status_label.setText(
            "每行显示 %d 个（%s）。" % (columns, "全屏设置" if maximized else "窗口设置"))

    def _sync_columns(self):
        columns = self.effective_columns()
        for page in self.tabs.pages:
            page.set_columns(columns)

        for count, action in self._column_actions.items():
            action.setChecked(count == columns)

        maximized = self._window_maximized()
        if maximized:
            self._column_hint.setText("全屏模式：改的是全屏时的列数")
        else:
            self._column_hint.setText(
                "全屏时自动用 %d 个" % self._maximized_columns)

        # 自检：实际摆出来的列数和设定对不对得上
        actual = 0
        page = self.tabs.current_page()
        if page is not None:
            actual = page.actual_columns()
        note = ""
        if actual and actual != columns:
            note = "｜注意：实际只排下 %d 列" % actual
        self.columns_button.setToolTip(
            "每行显示 %d 个（%s）%s｜Ctrl+2 / Ctrl+3 / Ctrl+4"
            % (columns, "全屏" if maximized else "窗口", note))
        self._actual_columns = actual

    # ------------------------------------------------------------ 无边框窗口

    def eventFilter(self, obj, event):
        """标签条空白处拖动窗口、双击最大化（Chrome 的手感）。"""
        if obj is self.tabs.bar and self.is_frameless():
            kind = event.type()
            if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if self.tabs.bar.tabAt(event.pos()) < 0:
                    was_maximized = self._window_maximized()
                    self.begin_drag(event.globalPos())
                    # 拖动会让最大化的窗口还原 —— 这时列数、缩放边距、
                    # 按钮图标都要跟着切，以前漏了这一步。
                    if was_maximized and not self._window_maximized():
                        self._on_window_state_changed()
                    return True
            elif kind == QEvent.MouseMove and self.is_dragging():
                self.continue_drag(event.globalPos())
                return True
            elif kind == QEvent.MouseButtonRelease:
                if self.is_dragging():
                    self.end_drag()
                    return True
            elif kind == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                if self.tabs.bar.tabAt(event.pos()) < 0:
                    self._toggle_maximize()
                    return True
        return super(MainWindow, self).eventFilter(obj, event)

    def _apply_initial_geometry(self):
        """初始窗口大小。

        默认**直接以最大化状态出生**（配置里可关），这样不会出现
        「先小后大」的闪一下；关掉时按屏幕可用区域的比例居中显示。

        顺便把「还原后」的尺寸先记下来：点 ❐ 还原时回到这个大小。
        """
        from PyQt5.QtWidgets import QApplication
        screen = None
        try:
            from PyQt5.QtGui import QCursor
            screen = QApplication.screenAt(QCursor.pos())
        except Exception:                              # 老版本没有 screenAt
            screen = None
        screen = screen or QApplication.primaryScreen()

        if screen is None:
            self.resize(1280, 720)
            return

        area = screen.availableGeometry()
        fraction = getattr(self.session.engine.cfg, "window_fraction", 0.5)
        width, height = initial_window_size(area.width(), area.height(), fraction)
        self._normal_geometry = QRect(area.x() + (area.width() - width) // 2,
                                      area.y() + (area.height() - height) // 2,
                                      width, height)

        if getattr(self.session.engine.cfg, "start_maximized", True):
            if self.is_frameless():
                # 直接按可用区域摆好，并标记成已最大化（按钮图标、缩放进退都会对上）
                self._custom_maximized = True
                self.setGeometry(area)
            else:
                self._start_maximized_pending = True   # 原生标题栏模式在 showEvent 里处理
        else:
            self.setGeometry(self._normal_geometry)

    def showEvent(self, event):
        """首次显示时按「真实窗口宽度」分配三栏宽度。

        构造时窗口还没定型（可能还是默认尺寸），这时候算出来的分配会偏小，
        之后再被拉伸就只是把错误的比例放大。
        """
        super(MainWindow, self).showEvent(event)
        if not self._panes_initialized:
            self._panes_initialized = True
            self.splitter.setSizes(list(pane_widths(self.width())))
            if getattr(self, "_start_maximized_pending", False):
                self._start_maximized_pending = False
                self.showMaximized()               # 原生标题栏模式下启动最大化
    def _apply_body_margins(self):
        """最大化时不留缩放边距，否则四周会多出一圈灰边。"""
        margin = EDGE if (self.is_frameless() and not self.custom_maximized()) else 0
        self._inner_layout.setContentsMargins(margin, margin, margin, margin)

    def _on_window_state_changed(self):
        """全屏/窗口状态变了之后要同步的所有东西，集中在一处。"""
        self.window_controls.set_maximized(self._window_maximized())
        self._apply_body_margins()
        # 换了尺寸档位（全屏 <-> 窗口）就重新按新宽度分配三栏，
        # 否则还原后两侧面板还留着全屏时的绝对宽度。
        self._splitter_user_adjusted = False
        self._reapply_pane_sizes()
        self._sync_columns()

    def _toggle_maximize(self):
        maximized = self.toggle_maximize()
        self._on_window_state_changed()
        if maximized:
            self.status_label.setText(
                "窗口已全屏，每行 %d 个（还原后回到 %d 个）。"
                % (self.effective_columns(), self._windowed_columns))
        else:
            self.status_label.setText(
                "窗口已还原，每行 %d 个。" % self.effective_columns())

    def changeEvent(self, event):
        super(MainWindow, self).changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.window_controls.set_maximized(self.isMaximized())
            if not self.is_frameless():
                self._sync_columns()         # 原生标题栏模式下最大化也要跟着变

    def _on_failed(self, message):
        QMessageBox.critical(self, "出错了", message)

    # ------------------------------------------------------------ 报表

    def _all_rows_of_current(self):
        page = self.current_page()
        if page is None:
            return []
        rows, _total = self.session.engine.query(
            limit=1000000, offset=0, sort=page.state.sort, desc=page.state.desc,
            **page.state.filters())
        return rows

    def export_csv(self):
        default = os.path.join(self.session.engine.cfg.report_dir, "index.csv")
        path, _ = QFileDialog.getSaveFileName(self, "导出 CSV", default, "CSV 文件 (*.csv)")
        if not path:
            return
        rows = self._all_rows_of_current()
        report.export_index_csv(rows, path)
        self.status_label.setText("已导出 %d 条到 %s" % (len(rows), path))

    def export_html(self):
        default = os.path.join(self.session.engine.cfg.report_dir, "index.html")
        path, _ = QFileDialog.getSaveFileName(self, "生成 HTML 索引", default,
                                              "HTML 文件 (*.html)")
        if not path:
            return
        rows = self._all_rows_of_current()
        report.render_html(rows, path, title="视频索引（来自界面）",
                           stats=self.session.engine.stats(),
                           source=self.session.root_label)
        self.status_label.setText("已生成 HTML 索引：%s" % path)
        try:
            os.startfile(path)
        except (AttributeError, OSError):
            pass

    # ------------------------------------------------------------ 其它

    def choose_root(self):
        start = self.session.roots[0] if self.session.roots else "F:\\"
        path = QFileDialog.getExistingDirectory(self, "选择要管理的文件夹", start)
        if not path:
            return
        if not dialogs.confirm(self, "切换根目录",
                               "切换到「%s」？\n\n当前索引里的记录会保留，"
                               "点右上角 ⟳ 扫描才会把新目录加进来。" % path):
            return
        self.session.engine.cfg.roots = [os.path.normpath(path)]
        self.root_label.setText(self.session.root_label)
        self.status_label.setText("已切换根目录，点右上角 ⟳ 开始建索引。")

    def open_db_folder(self):
        folder = os.path.dirname(self.session.engine.cfg.db_path)
        try:
            os.startfile(folder)
        except (AttributeError, OSError) as exc:
            QMessageBox.information(self, "数据库位置", "%s\n(%s)" % (folder, exc))

    def about(self):
        QMessageBox.information(
            self, "关于",
            "视频库管理\n\n"
            "· 缩略图网格浏览，标签页组织（点左侧标签开新页）\n"
            "· Ctrl+F 搜索，Ctrl+单击标签 = 加到当前页筛选\n"
            "· 打标签只写索引库，不改动视频文件\n\n"
            "数据库：%s\n根目录：%s\nffmpeg：%s"
            % (self.session.engine.cfg.db_path, self.session.root_label,
               self.session.engine.cfg.ffmpeg or "未找到"))

    def closeEvent(self, event):
        if self.session.is_busy():
            if not dialogs.confirm(self, "扫描进行中", "扫描还在进行，确定要退出吗？"):
                event.ignore()
                return
        self.session.shutdown()
        event.accept()
