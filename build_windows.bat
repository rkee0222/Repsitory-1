@echo off
REM ===================================================================
REM  Build VoicePitch.exe on Windows.
REM  Run this from the project root in a Command Prompt.
REM ===================================================================
setlocal

echo [1/4] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 goto :error

echo [2/4] Activating and installing dependencies...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/4] Running pitch-accuracy tests...
python tests\run_core_tests.py
pytest -q
if errorlevel 1 echo (pytest reported issues - review above)

echo [4/4] Building standalone executable with PyInstaller...
pyinstaller --noconfirm VoicePitch.spec
if errorlevel 1 goto :error

echo.
echo ======================================================
echo  Build complete:  dist\VoicePitch.exe
echo  (For MP3/M4A: place ffmpeg.exe next to VoicePitch.exe
echo   or on the system PATH. See README.)
echo ======================================================
goto :eof

:error
echo.
echo BUILD FAILED. See the messages above.
exit /b 1
