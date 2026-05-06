[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8000,
    [string]$DbPath = "",
    [switch]$GenerateReport,
    [switch]$StrictPort,
    [switch]$RunTests,
    [switch]$SkipServer,
    [string]$EnvFile = ".env",
    [switch]$SkipEnv
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

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

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        Write-Host "No .env file found at $Path; continuing with current environment."
        return
    }

    $loadedKeys = New-Object System.Collections.Generic.List[string]
    foreach ($rawLine in [System.IO.File]::ReadLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }

        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            continue
        }

        $key = $line.Substring(0, $separatorIndex).Trim()
        $value = $line.Substring($separatorIndex + 1).Trim()

        if ($key -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
            continue
        }

        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
        $loadedKeys.Add($key) | Out-Null
    }

    if ($loadedKeys.Count -gt 0) {
        Write-Host "Loaded .env values: $($loadedKeys -join ', ')"
    }
}

Push-Location $PSScriptRoot
try {
    if (-not $SkipEnv) {
        $envPath = $EnvFile
        if (-not [System.IO.Path]::IsPathRooted($envPath)) {
            $envPath = Join-Path $PSScriptRoot $envPath
        }
        Import-DotEnv -Path $envPath
    }

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

    & python -m uvicorn app.main:app --host $ListenHost --port $Port 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Server exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
