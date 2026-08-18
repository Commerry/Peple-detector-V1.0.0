"""Diagnose why a machine answers ping but refuses SSH.

    .venv\\Scripts\\python.exe tools\\check_remote.py 10.1.100.60

Reports which remote-access ports are open and guesses the OS from the ping
TTL, then prints what to do next.
"""
import re
import socket
import subprocess
import sys

PORTS = {
    22: "SSH",
    3389: "RDP (Windows Remote Desktop)",
    5985: "WinRM / PowerShell Remoting (HTTP)",
    5986: "WinRM (HTTPS)",
    445: "SMB file sharing",
    135: "Windows RPC",
    8000: "People Counter web app",
    80: "HTTP",
    443: "HTTPS",
}


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def ping_ttl(host: str) -> int | None:
    try:
        out = subprocess.run(
            ["ping", "-n", "2", host], capture_output=True, text=True, timeout=15
        ).stdout
        m = re.search(r"TTL=(\d+)", out, re.IGNORECASE)
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: check_remote.py <ip-address>")
        sys.exit(1)
    host = sys.argv[1]

    print("-" * 62)
    print(f" Remote access check: {host}")
    print("-" * 62)

    ttl = ping_ttl(host)
    if ttl is None:
        print("ping        : NO REPLY (host down, or ICMP blocked)")
    else:
        # routers decrement TTL, so round up to the nearest common start value
        guess = "Windows" if ttl > 64 else "Linux / Unix"
        print(f"ping        : OK (TTL={ttl} -> probably {guess})")

    open_ports = []
    print("\nport scan:")
    for port, label in PORTS.items():
        ok = port_open(host, port)
        if ok:
            open_ports.append(port)
        print(f"  {port:>5}  {'OPEN  ' if ok else 'closed'}  {label}")

    print("\n" + "-" * 62)
    print(" WHAT TO DO")
    print("-" * 62)

    if 22 in open_ports:
        print("SSH is listening. The problem is login, not the service:")
        print("  - wrong username: use  ssh <windows-username>@" + host)
        print("  - password rejected: check the account has a password set")
        print("  - key refused: try  ssh -o PreferredAuthentications=password ...")
        return

    print("SSH (port 22) is NOT listening. Options, easiest first:\n")

    if 3389 in open_ports:
        print("1) USE REMOTE DESKTOP - it is already enabled on that machine.")
        print(f"     Press Win+R, type:  mstsc /v:{host}")
        print("     That gives you the full desktop; no SSH needed.\n")

    print("2) INSTALL SSH SERVER on the mini PC (needs keyboard/monitor or RDP once).")
    print("   Open PowerShell AS ADMINISTRATOR on that machine and run:\n")
    print("     Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0")
    print("     Start-Service sshd")
    print("     Set-Service -Name sshd -StartupType Automatic")
    print("     New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' \\")
    print("       -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22\n")
    print(f"   Then from here:  ssh <username>@{host}\n")

    if 3389 not in open_ports:
        print("3) ENABLE REMOTE DESKTOP instead (also needs one-time local access):")
        print("     Settings > System > Remote Desktop > On")
        print("   or in PowerShell as admin:")
        print("     Set-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' \\")
        print("       -Name fDenyTSConnections -Value 0")
        print("     Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'\n")

    if 8000 in open_ports:
        print("NOTE: the People Counter app IS already running on that machine")
        print(f"      -> open  http://{host}:8000  in a browser right now.")
    else:
        print("NOTE: port 8000 is closed, so the app is not running there yet")
        print("      (or its firewall rule is missing).")

    if not open_ports:
        print("\nEvery port is closed but ping works: the Windows firewall is")
        print("blocking everything, or the machine is freshly installed with no")
        print("services enabled. You will need keyboard + monitor on it once.")


if __name__ == "__main__":
    main()
