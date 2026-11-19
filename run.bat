@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause & exit /b 1
)
echo Starting ClipForge - your browser will open at http://127.0.0.1:7860
echo Keep this window open while you use it. Close it to stop.
".venv\Scripts\python.exe" app.py
pause
