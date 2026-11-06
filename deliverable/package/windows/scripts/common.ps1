$ErrorActionPreference = 'Stop'

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:NO_PROXY = if ($env:NO_PROXY) { "$env:NO_PROXY,127.0.0.1,localhost" } else { "127.0.0.1,localhost" }

$script:Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:EnvFile = Join-Path $script:Root '.env'

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Info([string]$Message) { Write-Host "    $Message" }
function Write-Ok([string]$Message) { Write-Host "    [OK] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    [!] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host "`n[错误] $Message" -ForegroundColor Red
    exit 1
}

function Get-Setting([string]$Name, [string]$Default) {
    if (-not (Test-Path $script:EnvFile)) { return $Default }
    $pattern = '^\s*' + [regex]::Escape($Name) + '\s*='
    $line = Get-Content -LiteralPath $script:EnvFile -Encoding UTF8 |
        Where-Object { $_ -match $pattern } |
        Select-Object -Last 1
    if (-not $line) { return $Default }
    return ($line -split '=', 2)[1].Trim()
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Push-Location $script:Root
    try {
        & docker compose @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose 执行失败（退出码 $LASTEXITCODE）"
        }
    } finally {
        Pop-Location
    }
}

function Get-ImageName {
    $image = Get-Setting 'ULTRACONSOLE_IMAGE' 'ultraconsole'
    $tag = Get-Setting 'ULTRACONSOLE_TAG' 'latest'
    return "${image}:${tag}"
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail '未检测到 docker 命令。请先安装 Docker Desktop（见「安装说明.txt」第 2 步）。'
    }

    & docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'Docker 已运行'
        return
    }

    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $exe)) {
        Fail 'Docker 未运行，且未找到 Docker Desktop。请手动启动 Docker Desktop 后重试。'
    }

    Write-Step '正在启动 Docker Desktop（首次启动可能需要 1-2 分钟）'
    Start-Process $exe | Out-Null

    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok 'Docker 已就绪'
            return
        }
    }
    Fail '等待 Docker 启动超时，请打开 Docker Desktop 查看状态后重试。'
}

function Assert-Image {
    $image = Get-ImageName
    & docker image inspect $image *> $null
    if ($LASTEXITCODE -eq 0) { return }

    $imagesDir = Join-Path $script:Root 'images'
    $tar = Get-ChildItem -LiteralPath $imagesDir -Filter '*.tar.gz' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $tar) {
        Fail "本机没有镜像 $image，images 目录里也没有可导入的 *.tar.gz。请确认交付包完整。"
    }

    Write-Step "导入镜像 $($tar.Name)（约 1-3 分钟）"
    & docker load -i $tar.FullName
    if ($LASTEXITCODE -ne 0) {
        Fail '镜像导入失败，请确认文件没有损坏或被占用。'
    }
    Write-Ok '镜像导入完成'
}

function Invoke-Setup {
    Write-Step '检查运行依赖与模型权重（首次运行需要下载数 GB，请保持网络畅通）'
    Invoke-Compose run -T --rm setup
    Write-Ok '依赖与权重已就绪'
}

function Invoke-Up {
    Write-Step '启动服务'
    Invoke-Compose up -d
}

function Test-Health([string]$Url) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $code = & curl.exe -s -o NUL -w '%{http_code}' --max-time 5 $Url 2>$null
        if ($null -eq $code) { return $false }
        return ([string]$code).Trim() -eq '200'
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Wait-Health([int]$TimeoutSec = 900) {
    $port = Get-Setting 'ULTRACONSOLE_PORT' '8001'
    $url = "http://127.0.0.1:$port/api/health"
    Write-Step "等待服务就绪：$url"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Health $url) {
            Write-Ok '服务已就绪'
            return $url
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 3
    }
    Write-Host ''
    Fail "服务在 $TimeoutSec 秒内没有就绪。请双击「查看日志.bat」排查。"
}

function Assert-Gpu {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Write-Warn '未检测到 nvidia-smi：请确认已安装 NVIDIA 显卡驱动。'
        return
    }
    $name = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
    if ($name) { Write-Ok "GPU：$name" }
}
