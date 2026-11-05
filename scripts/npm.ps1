param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$NpmArguments = @('run', 'dev')
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$nodeVersion = (Get-Content -LiteralPath (Join-Path $projectRoot '.node-version') -Raw).Trim()
if ($nodeVersion -notmatch '^24\.\d+\.\d+$') { throw 'Invalid project Node version.' }
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
$nodeDirectory = Join-Path $projectRoot ".tools/node-v$nodeVersion-win-$architecture"
$savedPath = $env:PATH
Push-Location (Join-Path $projectRoot 'frontend')
try {
    if (Test-Path -LiteralPath (Join-Path $nodeDirectory 'node.exe')) {
        $env:PATH = $nodeDirectory + [IO.Path]::PathSeparator + $savedPath
    }
    $actualVersion = (& node --version).TrimStart('v')
    if ($LASTEXITCODE -ne 0 -or ([version]$actualVersion).Major -ne 24 -or [version]$actualVersion -lt [version]$nodeVersion) {
        throw 'Install the project runtime first: ./scripts/setup-node.ps1'
    }
    & npm.cmd @NpmArguments
    $result = $LASTEXITCODE
} finally {
    $env:PATH = $savedPath
    Pop-Location
}
exit $result
