@echo off
echo ============================================================
echo        Creating PUBLIC Link for Wall Clock
echo ============================================================
echo.
echo This will create a public URL accessible from anywhere!
echo.
echo Make sure the server is running first (start-clock.bat)
echo.

REM Refresh PATH to include ngrok
set PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links

REM Check if ngrok is configured
ngrok config check >nul 2>&1
if errorlevel 1 (
    echo.
    echo ============================================================
    echo FIRST TIME SETUP REQUIRED:
    echo ============================================================
    echo 1. Go to: https://dashboard.ngrok.com/signup
    echo 2. Sign up for FREE account
    echo 3. Copy your authtoken from the dashboard
    echo 4. Run: ngrok config add-authtoken YOUR_TOKEN
    echo ============================================================
    echo.
    pause
    exit /b
)

echo Starting ngrok tunnel to port 8080...
echo.
echo Your PUBLIC URL will appear below:
echo (Share this URL with anyone to access your wall clock)
echo.
ngrok http 8080
