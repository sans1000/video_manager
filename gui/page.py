# -*- coding: utf-8 -*-
"""VideoPage：一个标签页 = 自己的查询状态 + 一屏缩略图卡片。

每个页面各持一份 QueryState，所以「筛选、排序、滚动位置」互不干扰 ——
这是标签页重构的核心。

缩略图的调度也在这里：只有**当前可见**的卡片才去抽帧，
滚动停下 120ms 之后批量发一次请求，避免快速滚动时排一堆没用的任务。
"""

from __future__ import annotations

import os
import subprocess

from PyQt5.QtCore import QEvent, QItemSelectionModel, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                             QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QListView, QMenu, QPushButton, QVBoxLayout, QWidget)

from core import util

from . import dialogs
from .card import VideoCardDelegate
from .engine import SORT_OPTIONS
from .grid import MARGIN, GridMetrics
from .model import VideoGridModel
from .query import QueryState

THUMB_BATCH = 60          # 一次最多丢多少个抽帧任务进线程池
THUMB_DEBOUNCE_MS = 120   # 滚动停止多久后才请求
LAYOUT_DEBOUNCE_MS = 150  # 窗口拖完之后多久才重排（拖的过程中不必每帧重算）


class VideoPage(QWidget):
    titleChanged = pyqtSignal(str)
    countChanged = pyqtSignal(int, int)        # 已加载, 总数
    selectionChanged = pyqtSignal(list)        # 选中的行
    statusMessage = pyqtSignal(str)
    tagPageRequested = pyqtSignal(str)         # 请求「把某个标签开成新页」

    def __init__(self, session, state=None, parent=None):
        super(VideoPage, self).__init__(parent)
        self.session = session
        self.state = state or QueryState()
        # 尺寸由 metrics 统一持有：模型回答 SizeHint，delegate 按它画，
        # 页面在窗口变化时更新它 —— 三处永远一致。
        self.metrics = GridMetrics()
        self.model = VideoGridModel(self.metrics, self)
        self.delegate = VideoCardDelegate(session, self.metrics, self)
        self.view = QListView()
        self._row_of_id = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(THUMB_DEBOUNCE_MS)
        self._thumb_timer.timeout.connect(self._request_visible_thumbs)

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(LAYOUT_DEBOUNCE_MS)
        self._layout_timer.timeout.connect(self._apply_metrics)

        self._build_ui()
        self.session.thumbs.signals.done.connect(self._on_thumb_done)
        self.refresh()

    # ------------------------------------------------------------ 界面

    def _build_ui(self):
        self.view.setModel(self.model)
        self.view.setItemDelegate(self.delegate)
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setUniformItemSizes(True)
        self.view.setMovement(QListView.Static)
        self.view.setWrapping(True)
        self.view.setFlow(QListView.LeftToRight)
        self.view.setGridSize(self.metrics.cell_size())
        self.view.setSpacing(0)
        # 四周留白交给 viewportMargins，格子本身就不用再算外边距
        self.view.setViewportMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 竖向滚动条常驻：否则它一出现，viewport 就变窄十几像素，
        # 卡片宽度跟着重算，第二张卡片会被挤到下一行去（来回抖）。
        # 宽度稳定了，网格尺寸才算得准。
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.view.setMouseTracking(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.doubleClicked.connect(lambda _: self.open_file())
        self.view.selectionModel().selectionChanged.connect(self._on_selection)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.view.installEventFilter(self)
        # 关键：监听 viewport 的大小变化，而不是 view 的。
        # 滚动条出现/消失、留白调整都只改变 viewport，不会触发 view 的 Resize，
        # 那样网格尺寸就永远停在旧值上了。
        self.view.viewport().installEventFilter(self)

        self.search_bar = self._build_search_bar()
        self.search_bar.setVisible(False)

        self.empty_label = QLabel(self.view)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.view, 1)

    def _build_search_bar(self):
        bar = QWidget()
        bar.setObjectName("searchBar")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入关键词后回车（Esc 收起）")
        self.search_edit.returnPressed.connect(self._apply_search)
        self.search_edit.installEventFilter(self)

        self.sort_combo = QComboBox()
        for label, key in SORT_OPTIONS:
            self.sort_combo.addItem(label, key)
        self.sort_combo.currentIndexChanged.connect(self._apply_sort)

        self.desc_box = QCheckBox("倒序")
        self.desc_box.stateChanged.connect(self._apply_sort)

        clear = QPushButton("清除")
        clear.clicked.connect(self.clear_search)
        close = QPushButton("×")
        close.setFixedWidth(32)
        close.clicked.connect(self.hide_search)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(self.search_edit, 1)
        layout.addWidget(QLabel("排序"))
        layout.addWidget(self.sort_combo)
        layout.addWidget(self.desc_box)
        layout.addWidget(clear)
        layout.addWidget(close)
        return bar

    def eventFilter(self, obj, event):
        if obj is self.view and event.type() == QEvent.Resize:
            self.empty_label.setGeometry(0, 0, self.view.width(), self.view.height())
        if obj is self.view.viewport() and event.type() == QEvent.Resize:
            self._layout_timer.start()          # 可用宽度变了 -> 防抖后重排
        if obj is self.search_edit and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.hide_search()
                return True
        return super(VideoPage, self).eventFilter(obj, event)

    # ------------------------------------------------------------ 尺寸

    def set_columns(self, columns):
        """改每行显示几个（全局设定，由主窗口统一推给所有页）。"""
        changed = self.metrics.set_columns(columns)
        self._layout_timer.stop()
        # 即使列数没变也强制重排一次：本页可能在后台待过，
        # 期间窗口尺寸变过，切回来必须先对齐一次。
        self._apply_metrics(force=True)
        return changed

    def apply_metrics(self):
        """立即按当前视图宽度重算卡片尺寸（外部要立刻生效时用）。"""
        self._layout_timer.stop()
        self._apply_metrics(force=True)

    def actual_columns(self):
        """数一数**实际**摆了几列（量第一行有几个不同的 x）。

        用来做自检：万一实际列数和设定对不上（比如被别的东西挤了），
        界面要能说出来，而不是默默显示错的。
        """
        count = self.model.rowCount()
        if count <= 1:
            return count
        first = self.view.visualRect(self.model.index(0, 0))
        columns = 1
        for i in range(1, min(count, 16)):
            rect = self.view.visualRect(self.model.index(i, 0))
            if rect.y() != first.y():
                break
            columns += 1
        return columns

    def _apply_metrics(self, force=False):
        # 用 viewport 宽度：四周留白已经由 viewportMargins 让出去了
        width = self.view.viewport().width()
        if width <= 0:
            return
        ratio = 1.0
        try:
            ratio = self.devicePixelRatioF()
        except AttributeError:
            pass
        changed = self.metrics.update(width, ratio)
        if not changed and not force:
            return
        self.view.setGridSize(self.metrics.cell_size())
        self.delegate.forget_all()              # 尺寸变了，已裁好的图作废
        self.model.layoutChanged.emit()         # 让视图重新算每格大小
        # layoutChanged 之外再显式重排一次：uniformItemSizes 开着的时候，
        # 视图会缓存条目尺寸，只发信号有时不够。
        try:
            self.view.doItemsLayout()
        except AttributeError:
            pass
        self._thumb_timer.start()               # 新尺寸的缩略图要重新抽

    # ------------------------------------------------------------ 查询

    def title(self):
        return self.state.title()

    def refresh(self, keep_selection=False, keep_view=None):
        """重新查询当前页。

        keep_view=True 时**保住用户的浏览进度**：已经翻出来的条数、滚动位置。
        以前刷新只重取第一页，于是滚到一半按 F5 会被弹回顶部，
        而且若选中的是后面几页的东西，重载后它不在模型里，选中也就丢了。

        keep_view 默认跟随 keep_selection —— 这两件事本来就是同一个意图：

          · 数据在底下变了（打完标签、扫描完、按 F5）→ 保住选中项，
            也就该保住滚动位置，别把用户弹走；
          · 结果集变了（筛选 / 搜索 / 排序）→ 调用方用的是不带参数的
            refresh()，那就老老实实回到顶部看新结果。
        """
        if keep_view is None:
            keep_view = keep_selection
        keep = [r["id"] for r in self.selected_rows()] if keep_selection else []
        scroll_bar = self.view.verticalScrollBar()
        scroll = scroll_bar.value() if keep_view else 0
        # 之前载入多少条，就按多少条重查（一次查询，不是循环翻页）
        wanted = max(self.state.page_size, self.model.loaded()) if keep_view else None
        self.state.offset = 0
        self._load(append=False, limit=wanted, keep_scroll=keep_view)
        if keep:
            self.reselect(keep)
        if scroll:
            self._restore_scroll(scroll)

    def _restore_scroll(self, value):
        """恢复滚动位置。

        模型重置后视图要重排一次才知道真实范围，所以除了立刻设一次，
        再排一个「事件循环之后」的补设。
        """
        bar = self.view.verticalScrollBar()

        def apply():
            try:
                bar.setValue(min(value, bar.maximum()))
            except RuntimeError:
                pass

        apply()
        QTimer.singleShot(0, apply)

    def _load(self, append, limit=None, keep_scroll=False):
        try:
            rows, total = self.session.engine.query(
                limit=limit or self.state.page_size, offset=self.state.offset,
                sort=self.state.sort, desc=self.state.desc, **self.state.filters())
        except Exception as exc:                      # noqa: BLE001
            self.statusMessage.emit("查询失败：%s" % exc)
            return
        if append:
            self.model.append_rows(rows)
        else:
            self.model.set_rows(rows, total)
        self.state.offset += len(rows)
        self._rebuild_index()
        offline = self._check_availability()
        self._update_empty_state(offline)
        if offline and not append:
            self.statusMessage.emit(
                "注意：本页有 %d 个文件当前读不到（索引里有、磁盘上找不到）。"
                "可能是硬盘/移动盘没接上，或文件被移走了。" % offline)
        self.countChanged.emit(self.model.loaded(), self.model.total())
        self.titleChanged.emit(self.title())
        if not append and not keep_scroll:
            self.view.scrollToTop()
        self._thumb_timer.start()

    def load_more(self):
        if self.model.loaded() < self.model.total():
            self._load(append=True)

    def _rebuild_index(self):
        self._row_of_id = {}
        for i in range(self.model.rowCount()):
            row = self.model.row_at(i)
            if row:
                self._row_of_id[row["id"]] = i

    def _check_availability(self):
        """检查文件此刻是否真的存在。

        索引库可能指向已经拔掉的移动硬盘 —— 这时如果只按「没有缩略图」判断，
        每张卡片都会永远显示「生成中…」，看不出真正的原因。所以在这里做一次
        实时探测，把结果挂到行数据上供绘制使用。
        """
        for i in range(self.model.rowCount()):
            row = self.model.row_at(i)
            if row is None or "available" in row:
                continue
            row["available"] = os.path.isfile(row["path"])
        return sum(1 for i in range(self.model.rowCount())
                   if not self.model.row_at(i).get("available", True))

    def _update_empty_state(self, offline=0):
        if self.model.total():
            self.empty_label.setVisible(False)
            return
        self.empty_label.setText(
            "没有符合条件的视频\n\n"
            "· 按 Ctrl+F 搜索文件名\n"
            "· 点左侧标签打开对应的页\n"
            "· 还没建索引的话，点右上角 ⟳ 扫描")
        self.empty_label.setVisible(True)

    def _on_scroll(self, value):
        self._thumb_timer.start()
        bar = self.view.verticalScrollBar()
        if bar.maximum() > 0 and value >= bar.maximum() - 40:
            self.load_more()

    # ------------------------------------------------------------ 缩略图

    def on_shown(self):
        """本页被切到前台时调用：先对齐一次尺寸（后台期间窗口可能变过），
        再给可见的卡片排抽帧。"""
        self._apply_metrics(force=True)
        self._thumb_timer.start()

    def _request_visible_thumbs(self):
        if not self.isVisible() or not self.session.thumbs.cache.ffmpeg:
            return
        viewport = self.view.viewport().rect()
        first = self.view.indexAt(viewport.topLeft())
        start = first.row() if first.isValid() else 0
        started = 0
        for i in range(start, self.model.rowCount()):
            index = self.model.index(i, 0)
            rect = self.view.visualRect(index)
            if rect.top() > viewport.bottom():
                break
            row = self.model.row_at(i)
            if row is None or not row.get("available", True):
                continue                           # 文件不在（比如硬盘没插），别白跑 ffmpeg
            if self.session.thumbs.request(row, self.metrics.bucket):
                started += 1
            if started >= THUMB_BATCH:
                break

    def _on_thumb_done(self, file_id, path):
        """缩略图好了：只重画那一格，不整屏重绘。

        页面可能在缩略图还在排队时就被关掉了，所以这里必须防一手
        「C++ 对象已销毁」——不然后台线程一回来就崩。
        """
        try:
            self.session.thumbs.release(file_id)
            row_index = self._row_of_id.get(file_id)
            if row_index is None:
                return                                 # 不是本页的行
            index = self.model.index(row_index, 0)
            rect = self.view.visualRect(index)
            if rect.isValid():
                self.view.viewport().update(rect)
        except RuntimeError:
            return                                     # 本页已经销毁

    # ------------------------------------------------------------ 选择

    def selected_rows(self):
        return self.model.rows_at(self.view.selectionModel().selectedIndexes())

    def _on_selection(self):
        self.selectionChanged.emit(self.selected_rows())

    def reselect(self, ids):
        wanted = set(ids)
        selection = self.view.selectionModel()
        selection.clearSelection()
        for file_id in wanted:
            row_index = self._row_of_id.get(file_id)
            if row_index is None:
                continue
            selection.select(self.model.index(row_index, 0),
                             QItemSelectionModel.Select | QItemSelectionModel.Rows)

    # ------------------------------------------------------------ 搜索与排序

    def show_search(self):
        self.search_bar.setVisible(True)
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def hide_search(self):
        self.search_bar.setVisible(False)
        self.view.setFocus()

    def toggle_search(self):
        if self.search_bar.isVisible():
            self.hide_search()
        else:
            self.show_search()

    def _apply_search(self):
        text = self.search_edit.text().strip()
        self.state.keyword = text or None
        self.refresh()

    def clear_search(self):
        self.search_edit.clear()
        self.state.keyword = None
        self.refresh()

    def _apply_sort(self):
        self.state.sort = self.sort_combo.currentData() or "name"
        self.state.desc = self.desc_box.isChecked()
        self.refresh()

    def sync_controls(self):
        """外部改了 state（比如筛选面板）之后，把控件同步过来。"""
        index = self.sort_combo.findData(self.state.sort)
        if index >= 0 and index != self.sort_combo.currentIndex():
            self.sort_combo.blockSignals(True)
            self.sort_combo.setCurrentIndex(index)
            self.sort_combo.blockSignals(False)
        self.desc_box.blockSignals(True)
        self.desc_box.setChecked(self.state.desc)
        self.desc_box.blockSignals(False)
        if self.state.keyword and self.search_edit.text() != self.state.keyword:
            self.search_edit.setText(self.state.keyword)

    # ------------------------------------------------------------ 文件操作

    def open_file(self):
        rows = self.selected_rows()
        if len(rows) != 1:
            return
        path = rows[0]["path"]
        try:
            os.startfile(path)                         # noqa: S606
        except AttributeError:
            subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self.statusMessage.emit("打不开文件：%s" % exc)

    def reveal_file(self):
        rows = self.selected_rows()
        if not rows:
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(rows[0]["path"])])
        except OSError as exc:
            self.statusMessage.emit("打开资源管理器失败：%s" % exc)

    def copy_path(self):
        rows = self.selected_rows()
        if not rows:
            return
        QApplication.clipboard().setText("\n".join(r["path"] for r in rows))
        self.statusMessage.emit("已复制 %d 条路径到剪贴板。" % len(rows))

    # ------------------------------------------------------------ 右键菜单

    def _context_menu(self, pos):
        index = self.view.indexAt(pos)
        if index.isValid() and not self.view.selectionModel().isSelected(index):
            self.view.selectionModel().select(
                index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        menu = QMenu(self)
        menu.addAction("播放", self.open_file)
        menu.addAction("在资源管理器中显示", self.reveal_file)
        menu.addAction("复制完整路径", self.copy_path)
        menu.addSeparator()
        menu.addAction("打标签…", self.prompt_add_tags)
        menu.addAction("移除标签…", self.prompt_remove_tags)
        menu.addSeparator()
        menu.addAction("按此标签筛选当前页", self.filter_by_first_tag)
        menu.addAction("把此标签开成新页", self.open_first_tag_as_page)
        menu.exec_(self.view.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ 标签操作

    def prompt_add_tags(self):
        rows = self.selected_rows()
        if not rows:
            return
        names = dialogs.ask_tag(self, "打标签",
                                "给选中的 %d 个视频打标签（多个用逗号分隔）：" % len(rows),
                                self.session.tag_names())
        if names:
            ids = [r["id"] for r in rows]
            self.session.add_tags(ids, names)
            self.refresh(keep_selection=True)

    def prompt_remove_tags(self):
        rows = self.selected_rows()
        if not rows:
            return
        names = dialogs.ask_tag(self, "移除标签",
                                "从选中的 %d 个视频上移除这些标签：" % len(rows),
                                self.session.tag_names())
        if names:
            self.session.remove_tags([r["id"] for r in rows], names)
            self.refresh(keep_selection=True)

    def first_tag_of_selection(self):
        """选中项里第一个带标签的，取它的第一个标签。"""
        for row in self.selected_rows():
            names = [t for t in (row.get("tags") or "").split("、") if t]
            if names:
                return names[0]
        return None

    def filter_by_first_tag(self):
        name = self.first_tag_of_selection()
        if not name:
            self.statusMessage.emit("选中的视频还没有标签。")
            return
        self.state.set_tag(name)
        self.sync_controls()
        self.refresh()
        self.statusMessage.emit("当前页已按标签「%s」筛选。" % name)

    def open_first_tag_as_page(self):
        name = self.first_tag_of_selection()
        if not name:
            self.statusMessage.emit("选中的视频还没有标签。")
            return
        self.tagPageRequested.emit(name)

    # ------------------------------------------------------------ 标签联动

    def on_tags_changed(self, existing_tags):
        """标签被改名/删除后，本页的筛选条件要跟着调整。"""
        changed = False
        kept = [t for t in self.state.tags if t in existing_tags]
        if len(kept) != len(self.state.tags):
            self.state.tags = kept
            changed = True
        self.refresh(keep_selection=True)
        return changed
