@echo off
chcp 65001 >nul
echo 令牌将保存到 Windows 凭据管理器，不会写入项目文件。
E:\LingNianAI\node-venv\Scripts\python.exe -m lingnian_node.credentials set-token
pause
