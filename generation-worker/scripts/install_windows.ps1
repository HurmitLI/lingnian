param([string]$BackendUrl, [string]$WorkflowPath)
$ErrorActionPreference = "Stop"

$WorkerRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $WorkerRoot ".venv\Scripts\python.exe"
$EnvPath = Join-Path $WorkerRoot ".env"
$ExistingTask = Get-ScheduledTask -TaskName "LingnianGenerationWorker" -ErrorAction SilentlyContinue

if ($ExistingTask) {
    Stop-ScheduledTask -TaskName "LingnianGenerationWorker" -ErrorAction SilentlyContinue
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python launcher not found. Install Python 3.11 or 3.12."
}

if (-not (Test-Path $Python)) {
    $AvailablePython = (& py -0p 2>$null) -join "`n"
    if ($AvailablePython -match "3\.11") {
        & py -3.11 -m venv (Join-Path $WorkerRoot ".venv")
    } elseif ($AvailablePython -match "3\.12") {
        & py -3.12 -m venv (Join-Path $WorkerRoot ".venv")
    } else {
        throw "Python 3.11 or 3.12 is required."
    }
}

& $Python -m pip install --upgrade pip
& $Python -m pip install --upgrade --force-reinstall $WorkerRoot

if (-not (Test-Path $EnvPath)) {
    if (-not $BackendUrl) { $BackendUrl = Read-Host "Formal backend HTTPS URL" }
    if (-not $WorkflowPath) { $WorkflowPath = Read-Host "Full path to the ComfyUI documentary API workflow JSON" }
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
    [System.IO.File]::WriteAllLines($EnvPath, $EnvLines, $Utf8WithoutBom)
} else {
    Write-Host "检测到现有 .env，原位升级将保留后端地址、工作流路径和其他本机配置。"
}

& $Python -m lingnian_worker credential-status
if ($LASTEXITCODE -ne 0) {
    & $Python -m lingnian_worker credential-set
}
& $Python -m lingnian_worker doctor

$Action = New-ScheduledTaskAction -Execute $Python -Argument "-m lingnian_worker run" -WorkingDirectory $WorkerRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 3650) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "LingnianGenerationWorker" -Action $Action -Trigger $Trigger -Settings $Settings -Description "LingNian home documentary generation worker" -Force | Out-Null
Start-ScheduledTask -TaskName "LingnianGenerationWorker"

Write-Host "聆年家用节点 v2.0.1 已安装并启动。以后登录 Windows 会自动运行。"
