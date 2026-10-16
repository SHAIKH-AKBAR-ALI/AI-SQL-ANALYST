param([switch]$Test, [switch]$Reload, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project .venv first. See README.md.' }
Push-Location (Join-Path $projectRoot 'backend')
try {
    if ($Test) {
        & $pythonPath -m unittest discover -s tests -v
    } else {
        $serverArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port", '--env-file', '.env', '--no-proxy-headers')
        if ($Reload) { $serverArgs += '--reload' }
        & $pythonPath @serverArgs
    }
    $resultCode = $LASTEXITCODE
} finally { Pop-Location }
exit $resultCode
