param([string]$Repository)
$comfy = 'E:\LingNianAI\ComfyUI'
try { $null = Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 2 } catch {
    Start-Process -FilePath "$comfy\.venv\Scripts\python.exe" -ArgumentList @('main.py','--listen','127.0.0.1','--port','8188','--lowvram','--preview-method','none','--disable-auto-launch') -WorkingDirectory $comfy -WindowStyle Hidden
}
$nodeRoot = Join-Path $Repository 'local_node\runtime'
$configPath = Join-Path $Repository 'local_node\config.json'
if (-not (Test-Path $configPath)) {
    Write-Host '尚未配置云端地址，请先运行“设置云端连接.cmd”。'
    exit 2
}
$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
if (-not $config.api_base.StartsWith('https://')) {
    Write-Host '云端地址必须使用 HTTPS。'
    exit 2
}
Start-Process -FilePath 'E:\LingNianAI\node-venv\Scripts\pythonw.exe' -ArgumentList @('-m','lingnian_node','--production','--api-base',$config.api_base,'--root',$nodeRoot) -WorkingDirectory $Repository -WindowStyle Hidden
Start-Process 'http://127.0.0.1:8188'
Write-Host '聆年本地影像节点已启动。ComfyUI 仅监听本机地址。'
