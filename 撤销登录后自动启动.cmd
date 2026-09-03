@echo off
chcp 65001 >nul
schtasks /Delete /TN "聆年本地影像节点" /F
echo 已撤销登录后自动启动任务。
pause
