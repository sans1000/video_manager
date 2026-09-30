# -*- coding: utf-8 -*-
"""界面外观：粉 + 白主题。

所有颜色集中在 PALETTE 里，样式表（本文件）和卡片绘制（card.py）
都从这里取色 —— 否则改一次主题要改好几个文件，还容易改不齐。

配色思路：不是把整个界面刷成同一种粉，而是**拉开各区域的粉色深浅**，
让侧栏 / 网格 / 卡片 / 详情一眼能分出层次：

    顶部标签条   最深（粉-100）     读作标题栏
    侧栏、详情   中间（粉-50 偏白）
    网格底       浅粉（粉-50）      白卡片从这里跳出来
    卡片         纯白
    强调色       玫红（粉-500）     选中、按钮、勾选状态
"""

from __future__ import annotations

PALETTE = {
    # ---- 底色层次（刻意拉开深浅，否则整屏一个调子分不清区域）
    "BG":          "#fce7f3",   # 窗口内容底 = 中间那块「粉色画布」
    "SURFACE":     "#ffffff",   # 卡片、列表、输入框：纯白
    "PANEL":       "#ffffff",   # 侧栏 / 详情面板：白，靠粉色画布衬出来
    "GRID_BG":     "#fce7f3",   # 网格底 = 粉色画布，白卡片从这里跳出来
    "HEADER_BG":   "#fbcfe8",   # 顶部标签条：再深一档，读作标题栏
    "FRAME":       "#f9a8d4",   # 无边框窗口四周那圈边框：最深，边界清楚

    # ---- 线条
    "BORDER":      "#fbcfe8",
    "BORDER_SOFT": "#fdd9e9",
    "BORDER_STRONG": "#f9a8d4",

    # ---- 强调色
    "ACCENT":      "#ec4899",
    "ACCENT_DARK": "#db2777",
    "ACCENT_DEEP": "#9d174d",
    "ACCENT_DIM":  "#f472b6",

    # ---- 文字
    "TEXT":        "#3f2230",
    "TEXT_MUTED":  "#8a6478",
    "TEXT_FAINT":  "#b99aab",
    "TEXT_ON_ACCENT": "#ffffff",

    # ---- 状态
    "HOVER":       "#fce7f3",
    "SELECTED":    "#fce7f3",
    "TAB_IDLE":    "#fdd9e9",
    "CLOSE_HOVER": "#e81123",
}

# 卡片绘制（card.py）单独用到的一小撮
CARD_COLORS = {
    "card":            PALETTE["SURFACE"],
    "card_hover":      PALETTE["HOVER"],
    "card_selected":   PALETTE["SELECTED"],
    "card_border":     PALETTE["BORDER"],
    "card_border_sel": PALETTE["ACCENT"],
    "title":           PALETTE["TEXT"],
    "meta":            PALETTE["TEXT_MUTED"],
    "chip_bg":         "#fce7f3",
    "chip_text":       "#be185d",
    "placeholder_bg":  "#fce7f3",
    "placeholder_text": "#c9a7b9",
    "offline_bg":      "#ffe4e6",
    "offline_text":    "#be123c",
    "badge_bg":        (74, 20, 46, 165),
    "badge_text":      "#ffffff",
}

