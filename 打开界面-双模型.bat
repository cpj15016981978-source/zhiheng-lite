@echo off
cd /d "%~dp0"
set "PY=python"
if not exist "%PY%" if exist "D:\ZhihengInstall\python\python.exe" set "PY=D:\ZhihengInstall\python\python.exe"
if not exist "%PY%" where python >nul 2>nul || (echo 找不到 python，请把 Python 加进 PATH。 & pause & exit /b 1)
set PAI_MODEL_PROVIDER=openai
set PAI_MODEL_NAME=MiniMax-M3.1-Flash
set PAI_OPENAI_BASE_URL=https://api.minimaxi.com/v1
set PAI_COUNCIL_MODELS=openai:MiniMax-M3.1-Flash,openai:MiniMax-M3
"%PY%" tools\preflight.py --startup
if errorlevel 1 ( pause & exit /b 1 )
"%PY%" webapp.py --live
if errorlevel 1 pause
