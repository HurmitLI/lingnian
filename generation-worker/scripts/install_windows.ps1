$ErrorActionPreference = "Stop"

$WorkerRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $WorkerRoot ".venv\Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "没有找到 Python 启动器，请先安装 Python 3.11 或 3.12。"
}

if (-not (Test-Path $Python)) {
    & py -3.11 -c "import sys; print(sys.version)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        & py -3.11 -m venv (Join-Path $WorkerRoot ".venv")
    } else {
        & py -3.12 -m venv (Join-Path $WorkerRoot ".venv")
    }
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -e $WorkerRoot

$BackendUrl = Read-Host "正式后端 HTTPS 地址"
$WorkflowPath = Read-Host "ComfyUI 纪实空镜 API 工作流 JSON 的完整路径"
$EnvLines = @(
    "LINGNIAN_BACKEND_URL=$BackendUrl",
    "LINGNIAN_COMFYUI_URL=http://127.0.0.1:8188",
    "LINGNIAN_WORK_DIR=$($WorkerRoot.Replace('\', '/'))/.worker-data",
    "LINGNIAN_SCENE_WORKFLOW=$($WorkflowPath.Replace('\', '/'))",
    "LINGNIAN_POLL_SECONDS=8",
    "LINGNIAN_COMFY_TIMEOUT_SECONDS=1800",
    "LINGNIAN_MAX_GENERATION_SECONDS=5",
    "LINGNIAN_CAPABILITIES=scene_video"
)
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines((Join-Path $WorkerRoot ".env"), $EnvLines, $Utf8WithoutBom)

& $Python -m lingnian_worker credential-set
& $Python -m lingnian_worker doctor

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m lingnian_worker run" -WorkingDirectory $WorkerRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "LingnianGenerationWorker" -Action $Action -Trigger $Trigger -Settings $Settings -Description "聆年家用纪实影片生成节点" -Force | Out-Null
Start-ScheduledTask -TaskName "LingnianGenerationWorker"

Write-Host "聆年家用节点 v2 已安装并启动。以后登录 Windows 会自动运行。"
