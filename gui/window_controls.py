# -*- coding: utf-8 -*-
"""窗口控制按钮（收起 / 放大还原 / 关闭）。

这三个按钮并到标签条右端，不额外占一行 —— 和 Chrome 的做法一样。
无边框窗口下它们是唯一的窗口操作入口，所以：

  · 关闭按钮有独立的红色悬停态，别和「最小化」点混；
  · 放大按钮的图标随状态在 □ / ❐ 之间切换；
  · 都有 tooltip 和快捷键提示。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QToolButton, QWidget


class WindowControls(QWidget):
    minimizeRequested = pyqtSignal()
    maximizeRequested = pyqtSignal()
    closeRequested = pyqtSignal()

    def __init__(self, parent=None):
        super(WindowControls, self).__init__(parent)
        self.setObjectName("windowControls")

        self.min_button = self._make("─", "最小化", "winMin")
        self.max_button = self._make("□", "最大化 / 还原", "winMax")
        self.close_button = self._make("✕", "关闭 (Ctrl+Q)", "winClose")

        self.min_button.clicked.connect(self.minimizeRequested.emit)
        self.max_button.clicked.connect(self.maximizeRequested.emit)
        self.close_button.clicked.connect(self.closeRequested.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

    def _make(self, text, tooltip, name):
        button = QToolButton()
        button.setText(text)
        button.setObjectName(name)
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(30, 24)
        return button

    def set_maximized(self, maximized):
        self.max_button.setText("❐" if maximized else "□")
        self.max_button.setToolTip("向下还原" if maximized else "最大化")

    def set_available(self, available):
        """原生标题栏模式下没有「最大化」概念由我们管，但按钮仍可用。"""
        for button in (self.min_button, self.max_button, self.close_button):
            button.setEnabled(bool(available))
