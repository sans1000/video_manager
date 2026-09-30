# -*- coding: utf-8 -*-
"""DetailPanel：右侧详情面板（所有标签页共用）。

它只显示「当前页选中的东西」：选中 1 个显示缩略图和完整元数据，
选中多个就显示合计，并且标签编辑会作用到全部选中项。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QCompleter, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QPushButton, QSizePolicy,
                             QSpacerItem, QToolButton, QVBoxLayout, QWidget)

from core import report, util

from .style import PALETTE


class DetailPanel(QWidget):
    addTagsRequested = pyqtSignal(list)
    removeTagsRequested = pyqtSignal(list)
    openRequested = pyqtSignal()
    revealRequested = pyqtSignal()
    collapsedChanged = pyqtSignal(bool)

    EXPANDED_MIN_WIDTH = 196     # 展开时不能拖得比这更窄（否则内容没意义）
    COLLAPSED_WIDTH = 34         # 收起后只留一条窄边，按钮还在原位

    def __init__(self, parent=None):
        super(DetailPanel, self).__init__(parent)
        self.setObjectName("detailPanel")
        # QWidget 的子类默认**不会**绘制样式表里的背景和边框，
        # 必须显式打开 WA_StyledBackground，否则 #detailPanel 那些
        # background / border / border-radius 规则全部形同虚设
        # （圆角、底色都不会出现，只看到父级的颜色）。
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._rows = []
        self._current_id = None
        self._collapsed = False
        # 一开始就把状态写进属性：样式表里是按 [collapsed=...] 分两套规则给的，
        # 属性没定义的话两边都不匹配，面板会没有背景色。
        self.setProperty("collapsed", "false")
        # 下面有一行「完整路径」，又长又没有空格，wordWrap 也折不下来，
        # 会把面板的最小宽度顶到 300 多像素，进而把中间的卡片区挤成 2 列。
        # 显式压住最小宽度，让分栏比例说了算。
        self.setMinimumWidth(self.EXPANDED_MIN_WIDTH)

        # 标题行右上角放收起/展开按钮：收起后这一条还在，随时能点回来，
        # 不用像拖分隔条那样「拖没了就找不回来」。
        self.title_label = QLabel("详情")
        self.title_label.setObjectName("panelTitle")
        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("collapseButton")
        self.collapse_button.setText("»")
        self.collapse_button.setToolTip("收起详情面板")
        self.collapse_button.setCursor(Qt.PointingHandCursor)
        self.collapse_button.setFocusPolicy(Qt.NoFocus)
        self.collapse_button.setFixedSize(24, 22)
        self.collapse_button.clicked.connect(self.toggle_collapsed)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.collapse_button)

        self.thumb = QLabel("选中一个视频\n即可显示缩略图")
        self.thumb.setObjectName("thumb")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setMinimumHeight(170)
        self.thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.info = QLabel("—")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setObjectName("info")
        self.info.setMinimumWidth(1)
        # 别让布局把它的内容宽度当回事（长路径会撑爆分栏）
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)

        buttons = QHBoxLayout()
        self.btn_open = QPushButton("播放")
        self.btn_open.clicked.connect(self.openRequested.emit)
        self.btn_reveal = QPushButton("所在文件夹")
        self.btn_reveal.clicked.connect(self.revealRequested.emit)
        buttons.addWidget(self.btn_open)
        buttons.addWidget(self.btn_reveal)

        tag_title = QLabel("标签")
        tag_title.setObjectName("panelTitle")
        self.tag_list = QListWidget()
        self.tag_list.setMaximumHeight(110)
        self.tag_list.setSelectionMode(QListWidget.ExtendedSelection)

        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("输入标签后回车添加…")
        self.tag_input.returnPressed.connect(self._add_from_input)

        tag_buttons = QHBoxLayout()
        btn_add = QPushButton("添加")
        btn_add.setObjectName("primary")      # 主要动作，用强调色
        btn_add.clicked.connect(self._add_from_input)
        btn_remove = QPushButton("移除选中")
        btn_remove.clicked.connect(self._remove_selected)
        tag_buttons.addWidget(btn_add)
        tag_buttons.addWidget(btn_remove)

        # 收起时要藏起来的东西都装进 body
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(6)
        body_layout.addWidget(self.thumb)
        body_layout.addLayout(buttons)
        body_layout.addWidget(self.info)
        body_layout.addWidget(tag_title)
        body_layout.addWidget(self.tag_list)
        body_layout.addWidget(self.tag_input)
        body_layout.addLayout(tag_buttons)
        body_layout.addStretch(1)
        self.body = body

        layout = QVBoxLayout(self)
        self._margins_expanded = (8, 8, 8, 8)
        self._margins_collapsed = (5, 6, 5, 6)
        layout.setContentsMargins(*self._margins_expanded)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(body, 1)
        # 尾部留一个可伸缩的空格：收起时 body 不占位，没有它的话
        # 多出来的竖直空间会被摊给标题行，按钮就跑到中间去了。
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Minimum,
                                   QSizePolicy.Expanding))
        self._layout = layout

        self.set_files([])

    # ------------------------------------------------------------ 收起 / 展开

    def is_collapsed(self):
        return self._collapsed

    def _repolish(self, widget):
        """改了动态属性之后要让样式表重新生效，否则颜色不会变。"""
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def set_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return False
        self._collapsed = collapsed
        self.body.setVisible(not collapsed)
        self.title_label.setVisible(not collapsed)
        self.collapse_button.setText("«" if collapsed else "»")
        self.collapse_button.setToolTip("展开详情面板" if collapsed else "收起详情面板")
        self.setMinimumWidth(self.COLLAPSED_WIDTH if collapsed
                             else self.EXPANDED_MIN_WIDTH)
        self._layout.setContentsMargins(
            *(self._margins_collapsed if collapsed else self._margins_expanded))
        # 让 QSS 里的 [collapsed="true"] 规则生效：收起后整条变成粉色圆角块，
        # 按钮也换成实心强调色，一眼就能看出这里有东西、可以点。
        self.setProperty("collapsed", "true" if collapsed else "false")
        self.collapse_button.setProperty(
            "collapsed", "true" if collapsed else "false")
        self._repolish(self)
        self._repolish(self.collapse_button)
        self.collapsedChanged.emit(collapsed)
        return True

    def toggle_collapsed(self):
        self.set_collapsed(not self._collapsed)

    # ------------------------------------------------------------ 数据

    def set_tag_suggestions(self, names):
        completer = QCompleter(list(names), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tag_input.setCompleter(completer)

    def set_files(self, rows):
        self._rows = list(rows or [])
        has = bool(self._rows)
        self.btn_open.setEnabled(len(self._rows) == 1)
        self.btn_reveal.setEnabled(has)
        self.tag_input.setEnabled(has)

        if not has:
            self._current_id = None
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("选中一个视频\n即可显示缩略图")
            self.info.setText("—")
            self.tag_list.clear()
            return

        self._current_id = self._rows[0]["id"] if len(self._rows) == 1 else None
        if len(self._rows) == 1:
            row = self._rows[0]
            self.thumb.setText("正在生成缩略图…")
            self.info.setText(self._describe(row))
            self._fill_tags(row.get("tags") or "")
        else:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("已选中 %d 个视频" % len(self._rows))
            total = sum(r["size"] or 0 for r in self._rows)
            seconds = sum(r["duration"] or 0 for r in self._rows)
            self.info.setText("已选中 <b>%d</b> 个视频<br>合计 %s，总时长 %s"
                              % (len(self._rows), util.human_size(total),
                                 util.human_duration(seconds)))
            shared = None
            for row in self._rows:
                names = set((row.get("tags") or "").split("、")) - {""}
                shared = names if shared is None else (shared & names)
            self._fill_tags("、".join(sorted(shared or [], key=util.natural_key)))

    def _describe(self, row):
        resolution = ("%sx%s" % (row["width"], row["height"])
                      if row["width"] and row["height"] else "-")
        cover = "有" if row["has_cover"] else ("无" if row["probe_state"] == 1 else "未探测")
        return (
            "<b>%s</b><br>"
            "ID：%s　大小：%s<br>"
            "时长：%s　分辨率：%s<br>"
            "编码：%s / %s<br>"
            "封面：%s<br>"
            "修改时间：%s<br>"
            "<span style='color:%s'>%s</span>"
            % (row["name"], row["id"], util.human_size(row["size"]),
               util.human_duration(row["duration"]), resolution,
               row["vcodec"] or "-", row["acodec"] or "-", cover,
               report.mtime_text(row["mtime"]), PALETTE["TEXT_MUTED"], row["path"]))

    def _fill_tags(self, text):
        self.tag_list.clear()
        for name in [t for t in (text or "").split("、") if t]:
            self.tag_list.addItem(name)

    def set_thumbnail(self, file_id, image_path):
        if self._current_id != file_id:
            return
        if not image_path or not os.path.isfile(image_path):
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("缩略图生成失败\n（可能编码特殊，或 ffmpeg 不可用）")
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.thumb.setText("缩略图读取失败")
            return
        self.thumb.setPixmap(pixmap.scaledToWidth(
            max(200, self.thumb.width() - 8), Qt.SmoothTransformation))
        self.thumb.setText("")

    # ------------------------------------------------------------ 操作

    def _add_from_input(self):
        text = self.tag_input.text().strip()
        if not text or not self._rows:
            return
        names = [util.normalize_tag(p) for p in text.replace("，", ",").split(",")]
        names = [n for n in names if n]
        if not names:
            return
        self.tag_input.clear()
        self.addTagsRequested.emit(names)

    def _remove_selected(self):
        names = [item.text() for item in self.tag_list.selectedItems()]
        if names and self._rows:
            self.removeTagsRequested.emit(names)

    def current_row(self):
        return self._rows[0] if self._rows else None

    def selected_ids(self):
        return [r["id"] for r in self._rows]
