param([string]$Repository)
$comfy = 'E:\LingNianAI\ComfyUI'
try { $null = Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 2 } catch {
    Start-Process -FilePath "$comfy\.venv\Scripts\python.exe" -ArgumentList @('main.py','--listen','127.0.0.1','--port','8188','--lowvram','--preview-method','none','--disable-auto-launch') -WorkingDirectory $comfy -WindowStyle Hidden
}
$nodeRoot = Join-Path $Repository 'local_node\runtime'
Start-Process -FilePath 'E:\LingNianAI\node-venv\Scripts\pythonw.exe' -ArgumentList @('-m','lingnian_node','--root',$nodeRoot) -WorkingDirectory $Repository -WindowStyle Hidden
Start-Process 'http://127.0.0.1:8188'
Write-Host '聆年本地影像节点已启动。ComfyUI 仅监听本机地址。'
