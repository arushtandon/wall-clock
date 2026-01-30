@echo off
title Market Wall Clock Server
cd /d "%~dp0"
echo ============================================================
echo                    MARKET WALL CLOCK
echo ============================================================
echo.
echo Starting server...
echo Opening browser in 3 seconds...
echo.
start /min python server.py
timeout /t 3 /nobreak >nul
start http://localhost:8080
echo.
echo Server is running in background.
echo Press any key to stop the server and exit.
pause >nul
taskkill /f /im python.exe >nul 2>&1
