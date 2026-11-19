@echo off
REM One-time setup for ClipForge. Creates a private Python environment in .venv
cd /d "%~dp0"

set PY=
for %%v in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%v -c "import sys" >nul 2>nul && set PY=py -%%v
    )
)
if not defined PY (
    python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,13)" >nul 2>nul && set PY=python
)
if not defined PY (
    echo [X] Python 3.10 - 3.13 not found.
    echo     Install Python 3.12 from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
    pause & exit /b 1
)

echo [*] Using %PY%
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || (echo [X] Could not create virtual environment & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
echo [*] Installing packages (about 400 MB, a few minutes)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [X] Install failed - send the error above to Claude & pause & exit /b 1)
echo [*] Generating sound effects...
".venv\Scripts\python.exe" make_sfx.py >nul
".venv\Scripts\python.exe" -c "from cf.tools import ffmpeg_exe; print('[*] ffmpeg:', ffmpeg_exe())" || (pause & exit /b 1)
echo.
echo [OK] Setup complete. Double-click run.bat to start ClipForge.
pause
