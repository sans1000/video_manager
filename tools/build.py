# -*- coding: utf-8 -*-
"""打成 Windows 程序（文件夹版）。

用法：
    py tools\\build.py                # 跑测试 -> 清理 -> 打包 -> 整理发布目录
    py tools\\build.py --skip-tests   # 跳过测试（赶时间时用）

产物在 `dist\\视频管理器\\`：

    视频库管理.exe    图形界面，双击即用
    vm.exe           命令行（scan / rename / dupes / tag / report）
    vm_config.toml   配置，打包时从项目目录带过来
    var\\             索引库、标签备份、操作日志（打包时带一份）
    使用说明.txt

为什么要带一份 var\\：配置里的路径都是**相对 exe 所在目录**解析的，
带过去之后打包版一打开就是你熟悉的那套索引和标签，而不是空库。
代价是它和源码目录那份从此各自独立 —— 用哪份就固定用哪份（说明里写了）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST_NAME = "视频管理器"
DIST = os.path.join(ROOT, "dist", DIST_NAME)

# 从项目目录带进发布包的运行数据（缩略图缓存不带，它会自己重建）
CARRY_FILES = [
    (os.path.join("var", "video_index.db"), os.path.join("var", "video_index.db")),
    (os.path.join("var", "video_index.db.before-rename"),
     os.path.join("var", "video_index.db.before-rename")),
    (os.path.join("var", "tags_backup.csv"), os.path.join("var", "tags_backup.csv")),
    (os.path.join("var", "tag_journal.csv"), os.path.join("var", "tag_journal.csv")),
    (os.path.join("var", "rename_journal.csv"), os.path.join("var", "rename_journal.csv")),
    ("vm_config.toml", "vm_config.toml"),
]

README_TXT = """\
视频库管理 —— 使用说明
========================================

这个文件夹里有两个程序：

  视频库管理.exe   图形界面，双击就能用
  vm.exe           命令行版本，用法：vm.exe --help

数据放在哪
----------------------------------------
索引库、标签、缩略图缓存都在本文件夹的 var\\ 里。
**配置里的相对路径一律相对于 exe 所在目录**，所以整个文件夹可以
随意搬动（U 盘、别的分区都行），数据跟着走。

注意：别放在需要管理员权限才能写的目录里（比如 C:\\Program Files），
否则索引库和标签写不进去。放桌面、D 盘、U 盘都可以。

ffmpeg
----------------------------------------
没找到 ffmpeg 时，只是**没有缩略图**，其余功能照常。
想有缩略图，把 ffmpeg.exe 和 ffprobe.exe 放到：

    本文件夹\\            （和 exe 并排）
    本文件夹\\bin\\
    本文件夹\\tools\\

或者写进 vm_config.toml 的 [probe] 段，或者装进系统 PATH。

配置文件
----------------------------------------
vm_config.toml 就在本文件夹里，用记事本就能改：

  [library] roots       要管理的文件夹（可以写多个）
  [probe]   ffmpeg      ffmpeg 路径（不填就按上面的顺序自动找）
  [gui]     frameless   无边框窗口；出问题改成 false 就回到系统标题栏
  [gui]     start_maximized  启动是否最大化

改完存盘，重新打开程序即可。

