$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
$uvExecutable = if ($uvCommand) { $uvCommand.Source } else { Join-Path $env:USERPROFILE '.local/bin/uv.exe' }
if (-not (Test-Path -LiteralPath $uvExecutable)) { throw 'Install uv and add it to PATH first.' }
Push-Location (Join-Path $projectRoot 'backend')
try {
    & $uvExecutable run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
