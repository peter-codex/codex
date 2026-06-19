@echo off
cd /d "%~dp0"
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING ABH"') do (
  taskkill /PID %%p /F >nul 2>nul
)
start "" cmd /c "timeout /t 2 >nul & start "" http://127.0.0.1:8765/"
"C:\Users\info\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" ".\billbee_export_app.py" --host 127.0.0.1 --port 8765
pause