STYLESHEET_TEMPLATE = """
QWidget { font-size: 12px; color: __TEXT__; }
QMainWindow { background: __FRAME__; }          /* 无边框时四周那圈 */
QDialog { background: __BG__; }
QWidget#windowBody { background: __BG__; }

/* ---------------------------------------------------------- 顶部标签条 */
QWidget#tabRow {
    background: __HEADER_BG__;
    border-bottom: 1px solid __BORDER__;
}
QTabBar { background: transparent; }
QTabBar::tab {
    background: __TAB_IDLE__;
    color: __ACCENT_DEEP__;
    border: 1px solid __BORDER__;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    /* 上下 padding 必须对称、而且这里不能留 margin-top：
       Qt 把标签上的按钮居中在**原始标签矩形**里，而文字是按这里的
       margin + padding 居中。标签一旦有 margin-top（标签"浮"在条上、
       上面留一道空隙），两者就会差出半个空隙 —— 表现就是 × 比文字高 4px。
       所以顶部那道空隙挪到容器上（tabs.py 的 tab_row 上边距）。 */
    padding: 6px 14px;
    margin: 0 2px 0 0;
    max-width: 240px;
}
QTabBar::tab:hover { background: #fdd9e9; }
QTabBar::tab:selected {
    background: __SURFACE__;
    color: __ACCENT_DARK__;
    font-weight: 600;
    border-color: __BORDER_STRONG__;
}
/* 标签上的 × 是自己画的 QToolButton（见 tabs.py 的 _make_close_button），
   不用 Qt 自带的 close-button：自带那个在自定义样式表下没有图标，
   `image: none` 会把图标抹掉，只剩一块看不见的空白。 */
QToolButton#tabClose {
    background: transparent;
    border: none;
    border-radius: 9px;
    /* 2px 上内边距是**光学对齐**：✕ 这个字形在自己的行盒里偏高 1px，
       补上之后它的墨迹中心才和标签文字的墨迹中心落在同一条线上
       （tests/test_gui.py 里有一个量像素的测试盯着这 1px）。 */
    padding: 2px 0 0 0;
    color: __ACCENT_DEEP__;
    font-size: 12px;
    font-weight: 700;
}
QToolButton#tabClose:hover {
    background: __BORDER_STRONG__;
    color: __ACCENT_DARK__;
}
QToolButton#tabClose:pressed {
    background: __ACCENT_DEEP__;
    color: #ffffff;
}

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 15px;
    color: __TEXT_MUTED__;
}
QToolButton:hover { background: __SELECTED__; border-color: __BORDER__; color: __ACCENT_DARK__; }
QToolButton:pressed { background: __BORDER__; }
QToolButton:disabled { color: __TEXT_FAINT__; }

/* 窗口控制按钮 */
QWidget#windowControls { background: transparent; }
QFrame#cornerSeparator { color: __BORDER_STRONG__; background: __BORDER_STRONG__; margin: 6px 4px; }
QToolButton#winMin, QToolButton#winMax, QToolButton#winClose {
    border: none; border-radius: 4px; font-size: 12px; color: __TEXT_MUTED__;
    padding: 0;
}
QToolButton#winMin:hover, QToolButton#winMax:hover {
    background: __SELECTED__; color: __ACCENT_DEEP__;
}
QToolButton#winMin:pressed, QToolButton#winMax:pressed { background: __BORDER__; }
QToolButton#winClose:hover { background: __CLOSE_HOVER__; color: #ffffff; }
QToolButton#winClose:pressed { background: #c50f1f; color: #ffffff; }

/* ---------------------------------------------------------- 搜索条 */
QWidget#searchBar {
    background: __SURFACE__;
    border-bottom: 1px solid __BORDER__;
}
QLabel#emptyState { color: __TEXT_FAINT__; font-size: 13px; }

/* ---------------------------------------------------------- 网格 */
QListView { background: __GRID_BG__; border: none; outline: none; }
QListView::item { border: none; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical {
    background: __BORDER_STRONG__; border-radius: 5px; min-height: 32px; margin: 2px;
}
QScrollBar::handle:vertical:hover { background: __ACCENT_DIM__; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

/* ---------------------------------------------------------- 左侧/右侧面板 */
QWidget#leftPanel { background: __PANEL__; border-right: 1px solid __BORDER__; }

/* 详情面板按状态给两套互斥的规则。
   以前基础规则先铺一个白色矩形、收起规则再画粉色圆角矩形，
   结果四个角露出底下那层白 —— 圆角处出现白色缺口。 */
QWidget#detailPanel[collapsed="false"] {
    background: __PANEL__;
    border-left: 1px solid __BORDER__;
}
QWidget#detailPanel[collapsed="true"] {
    background: __TAB_IDLE__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: 10px;
}

/* 筛选区 */
QWidget#filterPanel { background: __PANEL__; border-top: 1px solid __BORDER__; }
/* 筛选表单的滚动区：透明无边框，滚动条样式沿用全局那套 */
QScrollArea#filterScroll,
QScrollArea#filterScroll > QWidget,
QScrollArea#filterScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}
QToolButton#sectionHeader {
    border: none; background: transparent; color: __TEXT_MUTED__;
    font-weight: 600; font-size: 11px; padding: 2px 0; text-align: left;
}
QToolButton#sectionHeader:hover { color: __ACCENT__; }
QLabel#filterBadge {
    color: __ACCENT_DEEP__; background: __SELECTED__; border-radius: 8px;
    padding: 1px 7px; font-size: 10px;
}
QPushButton#linkButton {
    border: none; background: transparent; color: __TEXT_FAINT__;
    font-size: 11px; padding: 1px 4px; min-height: 16px;
}
QPushButton#linkButton:hover { color: __ACCENT__; }
QLabel#chipGroupTitle { color: __TEXT_FAINT__; font-size: 10px; }
QLineEdit#filterKeyword {
    border: 1px solid __BORDER__; border-radius: 12px;
    padding: 3px 10px; background: __SURFACE__; font-size: 11px; min-height: 18px;
}
QLineEdit#filterKeyword:focus { border-color: __ACCENT_DIM__; }
QToolButton#chip {
    background: __SURFACE__; border: 1px solid __BORDER__; border-radius: 10px;
    padding: 2px 10px; color: __TEXT_MUTED__; font-size: 11px;
}
QToolButton#chip:hover { background: __HOVER__; border-color: __BORDER_STRONG__; color: __ACCENT_DEEP__; }
QToolButton#chip:checked {
    background: __ACCENT__; border-color: __ACCENT__; color: __TEXT_ON_ACCENT__; font-weight: 600;
}
QToolButton#chip:checked:hover { background: __ACCENT_DARK__; }

/* 详情面板右上角的收起/展开按钮：做成有底色的圆角胶囊，一眼能找到 */
QToolButton#collapseButton {
    border: 1px solid __BORDER_STRONG__;
    background: __SELECTED__;
    color: __ACCENT_DEEP__;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
}
QToolButton#collapseButton:hover {
    background: __ACCENT__; border-color: __ACCENT__; color: __TEXT_ON_ACCENT__;
}
QToolButton#collapseButton:pressed {
    background: __ACCENT_DARK__; border-color: __ACCENT_DARK__; color: __TEXT_ON_ACCENT__;
}
/* 收起状态：按钮变成实心强调色 */
QToolButton#collapseButton[collapsed="true"] {
    background: __ACCENT__; border-color: __ACCENT__; color: __TEXT_ON_ACCENT__;
}
QToolButton#collapseButton[collapsed="true"]:hover {
    background: __ACCENT_DARK__; border-color: __ACCENT_DARK__;
}

/* 收起后的那条窄边：整条染成粉色圆角块，四个角倒角 */

QLabel#panelTitle { font-weight: 600; color: __ACCENT_DEEP__; padding: 2px 0; }
QLabel#hint { color: __TEXT_FAINT__; font-size: 11px; }
QLabel#muted { color: __TEXT_MUTED__; font-size: 11px; }
QLabel#info { color: __TEXT__; padding: 6px 2px; }
QLabel#thumb {
    background: __SURFACE__; border: 1px solid __BORDER__; border-radius: 6px;
    color: __TEXT_FAINT__;
}

QListWidget {
    background: __SURFACE__; border: 1px solid __BORDER__; border-radius: 6px;
    padding: 3px;
}
QListWidget::item { padding: 5px 6px; border-radius: 4px; color: __TEXT__; }
QListWidget::item:selected { background: __SELECTED__; color: __ACCENT_DEEP__; }
QListWidget::item:hover { background: __HOVER__; }

QLineEdit, QComboBox {
    background: __SURFACE__; border: 1px solid __BORDER__; border-radius: 6px;
    padding: 4px 8px; min-height: 20px; color: __TEXT__;
}
QLineEdit:focus, QComboBox:focus { border-color: __ACCENT_DIM__; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: __SURFACE__; border: 1px solid __BORDER__;
    selection-background-color: __SELECTED__; selection-color: __ACCENT_DEEP__;
}

QPushButton {
    background: __SURFACE__; border: 1px solid __BORDER__; border-radius: 6px;
    padding: 5px 12px; min-height: 20px; color: __ACCENT_DEEP__;
}
QPushButton:hover { background: __HOVER__; border-color: __BORDER_STRONG__; }
QPushButton:pressed { background: __SELECTED__; }
QPushButton:disabled { color: __TEXT_FAINT__; background: __BG__; border-color: __BORDER_SOFT__; }
QPushButton#primary {
    background: __ACCENT__; border-color: __ACCENT__; color: __TEXT_ON_ACCENT__;
    font-weight: 600;
}
QPushButton#primary:hover { background: __ACCENT_DARK__; border-color: __ACCENT_DARK__; }

QGroupBox {
    border: 1px solid __BORDER__; border-radius: 6px; margin-top: 8px;
    background: __SURFACE__;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: __TEXT_MUTED__; }

QStatusBar { background: __SURFACE__; border-top: 1px solid __BORDER__; color: __TEXT_MUTED__; }
QStatusBar::item { border: none; }
QProgressBar {
    border: 1px solid __BORDER__; border-radius: 6px; background: __SURFACE__;
    height: 14px; text-align: center; color: __TEXT_MUTED__;
}
QProgressBar::chunk { background: __ACCENT__; border-radius: 5px; }

QMenu { background: __SURFACE__; border: 1px solid __BORDER__; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 4px; color: __TEXT__; }
QMenu::item:selected { background: __SELECTED__; color: __ACCENT_DEEP__; }
QMenu::separator { height: 1px; background: __BORDER__; margin: 4px 6px; }
QSplitter::handle { background: __BORDER__; width: 3px; }
QSplitter::handle:hover { background: __BORDER_STRONG__; }

QToolTip {
    background: __SURFACE__; color: __TEXT__;
    border: 1px solid __BORDER_STRONG__; padding: 4px 6px;
}

QCheckBox { color: __TEXT__; }
QCheckBox::indicator {
    width: 13px; height: 13px; border: 1px solid __BORDER_STRONG__;
    border-radius: 3px; background: __SURFACE__;
}
QCheckBox::indicator:checked { background: __ACCENT__; border-color: __ACCENT__; }
"""


def stylesheet():
    """把调色板填进样式表模板。"""
    text = STYLESHEET_TEMPLATE
    for key, value in PALETTE.items():
        text = text.replace("__%s__" % key, value)
    return text


STYLESHEET = stylesheet()
