# -*- coding: utf-8 -*-
"""FilterPanel：作用于「当前标签页」的筛选。

原来是「年份/分辨率/时长/封面」四个下拉框挤在一个 QGroupBox 里，
看着像系统设置对话框，和卡片流的调性完全不搭。现在改成：

  · 一排排胶囊按钮（chip），选中的高亮，一眼能看出当前选了什么；
  · 可以整块折叠起来，把版面让给标签列表；
  · 只有真的改了筛选条件，标题右边才出现「N 项」和「重置」。

它本身不持有状态 —— 值变了只发信号，由 MainWindow 写进当前页的
QueryState 再让它刷新，所以切页时状态天然跟着页走。
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QSizePolicy, QToolButton,
                             QVBoxLayout, QWidget)

from .chips import ChipGroup

HEIGHT_CHOICES = [
    ("全部", None), ("720p", 720), ("1080p", 1080), ("4K", 2160),
]
DURATION_CHOICES = [
    ("全部", None), ("1分", 60), ("5分", 300), ("10分", 600),
    ("30分", 1800), ("1时", 3600),
]
COVER_CHOICES = [("全部", "any"), ("有封面", True), ("无封面", False)]


class FilterBodyScroll(QScrollArea):
    """筛选表单的滚动区。

    QScrollArea 默认的 sizeHint 很小，外层布局会以为「内容就一点点」，
    于是筛选区永远只有一百多像素高、白白空着。这里把尺寸提示改成
    按内容算，并把 heightForWidth 一路传下去（chip 换行会改变需要的高度）。
    """

    def __init__(self, parent=None):
        super(FilterBodyScroll, self).__init__(parent)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def sizeHint(self):
        widget = self.widget()
        if widget is None:
            return super(FilterBodyScroll, self).sizeHint()
        hint = widget.sizeHint()
        return QSize(hint.width() + 4, hint.height() + 4)

    def minimumSizeHint(self):
        return QSize(80, 56)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        widget = self.widget()
        if widget is None:
            return 56
        inner = max(40, width - 4)
        return max(56, widget.heightForWidth(inner) + 4)


class FilterPanel(QWidget):
    filtersChanged = pyqtSignal()
    resetRequested = pyqtSignal()

    def __init__(self, parent=None):
        super(FilterPanel, self).__init__(parent)
        self.setObjectName("filterPanel")
        # 同 DetailPanel：QWidget 的子类要显式打开才会画样式表给的背景和边框，
        # 否则上面那条分隔线和底色都不会出现。
        self.setAttribute(Qt.WA_StyledBackground, True)

        # ---- 折叠开关 + 生效项数 + 重置
        self.toggle = QToolButton()
        self.toggle.setObjectName("sectionHeader")
        self.toggle.setText("▾ 筛选")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setToolTip("收起 / 展开筛选")
        self.toggle.toggled.connect(self._on_toggled)

        self.badge = QLabel("")
        self.badge.setObjectName("filterBadge")

        self.reset_button = QPushButton("重置")
        self.reset_button.setObjectName("linkButton")
        self.reset_button.setCursor(Qt.PointingHandCursor)
        self.reset_button.setVisible(False)
        self.reset_button.clicked.connect(self.resetRequested.emit)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(self.toggle)
        header.addWidget(self.badge)
        header.addStretch(1)
        header.addWidget(self.reset_button)

        # ---- 关键词
        self.keyword = QLineEdit()
        self.keyword.setObjectName("filterKeyword")
        self.keyword.setPlaceholderText("关键词…")
        self.keyword.setClearButtonEnabled(True)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)                 # 防抖，别每敲一个字就查库
        self.timer.timeout.connect(self._on_changed)
        self.keyword.textChanged.connect(lambda _: self.timer.start())

        # ---- 四个 chip 组（标题放左边一列，比占一整行省竖向空间）
        self.height_group = ChipGroup(
            HEIGHT_CHOICES,
            tooltip_of=lambda v: "不限" if v is None else "分辨率不低于 %dp" % v)
        self.duration_group = ChipGroup(
            DURATION_CHOICES,
            tooltip_of=lambda v: "不限" if v is None else "时长不少于 %d 分钟" % (v // 60))
        self.cover_group = ChipGroup(COVER_CHOICES)
        self.year_group = ChipGroup([("全部", None)])
        for group in (self.height_group, self.duration_group,
                      self.cover_group, self.year_group):
            group.changed.connect(self._on_changed)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        for row, (label, group) in enumerate((
                ("分辨率", self.height_group),
                ("时长", self.duration_group),
                ("封面", self.cover_group),
                ("年份", self.year_group))):
            caption = QLabel(label)
            caption.setObjectName("chipGroupTitle")
            caption.setAlignment(Qt.AlignRight | Qt.AlignTop)
            caption.setFixedWidth(38)
            grid.addWidget(caption, row, 0)
            grid.addWidget(group, row, 1)
        grid.setColumnStretch(1, 1)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 2, 2, 0)
        body_layout.setSpacing(5)
        body_layout.addWidget(self.keyword)
        body_layout.addLayout(grid)
        body_layout.addStretch(1)
        self.body = body

        # 表单内容放进滚动区：窗口一矮，筛选区被限高，超出部分可以滚，
        # 而不是直接被切掉看不见（以前就是这样）。
        self.body_scroll = FilterBodyScroll()
        self.body_scroll.setObjectName("filterScroll")
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body_scroll.setWidget(body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.body_scroll, 1)
        self._layout = layout

    # ------------------------------------------------------------ 限高

    def set_max_height(self, pixels):
        """由左栏告知可用高度：本区最多占这么多，其余留给标签列表。

        内容比这高就在滚动区里滚，不会出现「显示不全」。
        """
        pixels = max(120, int(pixels))
        if pixels == self.maximumHeight():
            return
        self.setMaximumHeight(pixels)

    # ------------------------------------------------------------ 折叠

    def _on_toggled(self, expanded):
        self.body_scroll.setVisible(expanded)
        self.toggle.setText("▾ 筛选" if expanded else "▸ 筛选")
        self.toggle.setToolTip("收起筛选" if expanded else "展开筛选")

    def set_expanded(self, expanded):
        self.toggle.setChecked(expanded)

    # ------------------------------------------------------------ 取值

    def values(self):
        return {
            "keyword": self.keyword.text().strip() or None,
            "year": self.year_group.value(),
            "min_height": self.height_group.value(),
            "min_duration": self.duration_group.value(),
            "has_cover": self.cover_group.value(),
        }

    def set_years(self, years):
        options = [("全部", None)] + [(str(y), str(y)) for y in years]
        self.year_group.set_options(options)

    # ------------------------------------------------------------ 同步

    def sync_from(self, state):
        """把一个页面的状态同步到控件上（切页时用），期间不发信号。"""
        widgets = [self.keyword, self.height_group, self.duration_group,
                   self.cover_group, self.year_group]
        for widget in widgets:
            widget.blockSignals(True)
        self.keyword.setText(state.keyword or "")
        self.height_group.set_value(state.min_height)
        self.duration_group.set_value(state.min_duration)
        self.cover_group.set_value("any" if state.has_cover is None else state.has_cover)
        self.year_group.set_value(state.year)
        for widget in widgets:
            widget.blockSignals(False)
        self.timer.stop()
        self._update_badge()

    def reset(self):
        widgets = [self.keyword, self.height_group, self.duration_group,
                   self.cover_group, self.year_group]
        for widget in widgets:
            widget.blockSignals(True)
        self.keyword.clear()
        for group in (self.height_group, self.duration_group,
                      self.cover_group, self.year_group):
            group.reset()
        for widget in widgets:
            widget.blockSignals(False)
        self.timer.stop()
        self._update_badge()

    # ------------------------------------------------------------ 内部

    def active_count(self):
        groups = [self.height_group, self.duration_group,
                  self.cover_group, self.year_group]
        return (sum(1 for g in groups if not g.is_default())
                + (1 if self.keyword.text().strip() else 0))

    def _on_changed(self):
        self._update_badge()
        self.filtersChanged.emit()

    def _update_badge(self):
        count = self.active_count()
        if count:
            self.badge.setText("%d 项" % count)
            self.badge.setVisible(True)
            self.reset_button.setVisible(True)
        else:
            self.badge.setVisible(False)
            self.reset_button.setVisible(False)
