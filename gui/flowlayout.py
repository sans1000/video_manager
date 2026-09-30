# -*- coding: utf-8 -*-
"""可自动换行的流式布局。

放筛选 chip 用：左侧栏只有 200 多像素宽，一排 chip 放不下就得换行，
而 QHBoxLayout 不会换行、QGridLayout 又得手动算列数。
这是 Qt 官方 Flow Layout 示例的等价实现。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=4):
        super(FlowLayout, self).__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    # ---------------------------------------------------------- 必须实现

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super(FlowLayout, self).setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    # ---------------------------------------------------------- 排版

    def _layout(self, rect, test_only):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        spacing = self.spacing()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > area.right() and line_height > 0:
                x = area.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class FlowWidget(QWidget):
    """装着 FlowLayout 的控件，高度随宽度自适应。"""

    def __init__(self, parent=None, spacing=4, margin=0):
        super(FlowWidget, self).__init__(parent)
        self.flow = FlowLayout(self, margin=margin, spacing=spacing)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def add(self, widget):
        self.flow.addWidget(widget)

    def clear(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.flow.heightForWidth(width)

    def sizeHint(self):
        width = self.width() or 200
        return QSize(width, self.flow.heightForWidth(width))

    def minimumSizeHint(self):
        return self.flow.minimumSize()
