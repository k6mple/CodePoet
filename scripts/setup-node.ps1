$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$nodeVersion = (Get-Content -LiteralPath (Join-Path $projectRoot '.node-version') -Raw).Trim()
if ($nodeVersion -notmatch '^24\.\d+\.\d+$') { throw 'Expected a Node 24 release in .node-version.' }
$architecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($architecture -notin @('x64', 'arm64')) { throw 'Only Windows x64 and arm64 are supported by this script.' }
$archiveName = "node-v$nodeVersion-win-$architecture.zip"
$releaseUrl = "https://nodejs.org/dist/v$nodeVersion"
$downloadDir = Join-Path $env:TEMP ('codepoet-node-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
$archivePath = Join-Path $downloadDir $archiveName
Invoke-WebRequest -Uri "$releaseUrl/SHASUMS256.txt" -OutFile (Join-Path $downloadDir 'SHASUMS256.txt') -TimeoutSec 120
Invoke-WebRequest -Uri "$releaseUrl/$archiveName" -OutFile $archivePath -TimeoutSec 120
$checksum = Get-Content (Join-Path $downloadDir 'SHASUMS256.txt') | Where-Object { ($_ -split '\s+')[-1] -eq $archiveName }
if (-not $checksum) { throw 'Official archive checksum is missing.' }
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne ($checksum -split '\s+')[0]) {
    throw 'Node archive checksum mismatch.'
}
$toolsDirectory = Join-Path $projectRoot '.tools'
Expand-Archive -LiteralPath $archivePath -DestinationPath $toolsDirectory -Force
$nodeExecutable = Join-Path $toolsDirectory "node-v$nodeVersion-win-$architecture/node.exe"
$signature = Get-AuthenticodeSignature -LiteralPath $nodeExecutable
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'OpenJS Foundation') {
    throw 'Node executable signature is not valid for OpenJS Foundation.'
}
& $nodeExecutable --version
if ($LASTEXITCODE -ne 0) { throw 'Node verification failed.' }
Write-Output 'Project Node is ready. Use scripts/npm.ps1 for frontend commands.'
