@echo off
title BaSSDream
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8080 ^| findstr LISTENING') do (
    taskkill /f /pid %%a >nul 2>&1
)
cd /d "E:\BassStation\backend"
start "" "C:\Python314\pythonw.exe" app.py
timeout /t 2 /nobreak >nul
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --app="http://localhost:8080" --disable-http-cache --window-size=1360,820
exit
