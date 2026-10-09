@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 键译 - 一键准备运行环境

echo ============================================================
echo   键译 · 一键准备运行环境
echo   这一步会做两件事: 创建独立的 Python 环境 + 安装依赖库
echo   需要联网, 下载约 60 MB, 大概 1~3 分钟, 请耐心等待
echo ============================================================
echo.

echo [1/3] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [失败] 没有找到 Python.
    echo   请先安装 Python 3.10 或更高版本(勾选 Add python.exe to PATH): 
    echo   https://www.python.org/downloads/windows/
    echo.
    pause
    exit /b 1
)
python --version

echo.
echo       检查 tkinter 图形库(设置界面需要)...
python -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [注意] 当前 Python 没有自带 tkinter 图形库.
    echo   影响: 托盘的「设置」菜单会弹提示, 无法打开图形设置界面(翻译功能不受影响).
    echo   建议: 到 python.org 下载官方完整版安装包(安装时保持默认勾选 tcl/tk and IDLE), 
    echo         然后重新运行本脚本.
    echo.
    echo   是否继续安装(只用翻译, 不要图形设置界面)?
    pause
) else (
    echo       tkinter 正常
)

echo.
echo [2/3] 创建虚拟环境 .venv ...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
) else (
    echo   已存在, 跳过
)

echo.
echo [3/3] 安装依赖库(使用阿里云镜像加速)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

if errorlevel 1 (
    echo.
    echo   [失败] 依赖安装出错, 请把上面的报错内容截图保存.
    echo   常见原因: 网络不通, 或用了不含 ctranslate2 的镜像源.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   环境准备完成!
echo   接下来可以双击  1-控制台版.bat  开始使用.
echo ============================================================
pause
