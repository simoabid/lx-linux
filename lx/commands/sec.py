"""lx sec — security audit: open ports, sudoers, SSH config, SUID files."""
from __future__ import annotations

import socket
import stat
from pathlib import Path

import click
import psutil
from rich.panel import Panel
from rich.table import Table

from lx.utils.output import center_rule, err, ok, warn
from lx.utils.shell import run


def _severity_score(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(severity, 0)


def _ssh_audit(console) -> list[tuple[str, str, str]]:
    findings = []
    cfg_path = Path("/etc/ssh/sshd_config")
    cfg_dropins = Path("/etc/ssh/sshd_config.d")
    content = ""
    if cfg_path.exists():
        content += cfg_path.read_text()
    if cfg_dropins.exists():
        for p in cfg_dropins.glob("*.conf"):
            content += "\n" + p.read_text()

    def get_value(key: str) -> str:
        for line in content.splitlines():
            if line.strip().startswith(key):
                # `Key value`
                parts = line.strip().split(None, 1)
                return parts[1] if len(parts) > 1 else ""
        return ""

    if get_value("PermitRootLogin").lower() in ("yes", ""):
        findings.append(("SSH", "high", "PermitRootLogin is yes or unset — disable it"))
    if get_value("PasswordAuthentication").lower() in ("yes", ""):
        findings.append(("SSH", "medium", "PasswordAuthentication allows password logins"))
    if get_value("PermitEmptyPasswords").lower() in ("yes", ""):
        findings.append(("SSH", "critical", "PermitEmptyPasswords is enabled (!)"))
    if get_value("X11Forwarding").lower() == "yes":
        findings.append(("SSH", "low", "X11Forwarding is on (small surface increase)"))
    port = get_value("Port") or "22"
    if port == "22":
        findings.append(("SSH", "info", "SSH still on port 22 (move it for noise reduction)"))

    center_rule(console, "SSH Configuration")
    if not content:
        console.print("[dim]ssh config not found — defaults apply (root login + password auth OPEN)[/dim]")
        return findings
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Setting")
    t.add_column("Value")
    for k in ("Port", "PermitRootLogin", "PasswordAuthentication", "PermitEmptyPasswords", "PubkeyAuthentication",
              "X11Forwarding", "AllowUsers", "MaxAuthTries"):
        v = get_value(k)
        if v or k in ("Port", "PermitRootLogin", "PasswordAuthentication"):
            color = ""
            if k == "PermitRootLogin" and v.lower() == "yes":
                color = "red"
            elif k == "PasswordAuthentication" and v.lower() == "yes":
                color = "yellow"
            t.add_row(k, f"[{color}]{v or 'unset'}[/{color}]" if color else (v or "unset"))
    console.print(t)
    return findings


def _ports_audit(console) -> list[tuple[str, str, str]]:
    findings = []
    center_rule(console, "Listening Sockets")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Proto")
    t.add_column("Address")
    t.add_column("PID")
    t.add_column("Process")
    wildcards = 0
    for c in psutil.net_connections(kind="inet"):
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        if c.laddr.ip == "0.0.0.0" or c.laddr.ip == "::":
            wildcards += 1
        try:
            name = psutil.Process(c.pid).name() if c.pid else "—"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = "—"
        proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
        t.add_row(proto, f"{c.laddr.ip}:{c.laddr.port}", str(c.pid or "—"), name)
    console.print(t)
    if wildcards:
        findings.append(("Ports", "medium", f"{wildcards} socket(s) bound to all interfaces"))
    return findings


def _sudoers_audit(console) -> list[tuple[str, str, str]]:
    findings = []
    center_rule(console, "Sudoers")
    # Check /etc/group for sudo/wheel membership (reliable without root)
    groups = Path("/etc/group").read_text() if Path("/etc/group").exists() else ""
    sudo_users = []
    for line in groups.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] in ("sudo", "wheel"):
            members = [m for m in parts[3].split(",") if m]
            sudo_users.extend(members)

    if sudo_users:
        console.print(f"[bold]Users in sudo/wheel group:[/bold] {', '.join(sorted(set(sudo_users))) or 'none'}")
        if len(sudo_users) > 5:
            findings.append(("Sudo", "low", f"unusually many ({len(sudo_users)}) sudoers"))
    else:
        console.print("[dim]could not enumerate sudoers (try running as root)[/dim]")
    return findings


def _suid_audit(console) -> list[tuple[str, str, str]]:
    findings = []
    center_rule(console, "SUID/SGID binaries (scanning /usr, /bin, /sbin)")
    hits = []
    roots = [Path(r) for r in ("/usr", "/bin", "/sbin", "/opt") if Path(r).exists()]
    for root in roots:
        for path in root.rglob("*"):
            try:
                st = path.stat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and (st.st_mode & stat.S_ISUID or st.st_mode & stat.S_ISGID):
                hits.append((path, "suid" if st.st_mode & stat.S_ISUID else "sgid"))
    weird = [p for p, _ in hits if str(p).startswith("/usr/local")]
    hits.sort(key=lambda x: str(x[0]))
    for p, kind in hits[:25]:
        console.print(f"  [{kind}] {p}")
    if len(hits) > 25:
        console.print(f"[dim]…{len(hits) - 25} more[/dim]")
    if weird:
        findings.append(("SUID", "medium", f"{len(weird)} SUID files in non-standard locations (/usr/local/*)"))
    return findings


