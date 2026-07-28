@echo off
cd /d %~dp0\..
if not exist .venv\Scripts\python.exe (
  echo Virtual environment not found. Run scripts\setup_windows.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe run.py
