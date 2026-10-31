@echo off
cd /d "%~dp0"
backend\.venv\Scripts\python.exe ops\contractor_local.py start --open-browser
if errorlevel 1 pause
