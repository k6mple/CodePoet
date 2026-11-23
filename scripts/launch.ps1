<#
.SYNOPSIS
    CodePoet（代码诗）Windows 一键启动器

.DESCRIPTION
    自动完成环境检查（uv / Python .venv / Node 24 / npm 依赖）、数据库迁移与新库初始化、
    前端生产构建（按需）、端口冲突检测，并串联启动 FastAPI 后端与 Next.js 前端。

.EXAMPLE
    .\scripts\launch.ps1
    .\scripts\launch.ps1 -Mode dev
    .\scripts\launch.ps1 -Detached -NoBrowser
    .\scripts\launch.ps1 -Restart -Rebuild
#>
[CmdletBinding()]
param(
    [ValidateSet('prod', 'dev')]
    [string]$Mode = 'prod',

    [int]$BackendPort = 8000,

    [int]$FrontendPort = 3000,

    [string]$BindHost = '127.0.0.1',

    [string]$DataDir = '',

    [switch]$Rebuild,

    [switch]$Migrate,

    [switch]$Seed,

    [switch]$SyncDeps,

    [switch]$Restart,

    [switch]$Detached,

    [switch]$NoBrowser,

    [switch]$Help
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $ProjectRoot 'backend'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$LauncherDir = Join-Path $ProjectRoot '.tools\launcher'
$PidFile = Join-Path $LauncherDir 'pids.json'
$BuildTargetFile = Join-Path $LauncherDir 'build-target.txt'

function Show-Usage {
    @'
CodePoet（代码诗）一键启动器用法：

  直接双击根目录 一键启动.bat，或在仓库根目录执行：
    .\一键启动.bat
    .\scripts\launch.ps1 [参数]

常用参数：
  -Mode prod|dev       启动模式，默认 prod（next start + uvicorn）；dev 为开发热重载模式
  -BackendPort 8000    后端监听端口，默认 8000
  -FrontendPort 3000   前端监听端口，默认 3000
  -BindHost 127.0.0.1  监听地址，默认 127.0.0.1
  -DataDir <path>      指定数据目录（覆盖 CODEPOET_DATA_DIR，支持相对仓库根目录）
  -Rebuild             强制重新执行前端生产构建（next build）
  -Migrate             强制检查并执行数据库迁移
  -Seed                强制执行幂等演示账号与演示素材导入（seed.py / seed_demo.py）
  -SyncDeps            强制同步前后端依赖（uv sync / npm ci）
  -Restart             若端口上已有 CodePoet 服务在运行，先停止后再重启
  -Detached            后台启动并在就绪后退出当前命令（停止请运行 一键停止.bat）
  -NoBrowser           就绪后不自动打开浏览器
  -Help                显示本帮助信息
'@ | Write-Host
}

if ($Help) {
    Show-Usage
    exit 0
}

function Write-Step([string]$Message) {
    Write-Host "[CodePoet] $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-WarnLine([string]$Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Resolve-UvExecutable {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $fallback = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $fallback) { return $fallback }
    throw "未找到 uv。请先安装 uv（https://docs.astral.sh/uv/）并确保其在 PATH 或 $fallback 中。"
}

function Resolve-NodeExecutable {
    $nodeVersionPath = Join-Path $ProjectRoot '.node-version'
    $nodeVersion = (Get-Content -LiteralPath $nodeVersionPath -Raw).Trim()
    if ($nodeVersion -notmatch '^24\.\d+\.\d+$') {
        throw "无效的 .node-version 配置：$nodeVersion"
    }
    $arch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    $portableNode = Join-Path $ProjectRoot ".tools\node-v$nodeVersion-win-$arch\node.exe"

    if (-not (Test-Path -LiteralPath $portableNode)) {
        $sysNode = Get-Command node -ErrorAction SilentlyContinue
        if ($sysNode) {
            $actual = (& $sysNode.Source --version).TrimStart('v')
            if ($LASTEXITCODE -eq 0 -and ([version]$actual).Major -eq 24 -and ([version]$actual) -ge [version]$nodeVersion) {
                return $sysNode.Source
            }
        }
        Write-Step "未检测到项目 Node $nodeVersion，正在自动运行 setup-node.ps1 ..."
        & (Join-Path $PSScriptRoot 'setup-node.ps1')
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $portableNode)) {
            throw '安装项目 Node 24 运行时失败，请检查网络后重试。'
        }
    }
    return $portableNode
}

