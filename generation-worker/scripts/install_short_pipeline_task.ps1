param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string]$TaskName = "Lingnian Short Pipeline"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) {
  throw "Missing worker virtual environment: $Python"
}
$LogDir = Join-Path $ProjectRoot ".worker-data\short-pipeline"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Launcher = Join-Path $LogDir "run-short-pipeline.cmd"
$Log = Join-Path $LogDir "service.log"
@"
@echo off
cd /d "$ProjectRoot"
"$Python" -m lingnian_worker.short_pipeline_service >> "$Log" 2>&1
"@ | Set-Content -Path $Launcher -Encoding Ascii

$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/d /c `"$Launcher`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 7) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
