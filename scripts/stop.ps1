<#
.SYNOPSIS
    停止由 CodePoet 一键启动器拉起的前后端服务
#>
[CmdletBinding()]
param(
    [switch]$ForcePorts,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$PidFile = Join-Path $ProjectRoot '.tools\launcher\pids.json'

function Stop-ProcessTree([int]$ProcessId, [string]$Label) {
    if ($ProcessId -le 0) { return $false }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        Write-Host "[SKIP] $Label (PID $ProcessId) 已不在运行。"
        return $false
    }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    Write-Host "[OK] 已停止 $Label (PID $ProcessId)。" -ForegroundColor Green
    return $true
}

$stoppedAny = $false

if (Test-Path -LiteralPath $PidFile) {
    $info = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($null -ne $info.frontendPid) {
        if (Stop-ProcessTree ([int]$info.frontendPid) "前端 ($($info.frontendPort))") { $stoppedAny = $true }
    }
    if ($null -ne $info.backendPid) {
        if (Stop-ProcessTree ([int]$info.backendPid) "后端 ($($info.backendPort))") { $stoppedAny = $true }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
} elseif ($ForcePorts) {
    foreach ($entry in @(@{ Port = $FrontendPort; Label = '前端'; Pattern = 'next' }, @{ Port = $BackendPort; Label = '后端'; Pattern = 'app\.main:app' })) {
        $conn = Get-NetTCPConnection -LocalPort $entry.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $conn) {
            $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
            if ($null -ne $procInfo -and ($procInfo.CommandLine -match $entry.Pattern -or $procInfo.CommandLine -match [regex]::Escape($ProjectRoot))) {
                if (Stop-ProcessTree ([int]$conn.OwningProcess) "$($entry.Label) ($($entry.Port))") { $stoppedAny = $true }
            } else {
                Write-Host "[WARN] 端口 $($entry.Port) 上的进程 (PID $($conn.OwningProcess)) 不像本项目服务，已跳过。" -ForegroundColor Yellow
            }
        }
    }
} else {
    Write-Host '[INFO] 未找到启动器记录 (.tools\launcher\pids.json)；如需按端口停止本项目服务，请加 -ForcePorts。'
}

if ($stoppedAny) {
    Write-Host '[CodePoet] 服务清理完成。' -ForegroundColor Cyan
}
