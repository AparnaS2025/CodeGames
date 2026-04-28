[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$DbPath = "",
    [switch]$GenerateReport,
    [switch]$StrictPort,
    [switch]$RunTests,
    [switch]$SkipServer
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-PortInUse {
    param([int]$CandidatePort)

    $connections = netstat -ano | Select-String -Pattern "LISTENING"
    return [bool]($connections | Where-Object { $_.Line -match "[:.]$CandidatePort\s+" })
}

function Get-AvailablePort {
    param([int]$PreferredPort)

    $candidate = $PreferredPort
    while (Test-PortInUse -CandidatePort $candidate) {
        $candidate += 1
    }
    return $candidate
}

Push-Location $PSScriptRoot
try {
    $env:PYTHONPATH = $PSScriptRoot

    $defaultScoredReportDb = Join-Path $PSScriptRoot "data\sumo_live_scored_report_30d.db"

    if ($GenerateReport) {
        Write-Host "Generating scored report from the previously fetched 30-day Sumo database..."
        & python scripts\generate_report_from_existing_db.py
        if ($LASTEXITCODE -ne 0) {
            throw "Report generation failed."
        }
    }

    if (-not $DbPath -and (Test-Path $defaultScoredReportDb)) {
        $DbPath = $defaultScoredReportDb
    }

    if ($DbPath) {
        $env:CAPACITY_DB_PATH = $DbPath
    }

    $env:CAPACITY_HOST = $ListenHost
    $env:CAPACITY_PORT = "$Port"

    if ($RunTests) {
        Write-Host "Running test suite..."
        & python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed."
        }
    }

    if ($SkipServer) {
        Write-Host "Skipping server start."
        return
    }

    $requestedPort = $Port
    if (Test-PortInUse -CandidatePort $Port) {
        if ($StrictPort) {
            throw "Port $Port is already in use. Stop the existing process or run .\run.ps1 -Port <free-port>."
        }
        $Port = Get-AvailablePort -PreferredPort ($Port + 1)
        $env:CAPACITY_PORT = "$Port"
        Write-Host "Port $requestedPort is already in use; using http://$ListenHost`:$Port instead."
    }

    Write-Host "Starting Capacity Intelligence MVP on http://$ListenHost`:$Port"
    if ($DbPath) {
        Write-Host "Using database path: $DbPath"
    }
    Write-Host "Dashboard: http://$ListenHost`:$Port/dashboard"
    Write-Host "API docs:  http://$ListenHost`:$Port/docs"

    & python -m uvicorn app.main:app --host $ListenHost --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Server exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
