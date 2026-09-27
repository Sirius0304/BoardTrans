@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 瞬译 - 自检工具

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" checkup.py
) else (
    python checkup.py
)
