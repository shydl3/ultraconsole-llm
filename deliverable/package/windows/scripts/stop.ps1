. "$PSScriptRoot\common.ps1"

try {
    Assert-Docker
    Write-Step '停止服务（数据与模型保留）'
    Invoke-Compose down
    Write-Ok '已停止'
} catch {
    Write-Host "`n[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
