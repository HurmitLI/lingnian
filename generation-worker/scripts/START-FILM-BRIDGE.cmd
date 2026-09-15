@echo off
setlocal
cd /d "%~dp0"
if not exist "E:\LingnianAI\node-venv\Scripts\python.exe" (
  echo Existing Python unavailable. Nothing installed.
  pause
  exit /b 1
)
"E:\LingnianAI\node-venv\Scripts\python.exe" -u -m lingnian_worker.film_bridge --root "%~dp0." --seconds 14400
echo Bridge has exited. Review bridge-status.json before restarting.
pause
