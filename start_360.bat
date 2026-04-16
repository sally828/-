@echo off

set "EXE="
for %%P in (
    "%LOCALAPPDATA%\360Chrome\Chrome\Application\360chrome.exe"
    "C:\Program Files (x86)\360\360Chrome\Chrome\Application\360chrome.exe"
    "C:\Program Files\360\360Chrome\Chrome\Application\360chrome.exe"
    "%ProgramFiles%\360\360Chrome\Chrome\Application\360chrome.exe"
) do (
    if exist %%P (
        if "%EXE%"=="" set "EXE=%%P"
    )
)

if "%EXE%"=="" (
    echo [ERROR] 360 Speed Browser not found.
    echo Please make sure 360Chrome is installed.
    pause
    exit /b 1
)

echo Starting 360 browser in debug mode...
start "" %EXE% --remote-debugging-port=9222 --user-data-dir="%TEMP%\360cdp" https://ima.qq.com

echo.
echo Browser started. Please:
echo   1. Log in to your Tencent account in the browser
echo   2. Press any key here when done
echo.
pause

cd /d "%~dp0"
python scrape_ima.py
pause
