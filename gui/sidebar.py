# -*- coding: utf-8 -*-
"""TagSidebar：左侧标签导航。

交互（按用户要求）：
  · 单击标签        -> 在新标签页打开该标签
  · Ctrl + 单击     -> 把标签加到「当前页」的筛选里（多个标签 = 同时具备）
  · 右键            -> 重命名 / 删除标签
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QApplication, QLabel, QListWidget, QListWidgetItem,
                             QMenu, QVBoxLayout, QWidget)

from core import util

ALL_KEY = "__all__"
UNTAGGED_KEY = "__untagged__"


class TagSidebar(QWidget):
    homeActivated = pyqtSignal()
    untaggedActivated = pyqtSignal()
    tagActivated = pyqtSignal(str)              # 开新页
    tagFilterRequested = pyqtSignal(str)        # Ctrl+单击：加到当前页
    renameRequested = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    exportRequested = pyqtSignal()

    def __init__(self, parent=None):
        super(TagSidebar, self).__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setMinimumHeight(140)          # 窗口变小时别被筛选区挤没
        self.list.itemClicked.connect(self._on_clicked)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        title = QLabel("标签")
        title.setObjectName("panelTitle")
        self.hint = QLabel("单击开新页 · Ctrl+单击加到当前页")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 9, 8, 4)
        layout.setSpacing(4)
        layout.addWidget(title)
        layout.addWidget(self.hint)
        layout.addWidget(self.list, 1)

    # ------------------------------------------------------------ 数据

    def set_tags(self, tags, active_tags=None):
        active = set(active_tags or [])
        self.list.blockSignals(True)
        self.list.clear()

        item = QListWidgetItem("全部视频")
        item.setData(Qt.UserRole, ALL_KEY)
        self.list.addItem(item)

        item = QListWidgetItem("未打标签")
        item.setData(Qt.UserRole, UNTAGGED_KEY)
        self.list.addItem(item)

        for tag in tags:
            entry = QListWidgetItem("%s  (%d)" % (tag["name"], tag["n"]))
            entry.setData(Qt.UserRole, tag["name"])
            entry.setToolTip("%s\n占用 %s，总时长 %s"
                             % (tag["name"], util.human_size(tag["size"]),
                                util.human_duration(tag["duration"])))
            if tag["name"] in active:
                font = QFont()
                font.setBold(True)
                entry.setFont(font)
                entry.setToolTip(entry.toolTip() + "\n（当前页正在按它筛选）")
            self.list.addItem(entry)
        self.list.blockSignals(False)

    def _value_at(self, item):
        return item.data(Qt.UserRole) if item else None

    # ------------------------------------------------------------ 交互

    def _on_clicked(self, item):
        value = self._value_at(item)
        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)
        if value == ALL_KEY:
            self.homeActivated.emit()
            return
        if value == UNTAGGED_KEY:
            if ctrl:
                self.tagFilterRequested.emit(UNTAGGED_KEY)
            else:
                self.untaggedActivated.emit()
            return
        if value:
            if ctrl:
                self.tagFilterRequested.emit(value)
            else:
                self.tagActivated.emit(value)

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        value = self._value_at(item)
        menu = QMenu(self)
        if value and value not in (ALL_KEY, UNTAGGED_KEY):
            menu.addAction("在新标签页打开",
                           lambda: self.tagActivated.emit(value))
            menu.addAction("加到当前页筛选",
                           lambda: self.tagFilterRequested.emit(value))
            menu.addSeparator()
            menu.addAction("重命名 / 合并标签…",
                           lambda: self.renameRequested.emit(value))
            menu.addAction("删除标签",
                           lambda: self.deleteRequested.emit(value))
            menu.addSeparator()
        menu.addAction("导出标签备份…", self.exportRequested.emit)
        menu.exec_(self.list.viewport().mapToGlobal(pos))
