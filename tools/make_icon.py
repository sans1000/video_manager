# -*- coding: utf-8 -*-
"""生成应用图标：assets/video_manager.ico

图标是**画出来**的，不是外部素材 —— 整个项目没有图片资源，配色也统一在
`gui.style.PALETTE` 里，图标跟着同一套色走，以后改主题一起改。

Windows 的 .ico 就是一个「多尺寸容器」，这里手写这个容器：
每个尺寸各渲染一张 PNG 塞进去（Vista 之后系统认 PNG 压缩的条目），
比装 Pillow 只为存个图标划算。

用法：
    py tools\\make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt5.QtCore import QBuffer, QByteArray, QRectF, Qt          # noqa: E402
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPixmap   # noqa: E402
from PyQt5.QtWidgets import QApplication                          # noqa: E402

from gui.style import PALETTE                                     # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size):
    """一枚图标：粉色圆角方块 + 白色播放三角。

    小尺寸下细节都会糊掉，所以只保留这两个元素；
    三角稍微右偏一点，视觉重心才在正中间。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    radius = size * 0.22
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.fillPath(path, QColor(PALETTE["ACCENT"]))

    width, height = size * 0.34, size * 0.40
    cx, cy = size * 0.54, size * 0.5
    triangle = QPainterPath()
    triangle.moveTo(cx - width / 2, cy - height / 2)
    triangle.lineTo(cx + width / 2, cy)
    triangle.lineTo(cx - width / 2, cy + height / 2)
    triangle.closeSubpath()
    painter.fillPath(triangle, QColor(PALETTE["TEXT_ON_ACCENT"]))
    painter.end()
    return pixmap


def png_bytes(pixmap):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(path, images):
    """把 [(尺寸, PNG 字节)] 写成 .ico。

    结构：6 字节头 + 每张 16 字节目录项 + 依次排列的图片数据。
    目录项里宽高 0 表示 256（一个字节存不下 256）。
    """
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)          # 保留位, 类型=图标, 张数
    offset = 6 + 16 * count
    entries, body = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(blob), offset)
        offset += len(blob)
        body += blob
    with open(path, "wb") as fh:
        fh.write(header + entries + body)


def main():
    app = QApplication(sys.argv[:1])                   # QPixmap 需要它
    out_dir = os.path.join(ROOT, "assets")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    ico_path = os.path.join(out_dir, "video_manager.ico")
    png_path = os.path.join(out_dir, "video_manager.png")

    images = [(size, png_bytes(render(size))) for size in SIZES]
    write_ico(ico_path, images)
    render(256).save(png_path)                         # 顺手留一张大图好看效果
    print("已生成 %s（%d 个尺寸，%.1f KB）" % (ico_path, len(images),
                                              os.path.getsize(ico_path) / 1024.0))
    print("已生成 %s" % png_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
