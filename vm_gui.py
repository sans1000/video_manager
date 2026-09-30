# -*- coding: utf-8 -*-
"""视频库管理 —— 图形界面启动入口。

用法：
    py vm_gui.py                       # 用 vm_config.toml 里的根目录
    py vm_gui.py --root F:\myphone     # 临时换根目录
    py vm_gui.py --db var\other.db     # 换一个索引库

也可以直接双击「启动界面.bat」。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

INSTALL_HINT = """\
未安装 PyQt5，图形界面无法启动。

任选一条命令安装（国内建议用清华镜像，直连 pypi.org 通常很慢）：

    py -m pip install PyQt5 -i https://pypi.tuna.tsinghua.edu.cn/simple

安装完成后重新运行本程序即可。
命令行版本（py vm.py ...）不需要 PyQt5，可以照常使用。
"""


def main():
    try:
        import PyQt5  # noqa: F401
    except ImportError:
        sys.stderr.write(INSTALL_HINT)
        try:
            input("\n按回车键退出…")
        except (EOFError, KeyboardInterrupt):
            pass
        return 2

    from gui.app import main as run
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
