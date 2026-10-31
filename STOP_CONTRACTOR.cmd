@echo off
cd /d "%~dp0"
backend\.venv\Scripts\python.exe ops\contractor_local.py stop
if errorlevel 1 pause
