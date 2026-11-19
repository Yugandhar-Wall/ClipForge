@echo off
REM Drag a FOLDER of videos (or several video files) onto this file to clip them all.
REM Uses the settings you last used in the app (API key, clip count, layout...).
cd /d "%~dp0"
if "%~1"=="" (
    echo Drag a folder of videos onto batch.bat, or run:  batch.bat "D:\path\to\videos"
    pause & exit /b 1
)
".venv\Scripts\python.exe" cli.py %*
pause