function Resolve-TargetDataDir {
    $raw = $DataDir
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $raw = $env:CODEPOET_DATA_DIR
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $envFile = Join-Path $ProjectRoot '.env'
        if (Test-Path -LiteralPath $envFile) {
            foreach ($line in (Get-Content -LiteralPath $envFile -Encoding UTF8)) {
                if ($line -match '^\s*CODEPOET_DATA_DIR\s*=\s*(.+?)\s*$') {
                    $candidate = $matches[1].Trim().Trim('"').Trim("'")
                    if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                        $raw = $candidate
                    }
                }
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $raw = 'data'
    }
    if ([System.IO.Path]::IsPathRooted($raw)) {
        return [System.IO.Path]::GetFullPath($raw)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $raw))
}

function Get-ListeningPid([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $conn) { return $null }
    return [int]$conn.OwningProcess
}

function Get-ProcessCommandLine([int]$ProcessId) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return '' }
    return [string]$proc.CommandLine
}

function Test-IsCodePoetProcess([int]$ProcessId) {
    $cmdLine = Get-ProcessCommandLine $ProcessId
    if ([string]::IsNullOrWhiteSpace($cmdLine)) { return $false }
    return ($cmdLine -match 'app\.main:app' -or $cmdLine -match 'next(\\|/|\s).*(\bstart\b|\bdev\b)' -or $cmdLine -match [regex]::Escape($ProjectRoot))
}

function Stop-ProcessTree([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) { return }
    & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
}

function Test-BackendHealthy([string]$BaseUrl) {
    try {
        $res = Invoke-RestMethod -Uri "$BaseUrl/api/v1/health" -TimeoutSec 2 -ErrorAction Stop
        return ($null -ne $res -and $res.status -eq 'ok')
    } catch {
        return $false
    }
}

