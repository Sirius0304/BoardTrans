@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 瞬译 - 阶段一 控制台版

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_stage1.py
) else (
    python run_stage1.py
)

echo.
echo 程序已退出.
pause
