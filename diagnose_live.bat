@echo off
cd /d "%~dp0"
set /p URL=Paste the live stream link and press Enter: 
echo Testing every way of recording this stream. This takes about 2 minutes...
".venv\Scripts\python.exe" -m cf.live_diag "%URL%" > live_diag.txt 2>&1
type live_diag.txt
echo.
echo Done. The results are saved in live_diag.txt - tell Claude it is ready.
pause
