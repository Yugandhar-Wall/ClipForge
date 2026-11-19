@echo off
REM One-time install for motion graphics (HyperFrames + GSAP). Everything goes inside this folder.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "motion\setup_motion.ps1"
if errorlevel 1 goto failed
echo.
echo Testing one animation...
".venv\Scripts\python.exe" -m cf.motion
pause
exit /b 0
:failed
echo.
echo Setup FAILED - copy the messages above and send them over.
pause
exit /b 1
