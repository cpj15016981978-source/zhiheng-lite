@echo off
cd /d "%~dp0"
set "PY=pythonw"
if not exist "%PY%" if exist "D:\ZhihengInstall\python\pythonw.exe" set "PY=D:\ZhihengInstall\python\pythonw.exe"
if not exist "%PY%" if exist "D:\ZhihengInstall\python\python.exe" set "PY=D:\ZhihengInstall\python\python.exe"
if not exist "%PY%" where pythonw >nul 2>nul || set "PY=python"
if not exist "%PY%" where python >nul 2>nul || (echo Python not found. Add it to PATH. & pause & exit /b 1)
start "" "%PY%" desktop_shell.py --theme dark
