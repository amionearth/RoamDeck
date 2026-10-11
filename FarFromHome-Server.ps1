param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDirectory = Join-Path $root "data\launcher-logs"
$stdoutLog = Join-Path $logDirectory "server-out.log"
$stderrLog = Join-Path $logDirectory "server-error.log"
$pidFile = Join-Path $logDirectory "server.pid"
$port = 8000

function Show-Message([string]$message, [int]$style) {
    $shell = New-Object -ComObject WScript.Shell
    $null = $shell.Popup($message, 0, "FarFromHome server", $style)
}

function Test-ServerReady {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if ($Action -eq "Stop") {
    if (-not (Test-Path $pidFile)) {
        return
    }

    $serverPid = 0
    if (-not [int]::TryParse((Get-Content $pidFile -Raw), [ref]$serverPid)) {
        Remove-Item $pidFile -Force
        Show-Message "The saved server process ID is invalid. No process was stopped." 16
        exit 1
    }

    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
    if ($serverProcess -and
        $serverProcess.CommandLine -like "*backend.app.main:app*" -and
        $serverProcess.CommandLine.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        Stop-Process -Id $serverPid -ErrorAction Stop
        Remove-Item $pidFile -Force
    }
    else {
        Remove-Item $pidFile -Force
        Show-Message "The recorded server process is not running. No other process was stopped." 48
    }
    exit 0
}

if (Test-ServerReady) {
    Start-Process "http://127.0.0.1:$port"
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

$pythonCandidates = @(
    (Join-Path $root ".venv\Scripts\python.exe"),
    (Join-Path $root "phone-app\.venv\Scripts\python.exe")
)
$pythonPath = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $pythonPath) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonPath = $pythonCommand.Source
    }
}
if (-not $pythonPath) {
    Show-Message "Python was not found. Install Python 3 and make sure python.exe is on PATH, then try again." 16
    exit 1
}

$env:HOST = "0.0.0.0"
$env:PORT = [string]$port
$arguments = "-m uvicorn backend.app.main:app --host 0.0.0.0 --port $port --app-dir `"$root`""
try {
    $server = Start-Process -FilePath $pythonPath -ArgumentList $arguments `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    Set-Content -Path $pidFile -Value $server.Id -Encoding ASCII
}
catch {
    Show-Message "Could not start the server:`n$($_.Exception.Message)" 16
    exit 1
}

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-ServerReady) {
        Start-Process "http://127.0.0.1:$port"
        exit 0
    }

    $server.Refresh()
    if ($server.HasExited) {
        break
    }
}

$output = @()
if (Test-Path $stderrLog) {
    $output += Get-Content $stderrLog -Tail 12
}
if (Test-Path $stdoutLog) {
    $output += Get-Content $stdoutLog -Tail 12
}
$details = ($output -join "`n").Trim()
if (-not $details) {
    $details = "The server did not respond at http://127.0.0.1:$port/api/health."
}
Show-Message "The server did not start. Check that its Python dependencies are installed.`n`n$details`n`nLogs: $logDirectory" 16
if (Test-Path $pidFile) {
    Remove-Item $pidFile -Force
}
exit 1
