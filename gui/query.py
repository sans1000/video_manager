# -*- coding: utf-8 -*-
"""QueryState：一个标签页的查询状态。

重构的核心就是把「一份全局状态」变成「每页一份」：
筛选、排序、分页位置全部装在这里，标签页之间互不干扰。

顺带一个好处：这个对象是纯数据的，可以直接序列化，
以后要做「重启恢复打开的标签页」不用再动结构。
"""

from __future__ import annotations

from .engine import SORT_OPTIONS

SORT_LABELS = dict(SORT_OPTIONS)
SORT_KEYS = [key for _label, key in SORT_OPTIONS]


class QueryState(object):
    def __init__(self, tags=None, untagged=False, keyword=None):
        self.tags = list(tags or [])      # 多个标签 = 同时具备
        self.untagged = bool(untagged)    # 只看没打标签的
        self.keyword = keyword
        self.year = None
        self.min_height = None
        self.min_duration = None
        self.has_cover = None
        self.sort = "name"
        self.desc = False
        self.page_size = 120
        self.offset = 0                   # 已加载条数，翻页用

    # ---------------------------------------------------------- 条件

    def filters(self):
        """喂给 Engine.query 的参数字典。"""
        return {
            "keyword": self.keyword,
            "tags": list(self.tags),
            "untagged": self.untagged,
            "year": self.year,
            "min_height": self.min_height,
            "min_duration": self.min_duration,
            "has_cover": self.has_cover,
        }

    def has_extra_filters(self):
        return any([self.keyword, self.year, self.min_height,
                    self.min_duration, self.has_cover is not None])

    def is_plain(self):
        """就是「全部视频」，没有任何条件。"""
        return not self.tags and not self.untagged and not self.has_extra_filters()

    def reset_filters(self):
        self.keyword = None
        self.year = None
        self.min_height = None
        self.min_duration = None
        self.has_cover = None

    # ---------------------------------------------------------- 标签

    def toggle_tag(self, name):
        if name in self.tags:
            self.tags.remove(name)
            return False
        self.tags.append(name)
        return True

    def set_tag(self, name):
        self.tags = [name] if name else []
        self.untagged = False

    def clear_tags(self):
        self.tags = []

    # ---------------------------------------------------------- 显示

    def title(self):
        """标签页标题里显示的名字（不含计数）。"""
        parts = list(self.tags)
        if self.untagged:
            parts.append("未打标签")
        if not parts:
            parts = ["全部视频"]
        text = " + ".join(parts)
        if self.keyword:
            text += " · “%s”" % self.keyword
        return text

    def describe(self):
        """状态栏用的条件描述。"""
        bits = []
        if self.tags:
            bits.append("标签：" + "、".join(self.tags))
        if self.untagged:
            bits.append("未打标签")
        if self.keyword:
            bits.append("关键词：“%s”" % self.keyword)
        if self.year:
            bits.append("%s 年" % self.year)
        if self.min_height:
            bits.append("≥%dp" % self.min_height)
        if self.min_duration:
            bits.append("≥%d 分钟" % (self.min_duration // 60))
        if self.has_cover is True:
            bits.append("有封面")
        elif self.has_cover is False:
            bits.append("无封面")
        return "，".join(bits) if bits else "全部视频"

    def sort_label(self):
        return SORT_LABELS.get(self.sort, self.sort)
