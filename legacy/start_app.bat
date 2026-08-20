@echo off
title Launching BSDC Ingest Engine...
echo ==================================================
echo  Starting FastAPI Engine ^& Opening n8n Workflow...
echo ==================================================

cd /d "%~dp0"

:: Active venv
call venv\Scripts\activate

:: Ensure dependencies and Playwright browser are installed
pip install -r requirements.txt
playwright install chromium

:: Run FastAPI in background
start /B python main.py

timeout /t 3 /nobreak >nul
start http://localhost:5678

echo.
echo Engine is running smoothly in background!