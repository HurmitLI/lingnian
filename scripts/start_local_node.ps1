param([string]$Repository)
$comfy = 'E:\LingNianAI\ComfyUI'
try { $null = Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 2 } catch {
    Start-Process -FilePath "$comfy\.venv\Scripts\python.exe" -ArgumentList @('main.py','--listen','127.0.0.1','--port','8188','--lowvram','--preview-method','none','--disable-auto-launch') -WorkingDirectory $comfy -WindowStyle Hidden
}
$nodeRoot = Join-Path $Repository 'local_node\runtime'
$configPath = Join-Path $Repository 'local_node\config.json'
if (-not (Test-Path $configPath)) {
    Write-Host 'Cloud API URL is not configured. Run the secure connection setup first.'
    exit 2
}
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
if (-not $config.api_base.StartsWith('https://')) {
    Write-Host 'Cloud API URL must use HTTPS.'
    exit 2
}
Start-Process -FilePath 'E:\LingNianAI\node-venv\Scripts\pythonw.exe' -ArgumentList @('-m','lingnian_node','--production','--api-base',$config.api_base,'--root',$nodeRoot) -WorkingDirectory $Repository -WindowStyle Hidden
Start-Process 'http://127.0.0.1:8188'
Write-Host 'LingNian worker started. ComfyUI is bound to localhost only.'
