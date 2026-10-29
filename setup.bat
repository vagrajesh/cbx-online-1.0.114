@echo off
REM ============================================================
REM  CBX-Connect - first-time setup (run once, as yourself)
REM  Needs: Python 3.11+ from python.org (tick "Add to PATH")
REM         Ollama from ollama.com (for C-EXA; optional at first)
REM ============================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo Python was not found. Install Python 3.11 or newer from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)

if not exist .venv (
  echo Creating the Python environment...
  python -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing requirements (this takes a few minutes the first time)...
pip install -r requirements.txt || (echo pip install failed & pause & exit /b 1)

if not exist .env (
  copy .env.example .env >nul
  echo Created .env from .env.example - edit it later to change the port, host names or OneDrive.
)
if not exist data\branding mkdir data\branding
echo.
echo Branding: put your logo at data\branding\logo.png and your tab icon at data\branding\favicon.png
echo (white lettering on a transparent PNG looks best). Sample files are in web\branding-samples\.
echo You can also upload them later from Settings ^> Branding once signed in as the owner.

echo.
set /p OWNER_EMAIL=Owner email (your CBRE address):
set /p OWNER_NAME=Owner full name:
python -m cbx.bootstrap --owner "%OWNER_EMAIL%" --name "%OWNER_NAME%"
if errorlevel 1 (echo Setup failed. & pause & exit /b 1)

echo.
echo Next: double-click run.bat, then open http://localhost:8080 in your browser.
echo For the AI (C-EXA) to think, install Ollama and run:  ollama pull qwen3.8:27b   ollama pull qwen3.5:9b   ollama pull glm-ocr
echo (Without Ollama everything still works; the AI answers in a limited "rules only" mode.)
pause
