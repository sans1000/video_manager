# -*- coding: utf-8 -*-
"""离屏渲染界面预览图（开发用，不影响正式功能）。

用途：在没有人盯着屏幕的环境里，也能把真实界面画出来存成 PNG，
方便检查布局、中文显示、列的宽度之类的问题。

用法：
    py tools\render_preview.py var\report\preview.png
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyQt5.QtCore import QTimer                       # noqa: E402
from PyQt5.QtWidgets import QApplication              # noqa: E402

from gui.engine import Engine                         # noqa: E402
from gui.main_window import MainWindow                # noqa: E402
from gui.session import Session                       # noqa: E402
from gui.style import STYLESHEET                      # noqa: E402


def render(out_path, wait_ms=6000, select_row=0, width=1420, height=880,
           open_tag=None, root=None, db_path=None, frameless=True, columns=None,
           collapse_detail=False):
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(STYLESHEET)
    engine = Engine(db_path=db_path or "", root=root)
    session = Session(engine)
    window = MainWindow(session, frameless=frameless)
    window.resize(width, height)
    if columns:
        window.set_preferred_columns(columns)
    window.show()
    if collapse_detail:
        window.detail.set_collapsed(True)
    if open_tag:
        window.tabs.open_tag(open_tag)
    page = window.tabs.current_page()
    if page is not None:
        page.apply_metrics()
        if page.model.rowCount() > select_row:
            page.view.setCurrentIndex(page.model.index(select_row, 0))

    def grab():
        window.grab().save(out_path)
        print("已保存预览图：%s" % out_path)
        app.quit()

    QTimer.singleShot(wait_ms, grab)
    code = app.exec_()
    session.shutdown()
    return code


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="离屏渲染界面预览图")
    parser.add_argument("out", nargs="?", default=os.path.join(
        ROOT, "var", "report", "preview.png"))
    parser.add_argument("--tag", default=None, help="额外打开某个标签页")
    parser.add_argument("--root", default=None, help="覆盖根目录")
    parser.add_argument("--db", default=None, help="覆盖索引库")
    parser.add_argument("--wait", type=int, default=6000, help="等待毫秒数")
    parser.add_argument("--width", type=int, default=None, help="窗口宽度")
    parser.add_argument("--height", type=int, default=None, help="窗口高度")
    parser.add_argument("--columns", type=int, default=None, help="每行显示几个 (2/3/4)")
    parser.add_argument("--collapse-detail", dest="collapse_detail", action="store_true",
                        help="渲染成详情面板已收起的样子")
    parser.add_argument("--native-frame", dest="native_frame", action="store_true",
                        help="渲染成原生标题栏模式（客户区内容一样，只是窗口标志不同）")
    args = parser.parse_args()
    kwargs = {}
    if args.width:
        kwargs["width"] = args.width
    if args.height:
        kwargs["height"] = args.height
    if args.columns:
        kwargs["columns"] = args.columns
    if args.collapse_detail:
        kwargs["collapse_detail"] = True
    sys.exit(render(args.out, wait_ms=args.wait, open_tag=args.tag,
                    root=args.root, db_path=args.db,
                    frameless=not args.native_frame, **kwargs))
