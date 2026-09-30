@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动视频库管理界面…
py vm_gui.py %*
if errorlevel 1 (
    echo.
    echo 程序异常退出，错误码 %errorlevel%
    pause
)
