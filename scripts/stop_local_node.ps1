$ports = Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue
foreach ($port in $ports) { Stop-Process -Id $port.OwningProcess -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*lingnian_node*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
Write-Host '聆年本地影像节点已停止。'
