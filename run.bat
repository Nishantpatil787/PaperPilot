@echo off
title PaperPilot Document Platform Server
echo ========================================================
echo    Starting PaperPilot - Your Documents, Under Control.
echo ========================================================
echo.

REM Use Anaconda Python (has all required dependencies including PyMuPDF)
set ANACONDA_PYTHON=C:\Users\LENOVO\anaconda3\python.exe

REM Fallback to system python if anaconda not at expected path
if not exist "%ANACONDA_PYTHON%" set ANACONDA_PYTHON=python

cd /d "%~dp0backend"
set PYTHONPATH=.

echo Launching application on http://127.0.0.1:5000 ...
echo Open your browser at: http://127.0.0.1:5000
echo Press Ctrl+C to stop the server.
echo.
"%ANACONDA_PYTHON%" app.py
pause
