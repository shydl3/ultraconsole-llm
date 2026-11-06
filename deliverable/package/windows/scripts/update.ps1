. "$PSScriptRoot\common.ps1"

try {
    Write-Host ''
    Write-Host '  UltraConsole VLM  ——  更新' -ForegroundColor White
    Write-Host '  -------------------------' -ForegroundColor DarkGray

    Assert-Docker
    Assert-Gpu

    $imagesDir = Join-Path $script:Root 'images'
    if (-not (Test-Path $imagesDir)) { New-Item -ItemType Directory -Path $imagesDir | Out-Null }
    $tar = Get-ChildItem -LiteralPath $imagesDir -Filter '*.tar.gz' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if ($tar) {
        $base = $tar.Name -replace '\.tar\.gz$', ''
        $dash = $base.LastIndexOf('-')
        $newTag = if ($dash -gt 0) { $base.Substring($dash + 1) } else { $null }

        Write-Step "导入新镜像 $($tar.Name)"
        & docker load -i $tar.FullName
        if ($LASTEXITCODE -ne 0) { Fail '镜像导入失败。' }

        if ($newTag -and (Get-Setting 'ULTRACONSOLE_TAG' '') -ne $newTag) {
            $envPath = Join-Path $script:Root '.env'
            $lines = Get-Content -LiteralPath $envPath -Encoding UTF8 | ForEach-Object {
                if ($_ -match '^\s*ULTRACONSOLE_TAG\s*=') { "ULTRACONSOLE_TAG=$newTag" } else { $_ }
            }
            [System.IO.File]::WriteAllText(
                $envPath,
                (($lines -join "`r`n") + "`r`n"),
                (New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false)
            )
            Write-Ok "配置已切换到镜像版本 $newTag"
        }
        Write-Ok '镜像已更新'
    } else {
        Write-Warn 'images 目录没有新的 *.tar.gz，将沿用本机现有镜像。'
        Assert-Image
    }

    Invoke-Setup
    Invoke-Up
    $url = Wait-Health
    Start-Process $url | Out-Null
    Write-Host ''
    Write-Host "  更新完成：$url" -ForegroundColor Green
    Write-Host '  历史记录与视频数据保持不变。' -ForegroundColor DarkGray
    Write-Host ''
} catch {
    Write-Host "`n[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
