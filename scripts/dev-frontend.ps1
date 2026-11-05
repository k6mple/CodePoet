$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'npm.ps1') run dev
exit $LASTEXITCODE
