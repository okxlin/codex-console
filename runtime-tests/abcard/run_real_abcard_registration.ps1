$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    throw "Python virtual environment not found: $pythonExe"
}

if (-not $env:RUNTIME_TEST_PROXY) {
    $env:RUNTIME_TEST_PROXY = "socks5://127.0.0.1:31156"
}

if (-not $env:RUNTIME_TEST_EMAIL_SERVICE) {
    $env:RUNTIME_TEST_EMAIL_SERVICE = "yyds_mail"
}

& $pythonExe (Join-Path $projectRoot "runtime-tests\abcard\run_real_abcard_registration.py")
