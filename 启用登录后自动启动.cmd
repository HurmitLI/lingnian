@echo off
chcp 65001 >nul
schtasks /Create /TN "聆年本地影像节点" /SC ONLOGON /TR "\"%~dp0启动聆年影像节点.cmd\"" /F
echo 已创建可撤销的登录后自动启动任务。
pause
