$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = [IO.File]::ReadAllText((Join-Path $root 'runtime\python-path.txt')).Trim()
New-Item -ItemType Directory -Path (Join-Path $root 'logs') -Force | Out-Null
Set-Location -LiteralPath $root
& $python -u (Join-Path $PSScriptRoot 'supervise-server.py') >> (Join-Path $root 'logs\boot-server.log') 2>&1
exit $LASTEXITCODE
