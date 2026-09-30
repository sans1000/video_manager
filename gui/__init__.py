# -*- coding: utf-8 -*-
"""video_manager 的图形界面层。

这一层只做「显示 + 交互」，所有实际逻辑仍然在 core 里，
所以命令行 vm.py 和界面 vm_gui.py 用的是同一套代码、同一个索引库。

结构（标签页重构后）：
    session.py       全局唯一资源 + 事件总线（Engine / 缩略图线程池 / 标签）
    query.py         一个标签页的查询状态（筛选、排序、分页位置）
    page.py          一个标签页 = 状态 + 网格 + 缩略图调度
    tabs.py          浏览器式标签页容器（QTabBar + QStackedWidget）
    card.py          卡片绘制（QStyledItemDelegate）
    model.py         网格数据模型
    sidebar.py       左侧标签导航
    filters.py       筛选面板（作用于当前页）
    detail.py        右侧详情面板（所有页共用）
    main_window.py   组装 + 应用级操作（无菜单栏）

PyQt5 是可选依赖：没装时 vm.py 照常能用，只有 vm_gui.py 会提示怎么装。
"""

from __future__ import annotations

__all__ = ["app", "card", "detail", "dialogs", "engine", "filters",
           "main_window", "model", "page", "query", "session", "sidebar",
           "style", "tabs", "thumbs", "workers"]
