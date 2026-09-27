@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 瞬译 - 阶段二 托盘版

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_stage2.py
) else (
    python run_stage2.py
)

echo.
echo 程序已退出。
pause
