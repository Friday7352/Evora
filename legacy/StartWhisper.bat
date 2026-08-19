@echo off
REM === Start Whisper.bat ===
REM 2070 BOX  ->  Whisper\Start Whisper.bat  (or a desktop shortcut to it)
REM ==========================================================================
REM  Double-click to start the transcription service, instead of opening
REM  PowerShell and typing the command each time.
REM
REM  The window stays open and shows the live log - transcription timings,
REM  detected languages, speaker matches. Closing it stops the service.
REM ==========================================================================

setlocal

REM This file works both from a git checkout and after the one-click
REM installer.  Keeping all paths relative to this file avoids the former
REM hard-coded Administrator/Python 3.14 paths.
set "WHISPER_DIR=%~dp0"
set "PYTHON_EXE=%WHISPER_DIR%.venv\Scripts\python.exe"

title Whisper transcription service

cd /d "%WHISPER_DIR%"
if errorlevel 1 (
    echo   Could not find the Whisper folder:
    echo     %WHISPER_DIR%
    echo   Edit WHISPER_DIR at the top of this file.
    echo.
    pause
    exit /b 1
)

REM Refuse to start a second copy. Two instances fight over port 9000 and the
REM second one dies with an unhelpful error.
curl -s -m 2 http://localhost:9000/health >nul 2>&1
if %errorlevel%==0 (
    echo.
    echo   Whisper is already running on this machine.
    echo   Open http://localhost:9000 to see its status.
    echo.
    pause
    exit /b 0
)

echo.
echo   Starting Whisper...
echo   Leave this window open. Closing it stops the service.
echo.

if not exist "%PYTHON_EXE%" (
    echo.
    echo   Whisper has not been installed yet.
    echo   Run Install-Whisper.ps1 first, then try again.
    echo.
    pause
    exit /b 1
)

set "HF_HOME=%WHISPER_DIR%model_cache"
set "HUGGINGFACE_HUB_CACHE=%WHISPER_DIR%model_cache"
set "TORCH_HOME=%WHISPER_DIR%model_cache"
set "SPEECHBRAIN_CACHE=%WHISPER_DIR%ecapa_model"
"%PYTHON_EXE%" -u whisper_server.py

echo.
echo   Whisper stopped.
pause
