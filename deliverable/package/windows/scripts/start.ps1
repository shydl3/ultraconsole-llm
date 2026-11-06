. "$PSScriptRoot\common.ps1"

try {
    Write-Host ''
    Write-Host '  UltraConsole VLM  ——  视频事件语义分析' -ForegroundColor White
    Write-Host '  ---------------------------------------' -ForegroundColor DarkGray

    Assert-Docker
    Assert-Gpu

    foreach ($dir in @('images', 'models', 'models\detect', 'data', 'offline')) {
        $path = Join-Path $script:Root $dir
        if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
    }

    Assert-Image

    Invoke-Setup
    Invoke-Up

    $url = Wait-Health
    Start-Process $url | Out-Null
    Write-Host ''
    Write-Host "  已在浏览器打开：$url" -ForegroundColor Green
    Write-Host '  如需停止服务，双击「停止.bat」。' -ForegroundColor DarkGray
    Write-Host ''
} catch {
    Write-Host "`n[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
