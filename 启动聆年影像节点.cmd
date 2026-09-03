@echo off
chcp 65001 >nul
if not exist "E:\LingNianAI\node-venv\Scripts\pythonw.exe" (
  echo 本地节点尚未安装完整，请查看部署记录。
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local_node.ps1" -Repository "%~dp0"
