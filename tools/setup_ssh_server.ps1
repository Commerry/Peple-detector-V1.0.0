# Enable SSH (and the People Counter port) on this Windows machine.
# Copy this file to the mini PC, then right-click it > Run with PowerShell,
# or from an ADMIN PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup_ssh_server.ps1

$ErrorActionPreference = 'Stop'

# --- must be administrator ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Run this in PowerShell as Administrator." -ForegroundColor Red
    Write-Host "Right-click Start > Terminal (Admin) / PowerShell (Admin), then run it again."
    exit 1
}

Write-Host "== 1. Installing OpenSSH Server ==" -ForegroundColor Cyan
$cap = Get-WindowsCapability -Online -Name OpenSSH.Server* | Select-Object -First 1
if ($null -eq $cap) {
    # very old builds do not expose the capability; fall back to the fixed name
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    Write-Host "installed (fallback name)."
} elseif ($cap.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $cap.Name
    Write-Host "installed."
} else {
    Write-Host "already installed."
}

Write-Host "`n== 2. Starting the service ==" -ForegroundColor Cyan
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Get-Service sshd | Select-Object Name, Status, StartType | Format-Table -AutoSize

Write-Host "== 3. Firewall rules ==" -ForegroundColor Cyan
foreach ($rule in @(
    @{ Name = 'OpenSSH-Server-In-TCP'; Display = 'OpenSSH Server (sshd)'; Port = 22 },
    @{ Name = 'PeopleCounter-8000';    Display = 'People Counter web app'; Port = 8000 }
)) {
    if (-not (Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $rule.Name -DisplayName $rule.Display `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow `
            -LocalPort $rule.Port | Out-Null
        Write-Host ("opened port {0} ({1})" -f $rule.Port, $rule.Display)
    } else {
        Write-Host ("port {0} rule already exists" -f $rule.Port)
    }
}

# PowerShell is a far nicer SSH shell than the default cmd.exe
Write-Host "`n== 4. Default SSH shell -> PowerShell ==" -ForegroundColor Cyan
$ps = (Get-Command powershell.exe).Source
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell `
    -Value $ps -PropertyType String -Force | Out-Null
Write-Host $ps

Write-Host "`n== 5. Connect with ==" -ForegroundColor Cyan
$user = $env:USERNAME
$ips = Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }
Write-Host ("hostname : {0}" -f $env:COMPUTERNAME)
Write-Host ("username : {0}" -f $user)
foreach ($ip in $ips) {
    Write-Host ("   ssh {0}@{1}" -f $user, $ip.IPAddress) -ForegroundColor Green
}

# Windows refuses remote logins for accounts with no password.
# (This check only works on English Windows; harmless if it finds nothing.)
$pwdLine = net user $user 2>$null | Select-String 'Password required'
if ($pwdLine -and $pwdLine.ToString() -match 'No\s*$') {
    Write-Host "`nWARNING: this account has no password - remote login will be refused." -ForegroundColor Yellow
    Write-Host "Set one with:  net user $user *"
}

Write-Host "`nDone. Test from the other PC with one of the ssh lines above."
