# -*- coding: utf-8 -*-
"""视频卡片的绘制（缩略图网格的核心）。

用 QStyledItemDelegate 而不是「给每个条目塞一个自定义控件」，
是因为 Qt 只会为**可见的**卡片调 paint()：
8 万个文件也不会因为创建 8 万个控件而卡死。

分工：
  - 这里只负责「画」；
  - 什么时候去抽帧由 VideoPage 根据可见范围决定（见 page.py）。
"""

from __future__ import annotations

import os
from collections import OrderedDict

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                         QPen, QPixmap)
from PyQt5.QtWidgets import QStyle, QStyledItemDelegate

from core import util

from .grid import GAP, GridMetrics, PAD
from .style import CARD_COLORS

CORNER = 8

# 卡片用到的颜色统一从 style.CARD_COLORS 取，改主题不用满仓库找色值
COLOR_BG = QColor(CARD_COLORS["card"])
COLOR_BG_HOVER = QColor(CARD_COLORS["card_hover"])
COLOR_BG_SELECTED = QColor(CARD_COLORS["card_selected"])
COLOR_BORDER = QColor(CARD_COLORS["card_border"])
COLOR_BORDER_SELECTED = QColor(CARD_COLORS["card_border_sel"])
COLOR_TITLE = QColor(CARD_COLORS["title"])
COLOR_META = QColor(CARD_COLORS["meta"])
COLOR_CHIP_BG = QColor(CARD_COLORS["chip_bg"])
COLOR_CHIP_TEXT = QColor(CARD_COLORS["chip_text"])
COLOR_PLACEHOLDER = QColor(CARD_COLORS["placeholder_bg"])
COLOR_PLACEHOLDER_TEXT = QColor(CARD_COLORS["placeholder_text"])
COLOR_OFFLINE = QColor(CARD_COLORS["offline_bg"])
COLOR_OFFLINE_TEXT = QColor(CARD_COLORS["offline_text"])
COLOR_BADGE = QColor(*CARD_COLORS["badge_bg"])


class PixmapCache(object):
    """内存里的卡片图缓存（LRU）。

    只缓存**已经裁成卡片大小**的图，不然 360px 的原图一张就 290KB，
    几百张就是几百 MB。
    """

    def __init__(self, capacity=250):
        self.capacity = capacity
        self._items = OrderedDict()

    def get(self, key):
        pixmap = self._items.get(key)
        if pixmap is not None:
            self._items.move_to_end(key)
        return pixmap

    def put(self, key, pixmap):
        self._items[key] = pixmap
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)

    def clear(self):
        self._items.clear()

    def __len__(self):
        return len(self._items)


def crop_to(pixmap, width, height):
    """按「填满并居中裁切」缩放，得到严格的 width×height。"""
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


def elide_lines(font_metrics, text, width, max_lines=2):
    """把文本折成最多 max_lines 行，放不下的用省略号。中文按字符折行即可。"""
    text = (text or "").strip()
    if not text:
        return []
    lines, rest = [], text
    for _ in range(max_lines):
        if not rest:
            break
        low, high = 0, len(rest)
        while low < high:
            mid = (low + high + 1) // 2
            if font_metrics.horizontalAdvance(rest[:mid]) <= width:
                low = mid
            else:
                high = mid - 1
        if low >= len(rest):
            lines.append(rest)
            rest = ""
        else:
            lines.append(rest[:low])
            rest = rest[low:]
    if rest:
        if lines:
            lines[-1] = font_metrics.elidedText(lines[-1] + rest, Qt.ElideRight, width)
        else:
            lines = [font_metrics.elidedText(rest, Qt.ElideRight, width)]
    return lines


