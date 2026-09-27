@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 瞬译 - 打包成 exe

echo ============================================================
echo   正在打包瞬译 - 精简配置
echo   已排除 numpy, Pillow 图片解码器等等运行时用不到的依赖
echo   产物约 34 MB, 未精简时是 50 MB
echo   大约需要 1 到 2 分钟...
echo ============================================================
echo.

REM 如果 dist 里已有 exe, 先改名, 否则 PyInstaller 覆盖时会失败
if exist "dist\瞬译.exe" (
    echo   检测到已存在的 dist\瞬译.exe, 先改名为 瞬译_上一版.exe
    if exist "dist\瞬译_上一版.exe" del "dist\瞬译_上一版.exe"
    ren "dist\瞬译.exe" "瞬译_上一版.exe"
)

if exist ".venv\Scripts\pyinstaller.exe" (
    ".venv\Scripts\pyinstaller.exe" --noconfirm --distpath "dist" --workpath "build" "瞬译_精简.spec"
) else (
    pyinstaller --noconfirm --distpath "dist" --workpath "build" "瞬译_精简.spec"
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
echo   产物: dist\瞬译.exe  大小约 34 MB
echo   双击即可运行, 不需要安装 Python
echo ============================================================
pause
