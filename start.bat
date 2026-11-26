@echo off
chcp 65001 >nul
title LowSpec Muse v2.2
cd /d %~dp0
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python 3.10+ not found. Please install Python and add it to PATH.
  pause
  exit /b 1
)
echo Installing dependencies (ollama, schedule, markitdown, sqlite-vec)...
echo (markitdown / sqlite-vec are optional: if they fail to install, the app still runs;
echo  only docx/xlsx/pdf import and vector-accelerated semantic search will fall back.)
python -m pip install --quiet ollama schedule "markitdown[docx,xlsx,pdf]" sqlite-vec
echo Starting Ollama in background (if not already running)...
start /min cmd /c "ollama serve"
timeout /t 3 /nobreak >nul
echo Opening LowSpec Muse in your browser...
start http://127.0.0.1:8080
python app.py
pause
