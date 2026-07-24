@echo off
echo Cerrando servidor anterior si existe...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 >nul

echo Abriendo navegador...
start "" "http://127.0.0.1:8000/v2"
timeout /t 2 >nul

echo Levantando servidor...
cd /d "%~dp0"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
