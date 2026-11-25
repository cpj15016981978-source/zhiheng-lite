@echo off
chcp 65001 >nul
title LowSpec Muse v2.1
cd /d %~dp0
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.10+ not found. Please install Python and add it to PATH.
  pause
  exit /b 1
)
echo Installing dependencies (ollama, schedule)...
python -m pip install --quiet ollama schedule
echo Starting Ollama in background (if not already running)...
start /min cmd /c "ollama serve"
timeout /t 3 /nobreak >nul
echo Opening LowSpec Muse in your browser...
start http://127.0.0.1:8080
python app.py
pause
