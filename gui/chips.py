# -*- coding: utf-8 -*-
"""筛选 chip 组件。

把「年份 / 分辨率 / 时长 / 封面」那排丑陋的下拉框换成一句话就能看懂的
分段按钮（pill），选中的高亮。同组的 chip 互斥（单选），
「全部」就是 value=None 的那一个。
"""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .flowlayout import FlowWidget


class Chip(QToolButton):
    """一个可切换的小胶囊按钮。"""

    def __init__(self, text, parent=None, tooltip=None):
        super(Chip, self).__init__(parent)
        self.setText(text)
        self.setObjectName("chip")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        if tooltip:
            self.setToolTip(tooltip)

    def sizeHint(self):
        hint = super(Chip, self).sizeHint()
        # 让胶囊看起来圆润一点，别贴着字
        return hint.__class__(hint.width() + 10, max(hint.height(), 22))


class ChipGroup(QWidget):
    """一组互斥的 chip，比如「全部 / 720p+ / 1080p+ / 4K+」。

    不带标题 —— 标题由调用方放在旁边（用网格布局对齐），
    这样比「标题占一整行 + 下面一排 chip」省下不少竖向空间。
    """

    changed = pyqtSignal()

    def __init__(self, options, parent=None, tooltip_of=None):
        super(ChipGroup, self).__init__(parent)
        self._options = list(options)
        self._pairs = []
        self._value = self._options[0][1] if self._options else None
        self._tooltip_of = tooltip_of
        # 高度随宽度变化要能一路传到外层：窄的时候 chip 会换行、变高
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self.flow = FlowWidget(spacing=4)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.flow)
        self._build()

    # ---------------------------------------------------------- 构建

    def _build(self):
        self._pairs = []
        self.flow.clear()
        for label, value in self._options:
            tip = self._tooltip_of(value) if self._tooltip_of else None
            chip = Chip(label, self, tooltip=tip)
            chip.setAutoExclusive(True)
            chip.clicked.connect(self._on_clicked)
            self.flow.add(chip)
            self._pairs.append((chip, value))
        self._apply_value()

    def _apply_value(self):
        for chip, value in self._pairs:
            chip.setChecked(value == self._value)

    def _on_clicked(self):
        for chip, value in self._pairs:
            if chip.isChecked():
                self._value = value
                break
        self.changed.emit()

    # ---------------------------------------------------------- 取值

    def value(self):
        return self._value

    def set_value(self, value):
        self._value = value
        self._apply_value()

    def set_options(self, options):
        """年份这种选项会变的组，重建 chip。原来的选中值能保留就保留。"""
        keep = self._value
        self._options = list(options)
        if all(keep != value for _label, value in self._options):
            keep = self._options[0][1] if self._options else None
        self._value = keep
        self._build()

    def reset(self):
        self.set_value(self._options[0][1] if self._options else None)

    def is_default(self):
        return not self._options or self._value == self._options[0][1]
