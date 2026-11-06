. "$PSScriptRoot\common.ps1"

Assert-Docker
Write-Host ''
Write-Host '  实时日志（Ctrl+C 退出）' -ForegroundColor Cyan
Write-Host ''

Push-Location $script:Root
try {
    & docker compose logs -f --tail 200
} finally {
    Pop-Location
}

Write-Host ''
Write-Host '  按任意键关闭窗口...' -ForegroundColor DarkGray
try { $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown') } catch { }
