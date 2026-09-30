# -*- coding: utf-8 -*-
"""网格数据模型：把查询结果喂给 QListView（IconMode）。

网格没有「列」的概念，一个条目就是一个视频，
具体长什么样交给 VideoCardDelegate 去画，尺寸由 GridMetrics 统一给。
"""

from __future__ import annotations

from PyQt5.QtCore import QAbstractListModel, QModelIndex, Qt

from .grid import GridMetrics


class VideoGridModel(QAbstractListModel):
    def __init__(self, metrics=None, parent=None):
        super(VideoGridModel, self).__init__(parent)
        self.metrics = metrics or GridMetrics()
        self._rows = []
        self._total = 0

    # ---------------------------------------------------------- 数据装载

    def set_rows(self, rows, total=None):
        self.beginResetModel()
        self._rows = list(rows)
        self._total = int(total) if total is not None else len(self._rows)
        self.endResetModel()

    def append_rows(self, rows):
        if not rows:
            return
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def clear(self):
        self.set_rows([], 0)

    def total(self):
        return self._total

    def loaded(self):
        return len(self._rows)

    def row_at(self, index):
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def rows_at(self, indexes):
        """选中的 index 列表 -> 去重后的行。"""
        seen, out = set(), []
        for index in indexes:
            if not index.isValid():
                continue
            row = index.row()
            if row in seen:
                continue
            seen.add(row)
            item = self.row_at(row)
            if item is not None:
                out.append(item)
        return out

    def index_of_id(self, file_id):
        for i, row in enumerate(self._rows):
            if row["id"] == file_id:
                return self.index(i, 0)
        return QModelIndex()

    # ---------------------------------------------------------- Qt 接口

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.DisplayRole:
            return row["name"]
        if role == Qt.UserRole:
            return row
        if role == Qt.SizeHintRole:
            # 尺寸随窗口变化，所以每次都问 metrics 要最新的
            return self.metrics.size_hint()
        if role == Qt.ToolTipRole:
            return ("%s\n\n标签：%s\n路径：%s"
                    % (row["name"], row.get("tags") or "（无标签）", row["path"]))
        return None
