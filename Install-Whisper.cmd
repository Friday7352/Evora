@echo off
REM Double-click entry point for Whisper Setup.  It bypasses a restrictive
REM local execution policy only for this signed-in user's installer process.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Whisper.ps1"
