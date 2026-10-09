@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 键译 - 打包成 exe

echo ============================================================
echo   正在打包键译 - 精简配置
echo   已排除 numpy, Pillow 图片解码器等等运行时用不到的依赖
echo   产物约 34 MB, 未精简时是 50 MB
echo   产物直接生成在本目录: 键译.exe
echo   大约需要 1 到 2 分钟...
echo ============================================================
echo.

REM 根目录若已有上一版成品, 先归档到 dist: 既不会挡 PyInstaller 覆盖,
REM 也不会让根目录堆出多个 exe (双击时容易选错旧版)
if exist "键译.exe" (
    echo   检测到已存在的 键译.exe, 先归档为 dist\键译_上一版.exe
    if not exist "dist" mkdir "dist"
    if exist "dist\键译_上一版.exe" del "dist\键译_上一版.exe"
    move /y "键译.exe" "dist\键译_上一版.exe" >nul
)

if exist ".venv\Scripts\pyinstaller.exe" (
    ".venv\Scripts\pyinstaller.exe" --noconfirm --distpath "." --workpath "build" "键译_精简.spec"
) else (
    pyinstaller --noconfirm --distpath "." --workpath "build" "键译_精简.spec"
)

if errorlevel 1 (
    echo.
    echo   [失败] 打包出错, 请把上面的报错内容截图保存.
    echo   常见原因: 依赖没装全 - 先运行 0-准备环境.bat
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   打包结束.
echo   产物: 键译.exe  (就在本目录下)  大小约 34 MB
echo   上一版已归档到: dist\键译_上一版.exe
echo   双击即可运行, 不需要安装 Python
echo ============================================================
pause
