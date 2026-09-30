# -*- coding: utf-8 -*-
"""无边框窗口：去掉 Windows 自带的标题栏，自己做窗口按钮。

实现要点（窗口标志和交互全是 Qt 层面；只有圆角那一处要碰 Windows API）：

1. `Qt.FramelessWindowHint` 去掉原生边框和标题栏；
2. 四边留 `EDGE` 像素的空白边距，鼠标移到那里改指针形状并支持拖拽缩放
   —— 这段边距属于 QMainWindow 自己，没有被子控件盖住，所以事件能收到；
3. 拖动窗口由标签条空白处触发（MainWindow 装事件过滤器转发过来）；
4. 最大化**不用** showMaximized()，而是直接贴到屏幕可用区域：
   无边框窗口在 Windows 上 showMaximized() 有盖住任务栏的老问题，
   自己算 availableGeometry 更可靠。

已知代价（相对原生标题栏）：
  · 没有 Aero Snap（拖到屏幕边缘自动分屏）和系统动画；
  · 窗口阴影要 Windows 11 才跟着圆角一起回来（见下）。
换来的是顶部完全按自己的设计走。

窗口圆角：交给 Windows 11 的 DWM 去做（`DWMWA_WINDOW_CORNER_PREFERENCE`），
而不是自己开透明背景 + `setMask` 画。理由：
  · 抗锯齿由 DWM 处理，`setMask` 出来的是锯齿边；
  · 系统连**客户区**一起裁，子控件不会从圆角里"戳"出来；
  · 顺带把系统投影也带回来了（无边框窗口本来丢掉的就是它）。
Win10 及更早没有这个属性，调用会失败，那时窗口就是方的。

不确定能不能接受时，用 `py vm_gui.py --native-frame` 可以立刻回到原生标题栏。
"""

from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow

EDGE = 6                     # 四边缩放热区宽度
MIN_WIDTH = 820
MIN_HEIGHT = 520
DEFAULT_FRACTION = 0.5       # 默认窗口占屏幕可用区域的比例

# ---- Windows 11 的 DWM 窗口圆角
DWMWA_WINDOW_CORNER_PREFERENCE = 33
CORNER_DEFAULT = 0           # 交给系统（原生标题栏模式用这个）
CORNER_DONOTROUND = 1
CORNER_ROUND = 2
CORNER_ROUND_SMALL = 3


def corner_preference(frameless=True, maximized=False):
    """该给窗口设哪种圆角。

    最大化时**不能**圆角：窗口是贴着屏幕边铺满的，切了圆角会把屏幕的四个角
    露出桌面来（Windows 自己的最大化窗口也是方的）。
    原生标题栏模式就更不用我们操心，交回系统按它自己的规矩办。
    """
    if not frameless:
        return CORNER_DEFAULT
    return CORNER_DONOTROUND if maximized else CORNER_ROUND


