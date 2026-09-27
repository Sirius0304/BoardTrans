@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 瞬译 - 打包成 exe

echo ============================================================
echo   正在把瞬译打包成单文件 exe，大约需要 1~3 分钟...
echo ============================================================
echo.

if exist ".venv\Scripts\pyinstaller.exe" (
    ".venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
        --name "瞬译" ^
        --icon "app_icon.ico" ^
        --hidden-import pystray._win32 ^
        --distpath "dist" ^
        --workpath "build" ^
        --specpath "build" ^
        run_stage2.py
) else (
    pyinstaller --noconfirm --clean --onefile --windowed ^
        --name "瞬译" ^
        --icon "app_icon.ico" ^
        --hidden-import pystray._win32 ^
        --distpath "dist" ^
        --workpath "build" ^
        --specpath "build" ^
        run_stage2.py
)

echo.
echo ============================================================
echo   打包结束。生成的 exe 在 dist 文件夹里：dist\瞬译.exe
echo   双击即可运行，不需要安装 Python。
echo ============================================================
pause
