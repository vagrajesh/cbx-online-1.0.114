@echo off
REM ============================================================
REM  CBX-Online - apply a version zip from the command line.
REM  Normally you use the Versions page in the app instead
REM  (works from anywhere). This is the fallback when you are
REM  at the server:  update.bat cbx-online-1.0.1.zip
REM ============================================================
setlocal
cd /d "%~dp0"
if "%~1"=="" (echo Usage: update.bat path\to\cbx-online-x.y.z.zip & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m cbx.versions.cli stage-and-promote "%~1"
if errorlevel 1 (echo Update failed - the previous version is still live. & pause & exit /b 1)
echo Done. If run.bat is open, close that window and double-click run.bat again so the new code runs.
pause
