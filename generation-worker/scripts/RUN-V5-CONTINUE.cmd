@echo off
setlocal
cd /d "%~dp0"
if not exist "E:\LingnianAI\node-venv\Scripts\python.exe" (
  echo Existing Python not found. Nothing installed.
  pause
  exit /b 1
)
"E:\LingnianAI\node-venv\Scripts\python.exe" -u "%~dp0continue_v5_reviewed.py" > "%~dp0continue.log" 2>&1
set "LINGNIAN_EXIT=%ERRORLEVEL%"
type "%~dp0continue.log"
echo Exit code: %LINGNIAN_EXIT%
pause
exit /b %LINGNIAN_EXIT%
