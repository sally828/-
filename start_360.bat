@echo off
chcp 65001 >nul

:: 查找 360极速浏览器（Chromium内核）可执行文件
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
    echo [错误] 找不到 360极速浏览器，请确认已安装。
    echo 注意：需要的是"360极速浏览器"，不是"360安全浏览器"。
    pause
    exit /b 1
)

echo 正在以调试模式启动 360极速浏览器...
start "" %EXE% --remote-debugging-port=9222 --user-data-dir="%TEMP%\360cdp" https://ima.qq.com

echo.
echo 浏览器已启动，请：
echo   1. 在浏览器里登录腾讯账号
echo   2. 登录完成后，回到这里按任意键启动脚本
echo.
pause

cd /d "%~dp0"
python scrape_ima.py
pause
