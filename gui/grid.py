# -*- coding: utf-8 -*-
"""网格尺寸计算：固定每行 2 个，卡片和缩略图跟着窗口宽度走。

原来走的是「格子固定 232px、列数随窗口自适应」，
现在反过来：「每行固定 2 个、格子随窗口自适应」。

尺寸由三处共用，所以抽成一个对象：
    page     —— 窗口变化时重新计算并应用
    model    —— 回答 SizeHintRole（每格多大）
    delegate —— 绘制时按实际尺寸画缩略图和文字
"""

from __future__ import annotations

from PyQt5.QtCore import QSize

COLUMNS = 2          # 每行固定几个
MARGIN = 8           # 网格四周留白
GAP = 8              # 卡片之间的缝
PAD = 8              # 卡片内部边距
TEXT_BLOCK = 76      # 缩略图下面那块文字区：标题两行 + 元信息 + 标签

MIN_CARD_WIDTH = 48   # 只是防止算出 0 或负数，**不参与**「放不放得下」的判断
PREFERRED_CARD_WIDTH = 200   # 只用来参考「这么窄就不太好看了」，不做硬钳制

# 抽帧宽度档位：按卡片宽度往上取一档，避免用户拖窗口时生成无数种尺寸的缓存
THUMB_BUCKETS = (360, 480, 640, 800, 960)
DEFAULT_BUCKET = 360


def bucket_for(card_width, device_ratio=1.0):
    """按卡片显示宽度选一个抽帧档位（考虑 HiDPI 缩放）。"""
    try:
        ratio = max(1.0, float(device_ratio or 1.0))
    except (TypeError, ValueError):
        ratio = 1.0
    want = float(card_width or 0) * ratio
    for bucket in THUMB_BUCKETS:
        if want <= bucket:
            return bucket
    return THUMB_BUCKETS[-1]


def card_metrics(view_width, columns=COLUMNS, margin=MARGIN, gap=GAP, safety=2):
    """按视图宽度算 (卡片宽, 卡片高, 缩略图高)。

    view_width 是 **viewport 宽度**（已扣掉 viewportMargins 和滚动条）。

    三个容易踩的点（前两个都真踩过）：
      1. 每列要减掉一个完整的 gap 而不是 gap/2：视图里每一格的宽度是
         「卡宽 + gap」，N 格就是 N×(卡宽+gap)。少算这一点，最后一张卡
         就会被挤到下一行。
      2. 再留 2px 余量：正好卡在边界上时，Qt 的取整会让第二张放不下。
      3. **不要把卡片宽度钳到一个「好看」的下限**：视口一窄，钳制会让
         实际列数少于用户选的数量（选 4 列只摆 3 列），而且菜单还显示 4。
         这里只留一个防负数的小地板，保证「选了 N 列就一定是 N 列」。

    卡片高 = 缩略图高（16:9）+ 固定的文字区高度，
    所以窗口一宽，缩略图跟着变大，文字区保持不变。
    """
    columns = max(1, int(columns))
    usable = int(view_width) - gap * columns - max(0, int(safety))
    card_width = max(MIN_CARD_WIDTH, usable // columns)
    thumb_height = int(round(card_width * 9.0 / 16.0))
    card_height = thumb_height + TEXT_BLOCK
    return card_width, card_height, thumb_height


class GridMetrics(object):
    """三处共用的当前尺寸。"""

    def __init__(self, columns=COLUMNS):
        self.columns = max(1, int(columns))
        self.card_width = 0
        self.card_height = 0
        self.thumb_height = 0
        self.bucket = DEFAULT_BUCKET
        self.update(600)                      # 先给一个合理初值

    def set_columns(self, columns):
        """改每行显示几个。返回是否真的变了。"""
        columns = max(1, int(columns))
        if columns == self.columns:
            return False
        self.columns = columns
        self.card_width = -1              # 强制下一次 update 认为尺寸变了
        return True

    def update(self, view_width, device_ratio=1.0):
        """返回「尺寸是否变了」，调用方据此决定要不要重排。"""
        width, height, thumb = card_metrics(view_width, self.columns)
        changed = (width, height) != (self.card_width, self.card_height)
        self.card_width, self.card_height, self.thumb_height = width, height, thumb
        self.bucket = bucket_for(width, device_ratio)
        return changed or width <= 0

    def cell_size(self):
        """格子尺寸：卡片 + 一条缝。视图布局按这个算。"""
        return QSize(self.card_width + GAP, self.card_height + GAP)

    def card_size(self):
        return QSize(self.card_width, self.card_height)

    def size_hint(self):
        """给视图用的条目尺寸。

        直接返回**格子**尺寸而不是卡片尺寸：这样卡片之间的缝由绘制时
        让出来，不用指望 QListView 的 setGridSize 被采纳
        （实测在 IconMode 下它会被 sizeHint 盖掉）。
        """
        return self.cell_size()

    def columns_for_width(self, width):
        """按当前卡宽反算这一行能放几个 —— 只用于自检。"""
        usable = width - MARGIN * 2 + GAP
        return max(1, usable // (self.card_width + GAP))
