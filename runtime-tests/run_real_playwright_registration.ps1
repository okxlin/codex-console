$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境 Python: $Python"
}

$env:APP_DATA_DIR = Join-Path $ProjectRoot 'runtime-tests\data'
$env:APP_LOGS_DIR = Join-Path $ProjectRoot 'runtime-tests\logs'

New-Item -ItemType Directory -Force -Path $env:APP_DATA_DIR | Out-Null
New-Item -ItemType Directory -Force -Path $env:APP_LOGS_DIR | Out-Null

& $Python (Join-Path $ProjectRoot 'runtime-tests\run_real_playwright_registration.py')
