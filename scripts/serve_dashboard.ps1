$ErrorActionPreference = "Stop"

Set-Location "C:\capacity-intelligence-agentic"
$env:PYTHONPATH = "C:\capacity-intelligence-agentic"
$env:CAPACITY_DB_PATH = if ($env:CAPACITY_DB_PATH) { $env:CAPACITY_DB_PATH } else { "C:\capacity-intelligence-agentic\data\sumo_live_scored_report_30d.db" }
$port = if ($env:CAPACITY_PORT) { $env:CAPACITY_PORT } else { "8000" }

& "C:\Users\Aparna.Sankeshware\AppData\Local\Programs\Python\Python314\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
