. "$PSScriptRoot\common.ps1"

$data = Join-Path $script:Root 'data'
if (-not (Test-Path $data)) { New-Item -ItemType Directory -Path $data | Out-Null }
Start-Process explorer.exe $data

$models = Join-Path $script:Root 'models'
if (Test-Path $models) { Start-Process explorer.exe $models }
