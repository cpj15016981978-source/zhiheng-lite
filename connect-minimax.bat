@echo off
cd /d "%~dp0"
python tools\minimax_quickstart.py
if errorlevel 1 pause
