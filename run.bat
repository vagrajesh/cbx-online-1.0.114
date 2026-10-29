@echo off
REM ============================================================
REM  CBX-Online - start the server. Leave this window open.
REM  Exit code 3 = a new version was promoted from the Versions
REM  page; the loop restarts the app on the new code.
REM ============================================================
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
title CBX-Online

:loop
if exist run.bat.new (
  echo Updating run.bat to the new version...
  REM Hand over to the new script. No "call": this whole block was parsed before it ran,
  REM so nothing more is read from the file we just replaced. The lines after only run
  REM if the copy failed (file locked) - then keep going on the current run.bat.
  copy /y run.bat.new run.bat >nul && del run.bat.new && run.bat
  echo Could not replace run.bat - continuing with the current one. The new copy is kept as run.bat.failed.
  if exist run.bat.failed del run.bat.failed
  ren run.bat.new run.bat.failed
)
echo [%date% %time%] CBX-Online starting...
python -m cbx.main
if %errorlevel%==3 (
  echo [%date% %time%] Restarting on the new version...
  timeout /t 2 /nobreak >nul
  goto loop
)
echo CBX-Online stopped (exit code %errorlevel%).
pause
