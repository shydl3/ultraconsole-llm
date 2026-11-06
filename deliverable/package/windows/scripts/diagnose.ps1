. "$PSScriptRoot\common.ps1"

Write-Host ''
Write-Host '  UltraConsole VLM  ——  环境诊断' -ForegroundColor White
Write-Host '  ------------------------------' -ForegroundColor DarkGray

Write-Step 'Docker'
if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker version --format '  Client {{.Client.Version}} / Server {{.Server.Version}}'
} else {
    Write-Warn 'docker 命令不可用'
}

Write-Step 'NVIDIA 驱动'
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
} else {
    Write-Warn '未检测到 nvidia-smi'
}

Write-Step '镜像'
$image = Get-ImageName
& docker image inspect $image --format '  {{.RepoTags}}  {{.Size}} bytes' 2>$null
if ($LASTEXITCODE -ne 0) { Write-Warn "缺少镜像 $image" }

Write-Step '容器状态'
Push-Location $script:Root
try { & docker compose ps } finally { Pop-Location }

Write-Step '健康检查'
$port = Get-Setting 'ULTRACONSOLE_PORT' '8001'
$url = "http://127.0.0.1:$port/api/health"
if (Test-Health $url) { Write-Ok $url } else { Write-Warn "无法访问 $url" }

Write-Host ''
Write-Host '  按任意键关闭窗口...' -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
