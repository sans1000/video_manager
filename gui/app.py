# -*- coding: utf-8 -*-
"""QApplication 启动逻辑。"""

from __future__ import annotations

import argparse
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QMessageBox

from .engine import Engine
from .main_window import MainWindow
from .session import Session
from .style import STYLESHEET


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="vm_gui", description="视频库管理（图形界面）")
    parser.add_argument("--config", default="", help="配置文件路径")
    parser.add_argument("--db", default="", help="索引数据库路径")
    parser.add_argument("--root", default=None, help="覆盖根目录")
    parser.add_argument("--native-frame", dest="native_frame", action="store_true",
                        help="用回 Windows 自带的标题栏（无边框模式出问题时的逃生通道）")
    args, _unknown = parser.parse_known_args(argv[1:])
    return args


def pick_font():
    """优先用微软雅黑，中文界面下比默认字体好看得多。"""
    for family in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "SimHei"):
        font = QFont(family, 9)
        if font.exactMatch() or family in QFont().family():
            return font
    return QFont("", 9)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    args = parse_args(argv)

    # 这两个属性必须在 QApplication 创建之前设置
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(argv[:1])
    app.setApplicationName("video_manager")
    app.setApplicationDisplayName("视频库管理")
    app.setStyleSheet(STYLESHEET)
    font = pick_font()
    if font.family():
        app.setFont(font)

    try:
        engine = Engine(config_path=args.config, db_path=args.db, root=args.root)
    except Exception as exc:                            # noqa: BLE001
        QMessageBox.critical(None, "打不开索引库", str(exc))
        return 2

    if not engine.cfg.ffmpeg:
        QMessageBox.warning(None, "找不到 ffmpeg",
                            "没找到 ffmpeg，缩略图功能会不可用。\n"
                            "其余功能不受影响；可在 vm_config.toml 的 [probe] 里指定路径。")

    frameless = not args.native_frame
    if frameless is True and hasattr(engine.cfg, "frameless"):
        frameless = bool(engine.cfg.frameless)

    window = MainWindow(Session(engine), frameless=frameless)
    window.show()
    return app.exec_()