def apply_corner_preference(hwnd, preference):
    """把圆角偏好告诉 Windows；做不到就返回 False，不抛异常。

    这个属性是 Windows 11（build 22000）才有的，老系统和别的平台上调用会失败
    —— 那就保持方角，不值得为它去自绘（见模块开头的取舍说明）。
    """
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                  # 非 Windows
        return False
    try:
        dwm = ctypes.windll.dwmapi
    except (AttributeError, OSError):
        return False
    try:
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        value = ctypes.c_int(int(preference))
        result = dwm.DwmSetWindowAttribute(
            wintypes.HWND(int(hwnd)), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:                                  # 老系统没有这个属性
        return False
    return result == 0


def initial_window_size(area_width, area_height, fraction=DEFAULT_FRACTION,
                        min_width=MIN_WIDTH, min_height=MIN_HEIGHT):
    """按屏幕可用区域算初始窗口大小。

    抽成纯函数是为了能单测 —— 离屏环境下拿不到真实屏幕尺寸，
    但这段换算逻辑可以独立验证。
    """
    if area_width <= 0 or area_height <= 0:
        return min_width, min_height
    try:
        fraction = float(fraction)
    except (TypeError, ValueError):
        fraction = DEFAULT_FRACTION
    fraction = min(1.0, max(0.1, fraction))
    width = int(area_width * fraction)
    height = int(area_height * fraction)
    width = max(min_width, min(width, area_width))
    height = max(min_height, min(height, area_height))
    return width, height


def pane_widths(window_width):
    """按窗口宽度分配三栏宽度。

    窗口够宽时，保证中间放得下 3 列卡片（3×232=696）；
    窗口窄时从两侧匀回来，**优先保中间**（卡片区才是主体）。
    返回值之和始终不超过窗口宽度。

    两侧面板按比例给，并且**小窗时更小**：
    这样 1280 宽的窗口模式下详情面板只占 200 出头，
    而不是把中间挤掉一大块。
    """
    width = max(MIN_WIDTH, int(window_width or MIN_WIDTH))
    usable = width - 30                       # 扣掉分隔条
    left = max(210, min(268, int(usable * 0.20)))
    right = max(200, min(330, int(usable * 0.175)))
    center = usable - left - right
    if center < 706:                          # 中间放不下 3 列（3×232+边距），从两边匀
        deficit = 706 - center
        take_left = min(deficit // 2, left - 180)
        take_right = min(deficit - take_left, right - 200)
        left -= max(0, take_left)
        right -= max(0, take_right)
        center = usable - left - right
    return left, center, right

CURSORS = {
    "left": Qt.SizeHorCursor,
    "right": Qt.SizeHorCursor,
    "top": Qt.SizeVerCursor,
    "bottom": Qt.SizeVerCursor,
    "topleft": Qt.SizeFDiagCursor,
    "bottomright": Qt.SizeFDiagCursor,
    "topright": Qt.SizeBDiagCursor,
    "bottomleft": Qt.SizeBDiagCursor,
}


def edge_zone(x, y, width, height, margin=EDGE):
    """判断一个点落在窗口的哪个缩放热区里。

    抽成纯函数是为了能单测 —— 窗口拖拽行为在离屏环境里没法真验，
    但这套几何判断可以。
    """
    if width <= 0 or height <= 0 or margin <= 0:
        return None
    if x < 0 or y < 0 or x >= width or y >= height:
        return None
    left = x <= margin
    right = x >= width - margin - 1
    top = y <= margin
    bottom = y >= height - margin - 1
    if top and left:
        return "topleft"
    if top and right:
        return "topright"
    if bottom and left:
        return "bottomleft"
    if bottom and right:
        return "bottomright"
    if left:
        return "left"
    if right:
        return "right"
    if top:
        return "top"
    if bottom:
        return "bottom"
    return None


class FramelessWindow(QMainWindow):
    def __init__(self, parent=None, frameless=True):
        super(FramelessWindow, self).__init__(parent)
        self._frameless = bool(frameless)
        self._custom_maximized = False
        self._normal_geometry = None
        self._resize_zone = None
        self._resize_origin = None       # (globalPos, geometry)
        self._drag_offset = None

        if self._frameless:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setMouseTracking(True)
            self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)

    # ------------------------------------------------------------ 状态

    def is_frameless(self):
        return self._frameless

    def custom_maximized(self):
        return self._custom_maximized

    def set_frameless(self, frameless):
        """切换无边框/原生标题栏（需要重新 show 才生效）。"""
        self._frameless = bool(frameless)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint
                            if self._frameless else Qt.Window)
        if not self._frameless:
            self._custom_maximized = False
        self._sync_window_corners()

    # ------------------------------------------------------------ 圆角

    def _sync_window_corners(self):
        """把「现在该不该圆角」同步给 Windows。

        窗口标志一变（`setWindowFlags`）Windows 会重建底层窗口，
        DWM 的那些属性也就跟着丢了，所以每次切换状态都要重新说一遍。
        """
        preference = corner_preference(self._frameless, self._custom_maximized)
        try:
            hwnd = int(self.winId())
        except Exception:                              # 窗口还没建出来
            return False
        return apply_corner_preference(hwnd, preference)

    def showEvent(self, event):
        super(FramelessWindow, self).showEvent(event)
        self._sync_window_corners()

    # ------------------------------------------------------------ 最大化

    def toggle_maximize(self):
        if not self._frameless:
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()
            return self.isMaximized()
        if self._custom_maximized:
            self.restore_geometry()
        else:
            self.maximize_custom()
        return self._custom_maximized

    def maximize_custom(self):
        if self._custom_maximized:
            return
        self._normal_geometry = self.geometry()
        screen = None
        try:
            screen = QApplication.screenAt(self.frameGeometry().center())
        except Exception:                              # 老版本没有 screenAt
            screen = None
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        else:
            self.showMaximized()
        self._custom_maximized = True
        self._sync_window_corners()                    # 铺满屏幕了就切回方角

    def restore_geometry(self):
        if not self._custom_maximized:
            return
        self._custom_maximized = False
        if self._normal_geometry is not None:
            self.setGeometry(self._normal_geometry)
        self._sync_window_corners()

    # ------------------------------------------------------------ 拖动

    def begin_drag(self, global_pos):
        """从标签条空白处开始拖窗口。"""
        if not self._frameless:
            return
        self._drag_offset = QPoint(global_pos) - self.frameGeometry().topLeft()
        if self._custom_maximized:
            # 最大化状态下拖动：先还原，并把窗口摆到光标下方，保持手感连续
            ratio = 0.5
            if self.width():
                ratio = min(0.9, max(0.1,
                                    (QPoint(global_pos).x() - self.x()) / float(self.width())))
            self.restore_geometry()
            new_x = int(QPoint(global_pos).x() - self.width() * ratio)
            self.move(new_x, max(0, QPoint(global_pos).y() - 18))
            self._drag_offset = QPoint(global_pos) - self.frameGeometry().topLeft()

    def continue_drag(self, global_pos):
        if self._drag_offset is None:
            return
        self.move(QPoint(global_pos) - self._drag_offset)

    def end_drag(self):
        self._drag_offset = None

    def is_dragging(self):
        return self._drag_offset is not None

    # ------------------------------------------------------------ 缩放

    def _zone_at(self, pos):
        if not self._frameless or self._custom_maximized:
            return None
        return edge_zone(pos.x(), pos.y(), self.width(), self.height(), EDGE)

    def mouseMoveEvent(self, event):
        if self._frameless:
            if self._resize_zone and self._resize_origin:
                self._apply_resize(event.globalPos())
                return
            zone = self._zone_at(event.pos())
            if zone:
                self.setCursor(CURSORS.get(zone, Qt.ArrowCursor))
            else:
                self.unsetCursor()
        super(FramelessWindow, self).mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._frameless and event.button() == Qt.LeftButton:
            zone = self._zone_at(event.pos())
            if zone:
                self._resize_zone = zone
                self._resize_origin = (QPoint(event.globalPos()), QRect(self.geometry()))
                return
        super(FramelessWindow, self).mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resize_zone:
            self._resize_zone = None
            self._resize_origin = None
            self.unsetCursor()
        super(FramelessWindow, self).mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if not self._resize_zone:
            self.unsetCursor()
        super(FramelessWindow, self).leaveEvent(event)

    def _apply_resize(self, global_pos):
        origin_pos, origin_geo = self._resize_origin
        delta = QPoint(global_pos) - origin_pos
        geo = QRect(origin_geo)
        zone = self._resize_zone

        if "left" in zone:
            geo.setLeft(min(origin_geo.left() + delta.x(),
                            origin_geo.right() - MIN_WIDTH + 1))
        if "right" in zone:
            geo.setRight(max(origin_geo.right() + delta.x(),
                             origin_geo.left() + MIN_WIDTH - 1))
        if "top" in zone:
            geo.setTop(min(origin_geo.top() + delta.y(),
                           origin_geo.bottom() - MIN_HEIGHT + 1))
        if "bottom" in zone:
            geo.setBottom(max(origin_geo.bottom() + delta.y(),
                              origin_geo.top() + MIN_HEIGHT - 1))
        self.setGeometry(geo)
