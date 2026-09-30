# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：一个文件夹里放两个程序。

    视频库管理.exe   图形界面
    vm.exe          命令行（rename / dupes / tag / report 这些）

为什么两个都打：命令行版不是"附赠品" —— 改名、查重、生成报表这些批量动作
本来就在命令行里更顺手，图形界面里没做。只打界面等于把一半功能删了。

用法：
    py tools\\build.py            # 推荐：会先清目录、跑测试，再调这里
    py -m PyInstaller vm.spec     # 只想重新打包

注意：不把 ffmpeg 塞进来（几十 MB，且它有自己的许可证）。程序会去
exe 同目录、bin\\、tools\\ 和 PATH 里找，找不到只是没有缩略图。
"""

import os

ROOT = os.path.abspath(os.getcwd())
ICON = os.path.join(ROOT, "assets", "video_manager.ico")
APP_NAME = "视频库管理"

# 用不到的重量级依赖，明确排掉，能省不少体积
EXCLUDES = [
    "tkinter", "unittest", "pydoc", "doctest", "pdb",
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtQml", "PyQt5.QtQuick",
    "PyQt5.QtMultimedia", "PyQt5.QtBluetooth", "PyQt5.QtNfc",
    "PyQt5.QtDesigner", "PyQt5.QtHelp", "PyQt5.QtTest",
    "PyQt5.QtSql", "PyQt5.QtXml", "PyQt5.QtSvg", "PyQt5.QtOpenGL",
    "PyQt5.QtNetworkAuth", "PyQt5.QtWebSockets", "PyQt5.QtWebChannel",
    "numpy", "PIL", "matplotlib",
]


def analyse(script):
    return Analysis(
        [script],
        pathex=[ROOT],
        binaries=[],
        datas=[],
        hiddenimports=[],
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=False,
    )


def make_exe(analysis, name, console):
    pyz = PYZ(analysis.pure)
    return EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                 # UPX 压过的 exe 常被杀软误报，不折腾
        console=console,
        disable_windowed_traceback=False,
        icon=ICON if os.path.isfile(ICON) else None,
    )


gui_analysis = analyse(os.path.join(ROOT, "vm_gui.py"))
cli_analysis = analyse(os.path.join(ROOT, "vm.py"))

gui_exe = make_exe(gui_analysis, APP_NAME, console=False)
cli_exe = make_exe(cli_analysis, "vm", console=True)

# 两个程序共用同一个文件夹：Qt 的运行库只放一份
COLLECT(
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    name="视频管理器",
)
