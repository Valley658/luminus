$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this registration script as administrator.'
}
$python = (& python -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) { throw 'Python installation not found.' }
New-Item -ItemType Directory -Path (Join-Path $root 'runtime') -Force | Out-Null
[IO.File]::WriteAllText((Join-Path $root 'runtime\python-path.txt'), $python)
$runner = Join-Path $PSScriptRoot 'boot-server.ps1'
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runner + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT20S'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$account = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'Pastellive-WebServer' -Action $action -Trigger $trigger -Settings $settings -Principal $account -Description 'Pastellive local Python web server at system startup' -Force | Out-Null
Start-ScheduledTask -TaskName 'Pastellive-WebServer'
Write-Host 'Registered startup task. Starts after reboot without signing in.'
