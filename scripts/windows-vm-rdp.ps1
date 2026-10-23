#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$edition = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').EditionID
if ($edition -match 'Core|Home') { throw 'Windows Pro or Enterprise is required.' }
Get-NetFirewallRule -Name 'Hebi-RDP-Block-Bridge','Hebi-RDP-Host-Only','Hebi-RDP-All','Windows-VM-RDP-65535-TCP','Windows-VM-RDP-65535-UDP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-NetFirewallRule -Name 'RemoteDesktop*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
foreach ($protocol in 'TCP','UDP') {
 New-NetFirewallRule -Name "Windows-VM-RDP-65535-$protocol" -DisplayName "Windows VM RDP 65535 $protocol - all IPs" -Direction Inbound -Action Allow -Protocol $protocol -LocalPort 65535 -RemoteAddress Any -Profile Any | Out-Null
}
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
$rdp = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
Set-ItemProperty $rdp -Name PortNumber -Value 65535
Set-ItemProperty $rdp -Name UserAuthentication -Value 1
Set-Service TermService -StartupType Automatic
Restart-Service TermService -Force
Start-Sleep -Seconds 3
Write-Host 'RDP 65535 enabled for all IPs. Windows account/password required.' -ForegroundColor Green
Get-NetTCPConnection -LocalPort 65535 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort
Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -notlike '127.*' | Select-Object InterfaceAlias,IPAddress
