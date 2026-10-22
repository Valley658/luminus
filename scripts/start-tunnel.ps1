$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
try {
    & python (Join-Path $PSScriptRoot 'server.py') --status
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Already running: http://127.0.0.1/index2.html'
        exit 0
    }
    foreach ($folder in @('logs', 'runtime', 'bin')) {
        New-Item -ItemType Directory -Path (Join-Path $ProjectRoot $folder) -Force | Out-Null
    }
    $python = (& python -c 'import sys; print(sys.executable)').Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python is required.' }
    $worker = Join-Path $PSScriptRoot 'supervise-server.py'
    $process = Start-Process -FilePath $python -ArgumentList @('-u', ('"' + $worker + '"')) -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $ProjectRoot 'logs\server.log') -RedirectStandardError (Join-Path $ProjectRoot 'logs\server-error.log') -PassThru
    Start-Sleep -Milliseconds 1000
    if ($process.HasExited) { throw 'Startup failed. Check logs/server-error.log (port 80 may be in use).' }
    Write-Host 'Running locally: http://127.0.0.1/index2.html'
    exit 0
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
