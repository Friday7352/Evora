@echo off
rem ------------------------------------------------------------------
rem  Evora — one-click installer build
rem ------------------------------------------------------------------
rem  Double-click this file from the project folder to create a fresh
rem  dist\EvoraSetup.exe. The PowerShell build script compiles
rem  EvoraHost.exe and EvoraSetupHost.exe — which is what picks up a
rem  changed EvoraIcon.ico, since the icon is compiled into the exe —
rem  and then runs Inno Setup. Inno Setup is installed for you if it
rem  is not already on this machine.
rem ------------------------------------------------------------------

setlocal
cd /d "%~dp0"

where powershell >nul 2>&1
if errorlevel 1 (
  echo Windows PowerShell was not found on this system.
  echo EvoraSetup.exe could not be built.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build\Build-EvoraInstaller.ps1"
set "BUILD_EXIT=%ERRORLEVEL%"

echo.
if not "%BUILD_EXIT%"=="0" (
  echo The installer build did not complete. Review the message above and try again.
) else (
  echo Done. Your new installer is in the dist folder as EvoraSetup.exe.
)
echo.
pause
exit /b %BUILD_EXIT%
