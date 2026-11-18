@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LANGLANG_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "LANGLANG_PYTHON=.venv\Scripts\python.exe"
"%LANGLANG_PYTHON%" -m weimi_manager
if errorlevel 1 pause
