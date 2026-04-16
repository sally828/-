@echo off

set "EXE=%APPDATA%\360se6\Application\360se.exe"

if not exist "%EXE%" (
    echo [ERROR] 360 browser not found at: %EXE%
    pause
    exit /b 1
)

echo Starting 360 browser in debug mode...
start "" "%EXE%" --remote-debugging-port=9222 --user-data-dir="%TEMP%\360cdp" https://ima.qq.com

echo.
echo Browser started. Please:
echo   1. Log in to your Tencent account in the browser
echo   2. Press any key here when done
echo.
pause

cd /d "%~dp0"
python scrape_ima.py
pause