def _kernel_params(console) -> list[tuple[str, str, str]]:
    findings = []
    center_rule(console, "Kernel security params")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Key")
    t.add_column("Value")
    keys = [
        "kernel.randomize_va_space",
        "kernel.kptr_restrict",
        "kernel.dmesg_restrict",
        "kernel.unprivileged_bpf_disabled",
        "kernel.yama.ptrace_scope",
        "net.ipv4.conf.all.rp_filter",
        "net.ipv4.conf.all.accept_redirects",
        "net.ipv4.conf.all.send_redirects",
        "net.ipv4.conf.all.log_martians",
        "fs.protected_hardlinks",
        "fs.protected_symlinks",
        "fs.suid_dumpable",
    ]
    for k in keys:
        res = run(["sysctl", "-n", k], timeout=5)
        v = res.stdout or "—"
        color = ""
        if k == "kernel.randomize_va_space" and v != "2":
            color = "red"
            findings.append(("Kernel", "high", f"{k} = {v} (should be 2)"))
        elif k == "net.ipv4.conf.all.accept_redirects" and v == "1":
            color = "yellow"
            findings.append(("Kernel", "medium", f"{k} enabled (should be 0)"))
        elif k == "fs.suid_dumpable" and v != "0":
            color = "yellow"
            findings.append(("Kernel", "low", f"{k} = {v} (should be 0)"))
        t.add_row(k, f"[{color}]{v}[/{color}]" if color else v)
    console.print(t)
    return findings


def _firewall_audit(console) -> list[tuple[str, str, str]]:
    findings = []
    center_rule(console, "Firewall")
    fw_active = False
    for fw in ("ufw", "firewalld", "iptables"):
        res = run([fw, "status"] if fw != "firewalld" else ["firewall-cmd", "--state"])
        if res.ok:
            console.print(f"[green]✓[/green] {fw} — active: {res.stdout[:80]}")
            fw_active = True
            break
    if not fw_active:
        res = run(["iptables", "-L", "-n"], sudo=True, timeout=10)
        if "policy ACCEPT" in res.stdout and not res.stdout.split("\n")[3:5]:
            console.print("[yellow]⚠[/yellow] iptables has default ACCEPT and few/no rules")
            findings.append(("Firewall", "high", "no firewall is configured (default ACCEPT)"))
        else:
            console.print("[green]✓[/green] iptables rules present")
            fw_active = True
    if not fw_active:
        console.print("[red]✗[/red] no firewall detected")
        findings.append(("Firewall", "high", "no firewall detected"))
    return findings


@click.group("sec")
@click.pass_context
def sec(ctx: click.Context) -> None:
    """Security audit: ports, SSH hardening, SUID, kernel hardness, firewall."""
    pass


@sec.command("audit")
@click.option("--no-suid", is_flag=True, help="Skip SUID scan (fast).")
@click.pass_context
def _sec_audit(ctx: click.Context, no_suid: bool) -> None:
    """Full security audit of the host."""
    console = ctx.obj.console
    console.print(Panel.fit("[bold cyan]lx security audit[/bold cyan]", border_style="cyan"))
    findings: list[tuple[str, str, str]] = []
    findings += _ssh_audit(console)
    findings += _ports_audit(console)
    findings += _firewall_audit(console)
    findings += _sudoers_audit(console)
    findings += _kernel_params(console)
    if not no_suid:
        findings += _suid_audit(console)

    if findings:
        center_rule(console, "Findings")
        t = Table(show_header=True, header_style="bold cyan")
        t.add_column("Area")
        t.add_column("Severity")
        t.add_column("Issue")
        for area, sev, issue in sorted(findings, key=lambda f: -_severity_score(f[1])):
            color = {"critical": "red", "high": "red", "medium": "yellow", "low": "blue", "info": "dim"}[sev]
            t.add_row(area, f"[{color}]{sev}[/{color}]", issue)
        console.print(t)
    else:
        ok(console, "no issues detected — looks tidy!")


@sec.command("ssh")
@click.pass_context
def _sec_ssh(ctx: click.Context) -> None:
    """Show only SSH configuration."""
    console = ctx.obj.console
    findings = _ssh_audit(console)
    if findings:
        console.print()
        center_rule(console, "SSH findings")
        for _area, sev, issue in findings:
            color = "red" if sev in ("critical", "high") else ("yellow" if sev == "medium" else "dim")
            console.print(f"  [{color}][{sev}][/{color}] {issue}")


@sec.command("ports")
@click.pass_context
def _sec_ports(ctx: click.Context) -> None:
    """Show only listening ports."""
    _ports_audit(ctx.obj.console)


@sec.command("suid")
@click.pass_context
def _sec_suid(ctx: click.Context) -> None:
    """Scan SUID/SGID binaries only."""
    _suid_audit(ctx.obj.console)


@sec.command("hardssh")
@click.pass_context
def _sec_hardssh(ctx: click.Context) -> None:
    """Apply recommended SSH hardening (writes a drop-in conf). Requires root."""
    console = ctx.obj.console
    drop_in = Path("/etc/ssh/sshd_config.d/99-lx-hardening.conf")
    try:
        drop_in.parent.mkdir(parents=True, exist_ok=True)
        with drop_in.open("w") as fh:
            rules = {
                "PermitRootLogin": "no",
                "PasswordAuthentication": "no",
                "PermitEmptyPasswords": "no",
                "X11Forwarding": "no",
                "MaxAuthTries": "3",
                "ClientAliveInterval": "300",
                "ClientAliveCountMax": "2",
            }
            for k, v in rules.items():
                fh.write(f"{k} {v}\n")
    except OSError as exc:
        err(console, f"cannot write {drop_in}: {exc} (retry with sudo)")
        raise click.exceptions.Exit(1) from exc
    ok(console, f"wrote {len(rules)} hardening rules to {drop_in}")
    warn(console, "verify ssh before restarting: sudo sshd -t && sudo systemctl restart sshd")
    warn(console, "make sure you have key auth working, else you may lock yourself out!")
