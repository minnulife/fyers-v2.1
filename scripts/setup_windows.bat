@echo off
cd /d %~dp0\..
py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.example .env
if not exist data mkdir data
if not exist logs mkdir logs
echo.
echo Setup complete. Edit .env, then run scripts\start_windows.bat
pause