function Test-FrontendHealthy([string]$BaseUrl) {
    try {
        $res = Invoke-WebRequest -Uri $BaseUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        return ($res.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Show-LogTail([string]$Label, [string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $lines = Get-Content -LiteralPath $Path -Tail 20 -ErrorAction SilentlyContinue
        if ($lines) {
            Write-WarnLine "---- $Label 最近日志 ($Path) ----"
            $lines | ForEach-Object { Write-Host "  $_" }
        }
    }
}

function Invoke-UvPython([string]$UvExe, [string[]]$ScriptArgs) {
    & $UvExe run --project $BackendDir --locked python @ScriptArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python 命令执行失败（退出码 $LASTEXITCODE）：python $($ScriptArgs -join ' ')"
    }
}

$backendUrl = "http://${BindHost}:${BackendPort}"
$frontendUrl = "http://${BindHost}:${FrontendPort}"
$resolvedDataDir = Resolve-TargetDataDir
$savedDataDirEnv = $env:CODEPOET_DATA_DIR
$savedHasDataDirEnv = Test-Path Env:CODEPOET_DATA_DIR
$savedApiProxyEnv = $env:API_PROXY_TARGET
$savedHasApiProxyEnv = Test-Path Env:API_PROXY_TARGET
$savedPythonIoEnv = $env:PYTHONIOENCODING
$savedHasPythonIoEnv = Test-Path Env:PYTHONIOENCODING
$savedPathEnv = $env:PATH

$startedBackendProc = $null
$startedFrontendProc = $null
$cleanupNeeded = $false

try {
    New-Item -ItemType Directory -Path $LauncherDir -Force | Out-Null

    # 1. 端口与已有服务检查
    $existingBackendPid = Get-ListeningPid $BackendPort
    $existingFrontendPid = Get-ListeningPid $FrontendPort

    if ($null -ne $existingBackendPid -and $null -ne $existingFrontendPid) {
        $backendOk = Test-BackendHealthy $backendUrl
        $frontendOk = Test-FrontendHealthy $frontendUrl
        if ($backendOk -and $frontendOk) {
            if (-not $Restart) {
                Write-Ok "检测到 CodePoet 服务已在运行（后端 PID $existingBackendPid / 前端 PID $existingFrontendPid）。"
                Write-Host "  前端入口：$frontendUrl"
                Write-Host "  接口文档：$backendUrl/api/docs"
                if (-not $NoBrowser) {
                    Start-Process $frontendUrl | Out-Null
                }
                exit 0
            }
            Write-Step '正在停止已有 CodePoet 服务以便重启...'
            if (-not (Test-IsCodePoetProcess $existingBackendPid) -or -not (Test-IsCodePoetProcess $existingFrontendPid)) {
                throw "端口 $BackendPort 或 $FrontendPort 上运行的服务不是本项目的 uvicorn/next 进程，已拒绝自动停止。"
            }
            Stop-ProcessTree $existingFrontendPid
            Stop-ProcessTree $existingBackendPid
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
            for ($i = 0; $i -lt 20; $i++) {
                if ($null -eq (Get-ListeningPid $BackendPort) -and $null -eq (Get-ListeningPid $FrontendPort)) { break }
                Start-Sleep -Milliseconds 250
            }
        }
    }

    $conflictBackend = Get-ListeningPid $BackendPort
    if ($null -ne $conflictBackend) {
        if ($Restart -and (Test-IsCodePoetProcess $conflictBackend)) {
            Write-Step "正在停止端口 $BackendPort 上的已有后端进程 (PID $conflictBackend)..."
            Stop-ProcessTree $conflictBackend
            Start-Sleep -Milliseconds 500
        } else {
            $cmdLine = Get-ProcessCommandLine $conflictBackend
            throw "后端端口 $BackendPort 已被占用（PID $conflictBackend）：$cmdLine`n请先停止对应进程，或使用 -BackendPort 指定其他端口，或使用 -Restart。"
        }
    }

    $conflictFrontend = Get-ListeningPid $FrontendPort
    if ($null -ne $conflictFrontend) {
        if ($Restart -and (Test-IsCodePoetProcess $conflictFrontend)) {
            Write-Step "正在停止端口 $FrontendPort 上的已有前端进程 (PID $conflictFrontend)..."
            Stop-ProcessTree $conflictFrontend
            Start-Sleep -Milliseconds 500
        } else {
            $cmdLine = Get-ProcessCommandLine $conflictFrontend
            throw "前端端口 $FrontendPort 已被占用（PID $conflictFrontend）：$cmdLine`n请先停止对应进程，或使用 -FrontendPort 指定其他端口，或使用 -Restart。"
        }
    }

    # 2. 环境与依赖检查
    Write-Step '检查后端环境 (uv + Python 3.12)...'
    $uvExe = Resolve-UvExecutable
    $venvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
    if ($SyncDeps -or -not (Test-Path -LiteralPath $venvPython)) {
        Write-Step '正在同步后端依赖 (uv sync --project backend --locked)...'
        & $uvExe sync --project $BackendDir --locked
        if ($LASTEXITCODE -ne 0) { throw '后端依赖同步失败。' }
    }

    Write-Step '检查前端环境 (Node 24 + npm 依赖)...'
    $nodeExe = Resolve-NodeExecutable
    $nodeDir = Split-Path $nodeExe -Parent
    $nodeModulesDir = Join-Path $FrontendDir 'node_modules'
    if ($SyncDeps -or -not (Test-Path -LiteralPath $nodeModulesDir)) {
        Write-Step '正在安装前端依赖 (scripts/npm.ps1 ci)...'
        & (Join-Path $PSScriptRoot 'npm.ps1') ci
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
    }

    # 3. 环境变量注入（仅当前进程及子进程，不改写 .env）
    $env:CODEPOET_DATA_DIR = $resolvedDataDir
    $env:API_PROXY_TARGET = $backendUrl
    $env:PYTHONIOENCODING = 'utf-8'

    # 4. 数据库与迁移检查
    $dbPath = Join-Path $resolvedDataDir 'codepoet.sqlite3'
    if (-not (Test-Path -LiteralPath $dbPath)) {
        Write-Step "未检测到数据库 ($dbPath)，正在执行首次建库、演示账号与示例素材初始化..."
        Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\migrate.py'))
        Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\seed.py'))
        Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\seed_demo.py'))
        Write-Ok '数据库与演示数据初始化完成。'
    } else {
        # 只读核对迁移状态，避免无待应用迁移时写入现有库（避免含双引号被 PS 5.1 传参剥离）
        $checkScript = @'
import sqlite3, sys
from pathlib import Path
db_path = Path(sys.argv[1])
mig_dir = Path(sys.argv[2])
expected = len(list(mig_dir.glob('*.sql')))
uri = db_path.resolve().as_uri() + '?mode=ro'
con = sqlite3.connect(uri, uri=True)
try:
    has_table = con.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', 'schema_migrations')).fetchone()
    if not has_table:
        print(f'pending:0/{expected}')
        sys.exit(0)
    applied = con.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
    print(f'ok:{applied}/{expected}' if applied >= expected else f'pending:{applied}/{expected}')
finally:
    con.close()
'@
        $migRaw = & $venvPython -c $checkScript $dbPath (Join-Path $BackendDir 'migrations')
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace("$migRaw")) { throw '只读检查数据库迁移状态失败。' }
        $migState = ("$migRaw").Trim()
        if ($Migrate -or $migState -like 'pending:*') {
            Write-Step "检测到待执行迁移 ($migState)，正在运行 migrate.py..."
            Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\migrate.py'))
        } else {
            Write-Ok "数据库迁移已是最新 ($($migState -replace '^ok:', ''))。"
        }
        if ($Seed) {
            Write-Step '正在执行幂等演示数据导入 (seed.py / seed_demo.py)...'
            Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\seed.py'))
            Invoke-UvPython $uvExe @((Join-Path $ProjectRoot 'scripts\seed_demo.py'))
        }
    }

    # 5. 前端生产构建检查
    if ($Mode -eq 'prod') {
        $buildIdPath = Join-Path $FrontendDir '.next\BUILD_ID'
        $hasBuild = Test-Path -LiteralPath $buildIdPath
        $recordedTarget = if (Test-Path -LiteralPath $BuildTargetFile) { (Get-Content -LiteralPath $BuildTargetFile -Raw).Trim() } else { '' }
        $targetChanged = ($BackendPort -ne 8000 -and $recordedTarget -ne $backendUrl) -or (-not [string]::IsNullOrWhiteSpace($recordedTarget) -and $recordedTarget -ne $backendUrl)
        if ($Rebuild -or -not $hasBuild -or $targetChanged) {
            Write-Step "正在执行前端生产构建 (API_PROXY_TARGET=$backendUrl)..."
            & (Join-Path $PSScriptRoot 'npm.ps1') run build
            if ($LASTEXITCODE -ne 0) { throw '前端生产构建失败。' }
            [System.IO.File]::WriteAllText($BuildTargetFile, $backendUrl, [System.Text.UTF8Encoding]::new($false))
            Write-Ok '前端生产构建完成。'
        } else {
            Write-Ok '前端生产产物已就绪（如需重编请加 -Rebuild）。'
        }
    }

    # 6. 启动后端与前端服务
    $backendOut = Join-Path $LauncherDir 'backend.out.log'
    $backendErr = Join-Path $LauncherDir 'backend.err.log'
    $frontendOut = Join-Path $LauncherDir 'frontend.out.log'
    $frontendErr = Join-Path $LauncherDir 'frontend.err.log'

    $backendArgs = @('-m', 'uvicorn', 'app.main:app', '--host', $BindHost, '--port', "$BackendPort")
    if ($Mode -eq 'dev') { $backendArgs += '--reload' }

    Write-Step "启动后端服务 ($backendUrl, 模式=$Mode)..."
    $startedBackendProc = Start-Process -FilePath $venvPython `
        -ArgumentList $backendArgs `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -PassThru
    $cleanupNeeded = $true

    $backendReady = $false
    for ($i = 0; $i -lt 100; $i++) {
        if ($startedBackendProc.HasExited) {
            Show-LogTail '后端 stderr' $backendErr
            Show-LogTail '后端 stdout' $backendOut
            throw "后端进程提前退出（退出码 $($startedBackendProc.ExitCode)）。"
        }
        if (Test-BackendHealthy $backendUrl) {
            $backendReady = $true
            break
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $backendReady) {
        Show-LogTail '后端 stderr' $backendErr
        throw "等待后端健康检查超时：$backendUrl/api/v1/health"
    }
    Write-Ok "后端已就绪 (PID $($startedBackendProc.Id))。"

    $env:PATH = $nodeDir + [IO.Path]::PathSeparator + $savedPathEnv
    $nextCli = Join-Path $FrontendDir 'node_modules\next\dist\bin\next'
    $nextSubCmd = if ($Mode -eq 'dev') { 'dev' } else { 'start' }
    $frontendArgs = @($nextCli, $nextSubCmd, '-H', $BindHost, '-p', "$FrontendPort")

    Write-Step "启动前端服务 ($frontendUrl, 模式=$Mode)..."
    $startedFrontendProc = Start-Process -FilePath $nodeExe `
        -ArgumentList $frontendArgs `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -PassThru

    $frontendReady = $false
    for ($i = 0; $i -lt 150; $i++) {
        if ($startedFrontendProc.HasExited) {
            Show-LogTail '前端 stderr' $frontendErr
            Show-LogTail '前端 stdout' $frontendOut
            throw "前端进程提前退出（退出码 $($startedFrontendProc.ExitCode)）。"
        }
        if (Test-FrontendHealthy $frontendUrl) {
            $frontendReady = $true
            break
        }
        Start-Sleep -Milliseconds 300
    }
    if (-not $frontendReady) {
        Show-LogTail '前端 stderr' $frontendErr
        throw "等待前端服务就绪超时：$frontendUrl"
    }
    Write-Ok "前端已就绪 (PID $($startedFrontendProc.Id))。"

    # 记录运行信息供 stop.ps1 使用
    $pidInfo = [ordered]@{
        mode         = $Mode
        host         = $BindHost
        backendPort  = $BackendPort
        frontendPort = $FrontendPort
        backendPid   = $startedBackendProc.Id
        frontendPid  = $startedFrontendProc.Id
        dataDir      = $resolvedDataDir
        startedAt    = (Get-Date).ToString('o')
    } | ConvertTo-Json -Depth 3
    [System.IO.File]::WriteAllText($PidFile, $pidInfo, [System.Text.UTF8Encoding]::new($false))

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor DarkCyan
    Write-Host '  CodePoet（代码诗）启动成功！' -ForegroundColor Green
    Write-Host "  运行模式：$Mode"
    Write-Host "  前端页面：$frontendUrl"
    Write-Host "  接口文档：$backendUrl/api/docs"
    Write-Host "  健康检查：$backendUrl/api/v1/health"
    Write-Host "  数据目录：$resolvedDataDir"
    Write-Host "  演示账号：creator_demo / developer_demo / expert_demo / admin_demo（初始密码见 scripts/seed.py）"
    Write-Host "  运行日志：$LauncherDir"
    Write-Host '============================================================' -ForegroundColor DarkCyan
    Write-Host ''

    if (-not $NoBrowser) {
        Start-Process $frontendUrl | Out-Null
    }

    if ($Detached) {
        Write-Host '已在后台运行。如需停止，请运行 .\一键停止.bat 或 .\scripts\stop.ps1。'
        $cleanupNeeded = $false
        exit 0
    }

    Write-Host '服务运行中... 按 Q 或 Ctrl+C 停止全部服务（若直接关闭窗口，可运行 一键停止.bat）。'
    while ($true) {
        if ($startedBackendProc.HasExited) {
            Show-LogTail '后端 stderr' $backendErr
            throw "后端进程意外退出（退出码 $($startedBackendProc.ExitCode)）。"
        }
        if ($startedFrontendProc.HasExited) {
            Show-LogTail '前端 stderr' $frontendErr
            throw "前端进程意外退出（退出码 $($startedFrontendProc.ExitCode)）。"
        }
        try {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::Q) {
                    Write-Step '收到停止指令，正在关闭前后端服务...'
                    break
                }
            }
        } catch {
            # 非交互宿主忽略 KeyAvailable
        }
        Start-Sleep -Milliseconds 400
    }
} finally {
    if ($cleanupNeeded) {
        if ($null -ne $startedFrontendProc) { Stop-ProcessTree $startedFrontendProc.Id }
        if ($null -ne $startedBackendProc) { Stop-ProcessTree $startedBackendProc.Id }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        Write-Ok '已停止 CodePoet 前后端服务。'
    } elseif ($null -ne $startedBackendProc -and $null -eq $startedFrontendProc) {
        # 前端启动失败时回收刚拉起的后端
        Stop-ProcessTree $startedBackendProc.Id
    }

    $env:PATH = $savedPathEnv
    if ($savedHasDataDirEnv) { $env:CODEPOET_DATA_DIR = $savedDataDirEnv } else { Remove-Item Env:CODEPOET_DATA_DIR -ErrorAction SilentlyContinue }
    if ($savedHasApiProxyEnv) { $env:API_PROXY_TARGET = $savedApiProxyEnv } else { Remove-Item Env:API_PROXY_TARGET -ErrorAction SilentlyContinue }
    if ($savedHasPythonIoEnv) { $env:PYTHONIOENCODING = $savedPythonIoEnv } else { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
}
