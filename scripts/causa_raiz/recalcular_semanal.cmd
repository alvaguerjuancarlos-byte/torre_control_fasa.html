@echo off
rem Recalculo semanal de la vista "Causa raiz" (Calidad del Producto).
rem Lo corre la tarea programada de Windows "TorreFASA - Causa raiz" cada lunes a las 05:30.
rem Solo lectura sobre la BD; escribe backend\data\causa_raiz.json (el servidor lo toma sin reiniciar).
rem Log: logs\causa_raiz_semanal.log
setlocal
set "RAIZ=%~dp0..\.."
set "PYTHONIOENCODING=utf-8"
cd /d "%RAIZ%"
if not exist logs mkdir logs
echo ==== %date% %time% inicio >> logs\causa_raiz_semanal.log
"C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe" scripts\causa_raiz\generar.py >> logs\causa_raiz_semanal.log 2>&1
echo ==== %date% %time% fin (codigo %errorlevel%) >> logs\causa_raiz_semanal.log
endlocal
