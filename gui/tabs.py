# -*- coding: utf-8 -*-
"""BrowserTabs：浏览器式的标签页容器。

没有直接用 QTabWidget，而是 QTabBar + QStackedWidget 自己拼：
这样标签条可以横跨整个窗口宽度（含左侧导航栏上方），
更接近视频网站的样子，也方便把 ⟳ ⋯ 放在标签条右端。
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QMenu, QStackedWidget, QTabBar,
                             QToolButton, QVBoxLayout, QWidget)

from .page import VideoPage
from .query import QueryState

CLOSE_SIZE = 18          # 标签上关闭按钮的点击区（正方形）
CLOSE_GLYPH = "✕"        # 和窗口右上角的关闭按钮同一个字形


class BrowserTabs(QWidget):
    pageAdded = pyqtSignal(object)               # 新页创建（供外部接信号）
    pageClosed = pyqtSignal(object)              # 页被关闭（对象即将析构）
    currentPageChanged = pyqtSignal(object)      # VideoPage / None
    statusMessage = pyqtSignal(str)
    openTagRequested = pyqtSignal(str)
    renameTagRequested = pyqtSignal(str)
    deleteTagRequested = pyqtSignal(str)

    def __init__(self, session, parent=None):
        super(BrowserTabs, self).__init__(parent)
        self.session = session
        self.stack = QStackedWidget()          # 先建栈，下面连线要用
        self.bar = QTabBar()
        # 不用 Qt 自带的关闭按钮：它在自定义样式表下要么没图标要么是
        # 一个突兀的黑色粗叉（跟主题完全不搭），而主页本来也不该能关。
        # 所以 × 一律自己画（见 _make_close_button）。
        self.bar.setTabsClosable(False)
        self.bar.setMovable(True)
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setUsesScrollButtons(True)
        self.bar.setElideMode(Qt.ElideRight)
        self.bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bar.installEventFilter(self)      # 中键点标签=关闭，浏览器习惯
        # 关键：标签条和页面栈必须联动，否则切标签时状态变了、画面还停在第一页。
        # 注意这里**不能**直接把 currentChanged 接到 stack.setCurrentIndex：
        # 标签可以拖动，拖完之后标签条的下标和页面栈的下标就不再一一对应了，
        # 那样会把「甲」的标签切到「丙」的画面去。一律按页面对象来定位。
        self.bar.currentChanged.connect(self._on_current_changed)
        self.bar.tabMoved.connect(self._on_tab_moved)
        self.bar.customContextMenuRequested.connect(self._tab_menu)

        self.corner = QWidget()
        self.corner.setObjectName("tabCorner")
        self.corner_layout = QHBoxLayout(self.corner)
        self.corner_layout.setContentsMargins(0, 0, 4, 0)
        self.corner_layout.setSpacing(2)

        self.bar_row = QWidget()
        self.bar_row.setObjectName("tabRow")
        row_layout = QHBoxLayout(self.bar_row)
        # 标签"浮"在标签条上、上面留一道空隙 —— 这道空隙必须留在这里，
        # 不能写成 QTabBar::tab 的 margin-top，否则标签上的 × 会跟文字对不齐
        # （原因见 style.py 里 QTabBar::tab 的注释）。
        row_layout.setContentsMargins(6, 6, 0, 0)
        row_layout.setSpacing(0)
        row_layout.addWidget(self.bar, 1)
        row_layout.addWidget(self.corner, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.bar_row, 0)
        layout.addWidget(self.stack, 1)

        self.pages = []
        self.home_page = None

    # ------------------------------------------------------------ 增删

    def add_corner_widget(self, widget):
        self.corner_layout.addWidget(widget)

    def _wire(self, page):
        page.titleChanged.connect(lambda _t, p=page: self._sync_title(p))
        page.countChanged.connect(lambda _l, _t, p=page: self._sync_title(p))
        page.statusMessage.connect(self.statusMessage.emit)
        page.tagPageRequested.connect(self.open_tag)

    def add_page(self, page, activate=True):
        self._wire(page)
        index = self.stack.addWidget(page)
        # 必须先登记再 addTab：加第一个标签时 QTabBar 会立刻把它设为当前页并发出
        # currentChanged，那时如果 pages 里还没有它，current_page() 会返回 None，
        # 主窗口就收不到「当前页」事件（状态栏计数一直是空的就是这个原因）。
        self.pages.append(page)
        tab_index = self.bar.addTab(page.title())
        # 新建时 stack 和 bar 都是往末尾追加，两个下标必然一致
        assert index == tab_index, "标签与页面的索引不同步"
        # 主页固定不关；其它页挂一个看得见的 ×
        if page is not self.home_page:
            self.bar.setTabButton(tab_index, QTabBar.RightSide,
                                  self._make_close_button(page))
        if activate:
            self.bar.setCurrentIndex(tab_index)
        self.pageAdded.emit(page)
        if self.current_page() is page:
            # 索引本来就是当前页时 currentChanged 不会再发一次，这里补上
            self._on_current_changed(tab_index)
        return tab_index

    def _make_close_button(self, page):
        """给标签做一个**画得出来**的 ×。

        Qt 自带的 `QTabBar::close-button` 在自定义样式表下什么都不画
        （以前 style.py 里那句 `image: none` 就是被它逼出来的），
        结果只剩一块点得到、但看不见的空白 —— 界面上就等于「这一页没有关闭按钮」。
        所以这里换成一个自己画的 QToolButton，配色和悬停反馈都跟着主题走。
        """
        button = QToolButton(self.bar)
        button.setObjectName("tabClose")
        button.setText(CLOSE_GLYPH)
        button.setToolTip("关闭此页（Ctrl+W）")
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.ArrowCursor)
        button.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        button.clicked.connect(lambda _checked=False, p=page:
                               self.close_page_object(p))
        return button

    def close_page_object(self, page):
        """按页面对象关闭。

        不能记标签索引：标签是 `setMovable(True)`，拖一下顺序就全变了，
        记下来的索引会关掉旁边那一页。
        """
        if page in self.pages:
            self.close_page(self.pages.index(page))

    def eventFilter(self, obj, event):
        """中键点标签 = 关闭这一页（浏览器习惯）。

        Qt 自己只在 `tabsClosable` 打开时才发 `tabCloseRequested`，而那个开关
        会顺手塞一个跟主题不搭的原生关闭按钮进来，所以这里自己处理。
        """
        if (obj is self.bar and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.MiddleButton):
            index = self.bar.tabAt(event.pos())
            if 0 <= index < len(self.pages) and self.pages[index] is not self.home_page:
                self.close_page(index)
                return True
        return super(BrowserTabs, self).eventFilter(obj, event)

    def open_home(self):
        if self.home_page is not None:
            self.set_current_page(self.home_page)
            return self.home_page
        page = VideoPage(self.session, QueryState())
        self.home_page = page          # 先认下主页，add_page 里就靠它决定挂不挂 ×
        self.add_page(page)
        return page

    def _find_page(self, predicate):
        for page in self.pages:
            if predicate(page):
                return page
        return None

    def page_for_tag(self, name):
        return self._find_page(lambda p: p.state.tags == [name] and not p.state.untagged)

    def page_for_untagged(self):
        return self._find_page(lambda p: p.state.untagged and not p.state.tags)

    def open_tag(self, name):
        """点标签：已经有这个标签的页就切过去，否则新开一页。"""
        page = self.page_for_tag(name)
        if page is None:
            page = VideoPage(self.session, QueryState(tags=[name]))
            self.add_page(page)
            self.statusMessage.emit("已打开标签页「%s」" % name)
        else:
            self.set_current_page(page)
        return page

    def open_untagged(self):
        page = self.page_for_untagged()
        if page is None:
            page = VideoPage(self.session, QueryState(untagged=True))
            self.add_page(page)
        else:
            self.set_current_page(page)
        return page

    def close_page(self, index):
        if not (0 <= index < len(self.pages)):
            return
        page = self.pages[index]
        if page is self.home_page:
            self.statusMessage.emit("主页不能关闭。")
            return
        self.pages.pop(index)
        self.bar.removeTab(index)
        self.stack.removeWidget(page)
        self.pageClosed.emit(page)
        page.setParent(None)
        page.deleteLater()
        # 删完之后再对齐一次画面。这里也按对象来定位，不看下标。
        now = self.current_page()
        if now is not None:
            self.stack.setCurrentWidget(now)

    def close_others(self, keep_index):
        for index in range(len(self.pages) - 1, -1, -1):
            if index != keep_index and self.pages[index] is not self.home_page:
                self.close_page(index)

    def close_to_right(self, index):
        for i in range(len(self.pages) - 1, index, -1):
            self.close_page(i)

    # ------------------------------------------------------------ 当前页

    def current_page(self):
        index = self.bar.currentIndex()
        if 0 <= index < len(self.pages):
            return self.pages[index]
        return None

    def set_current_page(self, page):
        if page in self.pages:
            self.bar.setCurrentIndex(self.pages.index(page))

    def next_page(self, step=1):
        if len(self.pages) < 2:
            return
        index = (self.bar.currentIndex() + step) % len(self.pages)
        self.bar.setCurrentIndex(index)

    def _on_current_changed(self, index):
        page = self.current_page()
        if page is not None:
            # 按对象切画面，不用下标：标签拖动过之后两个下标就对不上了
            self.stack.setCurrentWidget(page)
            page.on_shown()
        self.currentPageChanged.emit(page)

    def _on_tab_moved(self, from_index, to_index):
        """标签被拖动后，让 pages 跟着标签条一起换位置。

        `pages`（以及它对应的页面栈）和标签条必须始终保持同一顺序，
        否则 `current_page()` 会指向另一页、`close_page(下标)` 会关错页。
        """
        if not (0 <= from_index < len(self.pages)):
            return
        if not (0 <= to_index < len(self.pages)):
            return
        page = self.pages.pop(from_index)
        self.pages.insert(to_index, page)
        # 拖动过程中 Qt 会把当前标签切来切去，这里把画面重新对一次
        current = self.current_page()
        if current is not None:
            self.stack.setCurrentWidget(current)

    def _sync_title(self, page):
        if page not in self.pages:
            return
        index = self.pages.index(page)
        self.bar.setTabText(index, page.title())
        self.bar.setTabToolTip(index, "%s\n（共 %d 个，已加载 %d 个）"
                               % (page.title(), page.model.total(),
                                  page.model.loaded()))

    # ------------------------------------------------------------ 右键菜单

    def _tab_menu(self, pos):
        index = self.bar.tabAt(pos)
        if index < 0:
            return
        page = self.pages[index]
        menu = QMenu(self)
        if page is not self.home_page:
            menu.addAction("关闭", lambda: self.close_page(index))
        menu.addAction("关闭其他", lambda: self.close_others(index))
        menu.addAction("关闭右侧", lambda: self.close_to_right(index))
        menu.addSeparator()
        if page.state.tags:
            name = page.state.tags[0]
            menu.addAction("重命名标签「%s」…" % name,
                           lambda: self.renameTagRequested.emit(name))
            menu.addAction("删除标签「%s」" % name,
                           lambda: self.deleteTagRequested.emit(name))
        menu.addAction("刷新本页", page.refresh)
        menu.exec_(self.bar.mapToGlobal(pos))

    # ------------------------------------------------------------ 联动

    def refresh_all(self):
        for page in self.pages:
            page.refresh(keep_selection=True)

    def on_tags_changed(self, existing_tags):
        for page in list(self.pages):
            page.on_tags_changed(existing_tags)
