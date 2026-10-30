@echo off
rem Recalculo de la vista "Causa raiz" (Calidad del Producto), A MANO.
rem Normalmente no hace falta: el servidor lo dispara solo cada lunes 05:30 (backend/routers/causa_raiz.py).
rem Solo lectura sobre la BD; escribe backend\data\causa_raiz.json (el servidor lo toma sin reiniciar).
rem Log: logs\causa_raiz_semanal.log
setlocal
set "RAIZ=%~dp0..\.."
set "PYTHONIOENCODING=utf-8"
cd /d "%RAIZ%"
if not exist logs mkdir logs
echo ==== %date% %time% inicio (a mano) >> logs\causa_raiz_semanal.log
"C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\causa_raiz\generar.py >> logs\causa_raiz_semanal.log 2>&1
echo ==== %date% %time% fin (codigo %errorlevel%) >> logs\causa_raiz_semanal.log
endlocal
