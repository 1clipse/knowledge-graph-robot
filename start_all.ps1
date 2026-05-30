param(
    [string]$Neo4jHome = "E:\neo4j-community-2025.04.0-windows",
    [int]$BackendPort = 8100,
    [string]$HostName = "127.0.0.1",
    [switch]$NoReload,
    [switch]$OpenDocs,
    [switch]$OpenNeo4j,
    [switch]$NoBrowser,
    [switch]$UseVenv
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Resolve-Neo4jHome([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Neo4j path does not exist: $Path. Pass -Neo4jHome <path> to the Neo4j folder."
    }

    $candidate = Resolve-Path -LiteralPath $Path -ErrorAction Stop
    $candidatePath = $candidate.Path

    if (Test-Path (Join-Path $candidatePath "bin\neo4j.bat")) {
        return $candidatePath
    }

    $child = Get-ChildItem -LiteralPath $candidatePath -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "bin\neo4j.bat") } |
        Select-Object -First 1

    if ($null -ne $child) {
        return $child.FullName
    }

    throw "Cannot find bin\neo4j.bat under $Path"
}

function Test-Port([int]$Port) {
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(800)
        if ($ok) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        if ($null -ne $client) { $client.Close() }
    }
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $Port) {
            Write-Ok "$Name is ready on port $Port"
            return $true
        }
        Start-Sleep -Seconds 1
    }
    Write-Warn "$Name did not become ready on port $Port within ${TimeoutSeconds}s"
    return $false
}

function Resolve-PythonCommand([string]$ProjectRoot, [switch]$UseVenv) {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if ($UseVenv -or (Test-Path -LiteralPath $venvPython)) {
        if (Test-Path -LiteralPath $venvPython) {
            return $venvPython
        }
        throw "-UseVenv was specified, but .venv\Scripts\python.exe was not found."
    }
    return "python"
}

function Start-Neo4j([string]$ResolvedNeo4jHome) {
    Write-Step "Starting Neo4j database"
    if (Test-Port 7687) {
        Write-Ok "Neo4j Bolt port 7687 is already open; skipping Neo4j start."
        return $true
    }

    $neo4jBat = Join-Path $ResolvedNeo4jHome "bin\neo4j.bat"
    Start-Process -FilePath $neo4jBat -ArgumentList "console" -WorkingDirectory $ResolvedNeo4jHome -WindowStyle Normal
    return Wait-Port -Port 7687 -TimeoutSeconds 120 -Name "Neo4j"
}

function Start-Backend([string]$PythonCommand, [string]$ProjectRoot, [string]$HostName, [int]$BackendPort, [switch]$NoReload) {
    Write-Step "Starting local FastAPI backend and frontend static server"
    if (Test-Port $BackendPort) {
        Write-Ok "Backend port $BackendPort is already open; skipping backend start."
        return $true
    }

    $uvicornArgs = @("-m", "uvicorn", "api.app:app", "--host", $HostName, "--port", "$BackendPort")
    if (-not $NoReload) {
        $uvicornArgs += "--reload"
    }

    Start-Process -FilePath $PythonCommand -ArgumentList $uvicornArgs -WorkingDirectory $ProjectRoot -WindowStyle Normal
    return Wait-Port -Port $BackendPort -TimeoutSeconds 120 -Name "FastAPI"
}

function Invoke-HealthCheck([int]$BackendPort) {
    Write-Step "Health check"
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:${BackendPort}/health" -TimeoutSec 10
        $health | ConvertTo-Json -Depth 8
    } catch {
        Write-Warn "Health check failed: $($_.Exception.Message)"
    }
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Step "Project"
Write-Host "Project root: $ProjectRoot"

Write-Step "Resolving tools"
$PythonCommand = Resolve-PythonCommand -ProjectRoot $ProjectRoot -UseVenv:$UseVenv
Write-Host "Python: $PythonCommand"
$ResolvedNeo4jHome = Resolve-Neo4jHome $Neo4jHome
Write-Host "Neo4j home: $ResolvedNeo4jHome"

$neo4jReady = Start-Neo4j -ResolvedNeo4jHome $ResolvedNeo4jHome
$backendReady = Start-Backend -PythonCommand $PythonCommand -ProjectRoot $ProjectRoot -HostName $HostName -BackendPort $BackendPort -NoReload:$NoReload

if ($backendReady) {
    Invoke-HealthCheck -BackendPort $BackendPort
} else {
    Write-Warn "Skipping health check because backend is not ready."
}

Write-Step "URLs"
Write-Host "Frontend:        http://localhost:${BackendPort}"
Write-Host "Backend docs:    http://localhost:${BackendPort}/docs"
Write-Host "Neo4j Browser:   http://localhost:7474"
Write-Host "Neo4j Bolt:      bolt://localhost:7687"

if (-not $NoBrowser -and $backendReady) {
    Start-Process "http://localhost:${BackendPort}"
}
if ($OpenDocs -and $backendReady) {
    Start-Process "http://localhost:${BackendPort}/docs"
}
if ($OpenNeo4j -and $neo4jReady) {
    Start-Process "http://localhost:7474"
}

Write-Host "`nStarted." -ForegroundColor Green
Write-Host "Stop Neo4j:  cd `"$ResolvedNeo4jHome`"; .\bin\neo4j.bat stop"
Write-Host "Stop backend: close the uvicorn PowerShell window, or stop the python process."
Write-Host "Options: .\start_all.ps1 -NoReload -OpenDocs -OpenNeo4j -NoBrowser -UseVenv -BackendPort 8100"