两个版本别混用
----------------------------------------
如果你之前一直用源码目录（D:\\ds_workplace\\video_manager）里的那份，
注意它的 var\\ 和这里的 var\\ 是**两份独立的数据**。
补一次标签、改一次名，只会在你当前用的那一份里生效。
建议以后固定用打包版；想合并的话，用「⋯ 菜单 -> 导出标签备份」，
再到另一份里「从备份导入」。
"""


def run(cmd, **kwargs):
    """跑一条命令，输出直接透传（不抓管道）。"""
    print("+ %s" % " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT, **kwargs)


def find_build_tools():
    """PyInstaller 在哪 —— 返回需要加进 PYTHONPATH 的目录，系统里就有则返回 ""。

    这台机器上 pip 写不了工作区以外的目录（系统 site-packages 和
    %APPDATA%\\Python 都被拒），所以构建工具装在项目旁边的独立目录里，
    靠 PYTHONPATH 指过去：**不动系统 Python，也不污染项目**。
    """
    try:
        import PyInstaller                                    # noqa: F401
        return ""
    except ImportError:
        pass
    for cand in (os.path.join(ROOT, ".build_tools"),
                 os.path.join(os.path.dirname(ROOT), "_build_tools")):
        if os.path.isdir(os.path.join(cand, "PyInstaller")):
            return cand
    return None


def clean():
    for path in (os.path.join(ROOT, "build"), os.path.join(ROOT, "dist")):
        if os.path.isdir(path):
            print("清掉旧的 %s" % path)
            shutil.rmtree(path, ignore_errors=True)


def run_tests():
    print("\n=== 先跑一遍测试 ===")
    code = run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    if code != 0:
        print("\n测试没过，先别打包。")
        return False
    return True


def build(env=None):
    print("\n=== 开始打包 ===")
    code = run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                "vm.spec"], env=env)
    if code != 0:
        print("\n打包失败。")
        return False
    return True


def carry_data():
    """把配置和运行数据带进发布目录。"""
    print("\n=== 整理发布目录 ===")
    var_dir = os.path.join(DIST, "var")
    if not os.path.isdir(var_dir):
        os.makedirs(var_dir)
    carried = []
    for src_rel, dst_rel in CARRY_FILES:
        src = os.path.join(ROOT, src_rel)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(DIST, dst_rel)
        shutil.copy2(src, dst)
        carried.append("%s（%.1f KB）" % (dst_rel, os.path.getsize(dst) / 1024.0))
    # utf-8-sig（带 BOM）：这是给「用记事本打开的人」看的文件，
    # 不带 BOM 的话老工具会按系统编码读，中文全是乱码。
    with open(os.path.join(DIST, "使用说明.txt"), "w",
              encoding="utf-8-sig") as fh:
        fh.write(README_TXT)
    for item in carried:
        print("  带上 %s" % item)
    print("  写上 使用说明.txt")
    return carried


def report():
    exe_gui = os.path.join(DIST, "视频库管理.exe")
    exe_cli = os.path.join(DIST, "vm.exe")
    total = 0
    for base, _dirs, files in os.walk(DIST):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
    print("\n=== 完成 ===")
    print("  发布目录：%s" % DIST)
    print("  图形界面：%s  %s" % (os.path.basename(exe_gui),
                                  "有" if os.path.isfile(exe_gui) else "**没有！**"))
    print("  命令行：  %s  %s" % (os.path.basename(exe_cli),
                                  "有" if os.path.isfile(exe_cli) else "**没有！**"))
    print("  合计 %.1f MB" % (total / 1024.0 / 1024.0))
    print("\n  试一下：  %s" % exe_gui)
    print("            %s --help" % exe_cli)


def main(argv):
    if sys.version_info < (3, 8):
        print("打包要用 Python 3.8 以上（当前 %s）。试试：py tools\\build.py"
              % sys.version.split()[0])
        return 2

    tools = find_build_tools()
    if tools is None:
        print("没装 PyInstaller。装一下：\n"
              "    py -m pip install pyinstaller "
              "-i http://mirrors.aliyun.com/pypi/simple/ "
              "--trusted-host mirrors.aliyun.com")
        return 2
    env = None
    if tools:
        print("用 %s 里的 PyInstaller" % tools)
        env = dict(os.environ)
        env["PYTHONPATH"] = tools + os.pathsep + env.get("PYTHONPATH", "")

    if "--skip-tests" not in argv:
        if not run_tests():
            return 1
    clean()
    if not build(env):
        return 1
    carry_data()
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
