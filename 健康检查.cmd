@echo off
chcp 65001 >nul
E:\LingNianAI\node-venv\Scripts\python.exe -m lingnian_node --root "%~dp0local_node\runtime" --health
pause