class VideoCardDelegate(QStyledItemDelegate):
    def __init__(self, session, metrics=None, parent=None):
        super(VideoCardDelegate, self).__init__(parent)
        self.session = session
        self.metrics = metrics or GridMetrics()
        self.cache = PixmapCache()

        self.title_font = QFont()
        self.title_font.setPointSize(9)
        self.small_font = QFont()
        self.small_font.setPointSize(8)
        self.badge_font = QFont()
        self.badge_font.setPointSize(8)
        self.badge_font.setBold(True)

    # ---------------------------------------------------------- 尺寸

    def sizeHint(self, option, index):
        return self.metrics.size_hint()

    # ---------------------------------------------------------- 缩略图

    def thumb_pixmap(self, row, width, height):
        """取已裁好的卡片缩略图；没有就返回 None（会画占位图）。"""
        if not self.session.thumbs.cache.ffmpeg:
            return None
        path = self.session.thumbs.cache.path_for(row, self.metrics.bucket)
        if not os.path.isfile(path):
            return None
        key = (path, width, height)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return None
        cropped = crop_to(pixmap, width, height)
        self.cache.put(key, cropped)
        return cropped

    def forget(self, row):
        """缩略图重新生成后，把旧的内存缓存丢掉。"""
        if not self.session.thumbs.cache.ffmpeg:
            return
        path = self.session.thumbs.cache.path_for(row, self.metrics.bucket)
        for key in [k for k in self.cache._items if k[0] == path]:
            self.cache._items.pop(key, None)

    def forget_all(self):
        self.cache.clear()

    # ---------------------------------------------------------- 绘制

    def paint(self, painter, option, index):
        row = index.data(Qt.UserRole)
        if not row:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 格子里已经含了一条缝（见 GridMetrics.cell_size），所以右边和下边让出来就行
        rect = option.rect.adjusted(0, 0, -GAP, -GAP)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        # 卡片底
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()),
                            float(rect.width()), float(rect.height()),
                            CORNER, CORNER)
        painter.fillPath(path, COLOR_BG_SELECTED if selected else
                         (COLOR_BG_HOVER if hovered else COLOR_BG))
        painter.setPen(QPen(COLOR_BORDER_SELECTED if selected else COLOR_BORDER, 1))
        painter.drawPath(path)

        # 缩略图：高度跟着卡片宽度走（16:9）
        thumb_rect = QRect(rect.x() + PAD, rect.y() + PAD,
                           rect.width() - 2 * PAD, self.metrics.thumb_height)
        self._draw_thumb(painter, thumb_rect, row)

        # 标题（两行）
        painter.setFont(self.title_font)
        painter.setPen(COLOR_TITLE)
        metrics = QFontMetrics(self.title_font)
        title_rect = QRect(thumb_rect.x(), thumb_rect.bottom() + 6,
                           thumb_rect.width(), metrics.height() * 2)
        lines = elide_lines(metrics, row["name"], title_rect.width(), 2)
        for i, line in enumerate(lines):
            painter.drawText(title_rect.x(),
                             title_rect.y() + metrics.ascent() + i * metrics.height(),
                             line)

        # 元信息：分辨率 · 大小 · 时长
        painter.setFont(self.small_font)
        painter.setPen(COLOR_META)
        small = QFontMetrics(self.small_font)
        resolution = ("%sx%s" % (row["width"], row["height"])
                      if row["width"] and row["height"] else "未探测")
        meta = "%s · %s · %s" % (resolution, util.human_size(row["size"]),
                                 util.human_duration(row["duration"]))
        meta_y = title_rect.bottom() + small.ascent()
        painter.drawText(thumb_rect.x(), meta_y,
                         small.elidedText(meta, Qt.ElideRight, thumb_rect.width()))

        # 标签 chips
        self._draw_chips(painter, thumb_rect.x(), meta_y + 6,
                         thumb_rect.width(), row.get("tags") or "")

        painter.restore()

    def _draw_thumb(self, painter, rect, row):
        pixmap = self.thumb_pixmap(row, rect.width(), rect.height())
        thumb_path = QPainterPath()
        thumb_path.addRoundedRect(float(rect.x()), float(rect.y()),
                                  float(rect.width()), float(rect.height()), 6, 6)
        if pixmap is None:
            offline = not row.get("available", True)
            painter.fillPath(thumb_path, COLOR_OFFLINE if offline else COLOR_PLACEHOLDER)
            painter.setFont(self.small_font)
            painter.setPen(COLOR_OFFLINE_TEXT if offline else COLOR_PLACEHOLDER_TEXT)
            if offline:
                text = "文件不可用\n（磁盘未接或已移走）"
            elif row.get("probe_state") == -1:
                text = "无法读取"
            elif not self.session.thumbs.cache.ffmpeg:
                text = "未安装 ffmpeg"
            else:
                text = "生成中…"
            painter.drawText(rect, Qt.AlignCenter, text)
            return

        painter.save()
        painter.setClipPath(thumb_path)
        painter.drawPixmap(rect, pixmap)
        painter.restore()

        # 时长徽章
        duration = util.human_duration(row["duration"])
        if duration and duration != "-":
            painter.setFont(self.badge_font)
            metrics = QFontMetrics(self.badge_font)
            text_width = metrics.horizontalAdvance(duration) + 12
            badge = QRect(rect.right() - text_width - 4, rect.bottom() - 20,
                          text_width, 17)
            badge_path = QPainterPath()
            badge_path.addRoundedRect(float(badge.x()), float(badge.y()),
                                      float(badge.width()), float(badge.height()), 4, 4)
            painter.fillPath(badge_path, COLOR_BADGE)
            painter.setPen(QColor(CARD_COLORS["badge_text"]))
            painter.drawText(badge, Qt.AlignCenter, duration)

    def _draw_chips(self, painter, x, y, width, tags_text):
        names = [t for t in (tags_text or "").split("、") if t]
        if not names:
            return
        painter.setFont(self.small_font)
        metrics = QFontMetrics(self.small_font)
        cursor = x
        limit = x + width
        for i, name in enumerate(names):
            remaining = len(names) - i
            label = name
            text_width = metrics.horizontalAdvance(label) + 12
            if cursor + text_width > limit:
                label = "+%d" % remaining
                text_width = metrics.horizontalAdvance(label) + 12
                if cursor + text_width > limit:
                    return
            chip = QRect(cursor, y, text_width, 16)
            chip_path = QPainterPath()
            chip_path.addRoundedRect(float(chip.x()), float(chip.y()),
                                     float(chip.width()), float(chip.height()), 8, 8)
            painter.fillPath(chip_path, COLOR_CHIP_BG)
            painter.setPen(COLOR_CHIP_TEXT)
            painter.drawText(chip, Qt.AlignCenter, label)
            cursor += text_width + 4
            if cursor >= limit:
                return
