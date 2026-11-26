@echo off
cd /d "%~dp0"
set "URL=http://127.0.0.1:8753/chat"
set "PY=python"
if not exist "%PY%" if exist "D:\ZhihengInstall\python\python.exe" set "PY=D:\ZhihengInstall\python\python.exe"
if not exist "%PY%" where python >nul 2>nul || (echo Python not found. Add it to PATH. & pause & exit /b 1)
rem If server is up, only open the app window; otherwise start it.
powershell -NoProfile -Command "try{$r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8753/api/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0}}catch{}; exit 1"
if not errorlevel 1 goto :open
start "" /min "%PY%" webapp.py --no-browser
timeout /t 6 /nobreak >nul
:open
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%EDGE%" (
  start "" "%EDGE%" --app=%URL%
) else (
  start "" %URL%
)
