"""lx sec — security audit: open ports, sudoers, SSH config, SUID files."""

from __future__ import annotations

import os
import socket
import stat
import time
from pathlib import Path

import click
import psutil
from rich.panel import Panel
from rich.table import Table

from lx.utils.flags import apply_flags, json_option, json_watch_options
from lx.utils.output import center_rule, emit, err, ok, warn
from lx.utils.parse import parse_apt_simulate, parse_authorized_keys, parse_passwd
from lx.utils.shell import run

_WORLD_WRITABLE_ROOTS = ("/etc", "/usr", "/var", "/opt")
_WORLD_WRITABLE_CAP = 200


def _severity_score(severity: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}.get(severity, 0)


# ---------------------------------------------------------------- SSH


def _collect_ssh(ssh_dir: Path | None = None) -> dict:
    findings: list[tuple[str, str, str]] = []
    ssh_dir = ssh_dir or Path("/etc/ssh")
    cfg_path = ssh_dir / "sshd_config"
    cfg_dropins = ssh_dir / "sshd_config.d"
    content = ""
    if cfg_path.is_file():
        try:
            content += cfg_path.read_text(errors="ignore")
        except OSError:
            pass
    if cfg_dropins.is_dir():
        for p in cfg_dropins.glob("*.conf"):
            try:
                content += "\n" + p.read_text(errors="ignore")
            except OSError:
                pass

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

    keys = (
        "Port",
        "PermitRootLogin",
        "PasswordAuthentication",
        "PermitEmptyPasswords",
        "PubkeyAuthentication",
        "X11Forwarding",
        "AllowUsers",
        "MaxAuthTries",
    )
    return {
        "config": {k: get_value(k) for k in keys},
        "config_present": bool(content),
        "port": port,
        "findings": findings,
    }


def _render_ssh(console, data: dict) -> None:
    center_rule(console, "SSH Configuration")
    if not data["config_present"]:
        console.print(
            "[dim]ssh config not found — defaults apply (root login + password auth OPEN)[/dim]"
        )
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Setting")
    t.add_column("Value")
    for k, v in data["config"].items():
        if v or k in ("Port", "PermitRootLogin", "PasswordAuthentication"):
            color = ""
            if k == "PermitRootLogin" and v.lower() == "yes":
                color = "red"
            elif k == "PasswordAuthentication" and v.lower() == "yes":
                color = "yellow"
            t.add_row(k, f"[{color}]{v or 'unset'}[/{color}]" if color else (v or "unset"))
    console.print(t)


# ---------------------------------------------------------------- Ports


def _collect_ports() -> dict:
    findings: list[tuple[str, str, str]] = []
    listening = []
    wildcards = 0
    for c in psutil.net_connections(kind="inet"):
        if c.status != psutil.CONN_LISTEN or not c.laddr:
            continue
        if c.laddr.ip == "0.0.0.0" or c.laddr.ip == "::":
            wildcards += 1
        try:
            name = psutil.Process(c.pid).name() if c.pid else None
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            name = None
        listening.append(
            {
                "proto": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                "local": f"{c.laddr.ip}:{c.laddr.port}",
                "pid": c.pid,
                "process": name,
            }
        )
    if wildcards:
        findings.append(("Ports", "medium", f"{wildcards} socket(s) bound to all interfaces"))
    return {"listening": listening, "wildcards": wildcards, "findings": findings}


def _render_ports(console, data: dict) -> None:
    center_rule(console, "Listening Sockets")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Proto")
    t.add_column("Address")
    t.add_column("PID")
    t.add_column("Process")
    for row in data["listening"]:
        t.add_row(row["proto"], row["local"], str(row["pid"] or "—"), row["process"] or "—")
    console.print(t)


# ---------------------------------------------------------------- Sudoers


def _collect_sudoers() -> dict:
    findings: list[tuple[str, str, str]] = []
    users: list[str] = []
    try:
        groups = Path("/etc/group").read_text() if Path("/etc/group").exists() else ""
    except OSError:
        groups = ""
    for line in groups.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] in ("sudo", "wheel"):
            members = [m for m in parts[3].split(",") if m]
            users.extend(members)
    users = sorted(set(users))
    if len(users) > 5:
        findings.append(("Sudo", "low", f"unusually many ({len(users)}) sudoers"))
    return {"users": users, "findings": findings}


def _render_sudoers(console, data: dict) -> None:
    if data["users"]:
        console.print(
            f"[bold]Users in sudo/wheel group:[/bold] {', '.join(data['users']) or 'none'}"
        )
    else:
        console.print("[dim]could not enumerate sudoers (try running as root)[/dim]")


# ---------------------------------------------------------------- SUID


def _collect_suid(roots: list[Path] | None = None) -> dict:
    findings: list[tuple[str, str, str]] = []
    hits: list[tuple[str, str]] = []
    roots = roots or [Path(r) for r in ("/usr", "/bin", "/sbin", "/opt") if Path(r).is_dir()]
    for root in roots:
        try:
            iterator = root.rglob("*")
            for path in iterator:
                try:
                    st = path.stat()
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode) and (
                    st.st_mode & stat.S_ISUID or st.st_mode & stat.S_ISGID
                ):
                    hits.append((str(path), "suid" if st.st_mode & stat.S_ISUID else "sgid"))
        except OSError:
            continue
    weird = [p for p, _ in hits if p.startswith("/usr/local")]
    hits.sort(key=lambda x: x[0])
    if weird:
        findings.append(
            ("SUID", "medium", f"{len(weird)} SUID files in non-standard locations (/usr/local/*)")
        )
    return {"hits": hits, "count": len(hits), "findings": findings}


def _render_suid(console, data: dict) -> None:
    hits = data["hits"]
    for path, kind in hits[:25]:
        console.print(f"  [{kind}] {path}")
    if len(hits) > 25:
        console.print(f"[dim]…{len(hits) - 25} more[/dim]")


# ---------------------------------------------------------------- Kernel


def _collect_kernel() -> dict:
    findings: list[tuple[str, str, str]] = []
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
    params: dict[str, str] = {}
    for k in keys:
        res = run(["sysctl", "-n", k], timeout=5)
        v = res.stdout or "—"
        params[k] = v
        if k == "kernel.randomize_va_space" and v != "2":
            findings.append(("Kernel", "high", f"{k} = {v} (should be 2)"))
        elif k == "net.ipv4.conf.all.accept_redirects" and v == "1":
            findings.append(("Kernel", "medium", f"{k} enabled (should be 0)"))
        elif k == "fs.suid_dumpable" and v != "0":
            findings.append(("Kernel", "low", f"{k} = {v} (should be 0)"))
    return {"params": params, "findings": findings}


def _render_kernel(console, data: dict) -> None:
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Key")
    t.add_column("Value")
    for k, v in data["params"].items():
        color = ""
        if k == "kernel.randomize_va_space" and v != "2":
            color = "red"
        elif (
            k == "net.ipv4.conf.all.accept_redirects"
            and v == "1"
            or k == "fs.suid_dumpable"
            and v != "0"
        ):
            color = "yellow"
        t.add_row(k, f"[{color}]{v}[/{color}]" if color else v)
    console.print(t)


# ---------------------------------------------------------------- Firewall


def _collect_firewall() -> dict:
    findings: list[tuple[str, str, str]] = []
    fw_active = False
    backend = None
    detail = ""
    for fw in ("ufw", "firewalld", "iptables"):
        res = run([fw, "status"] if fw != "firewalld" else ["firewall-cmd", "--state"])
        if res.ok:
            fw_active = True
            backend = fw
            detail = res.stdout[:80]
            break
    if not fw_active:
        res = run(["iptables", "-L", "-n"], sudo=True, timeout=10)
        if "policy ACCEPT" in res.stdout and not res.stdout.split("\n")[3:5]:
            detail = "iptables has default ACCEPT and few/no rules"
            findings.append(("Firewall", "high", "no firewall is configured (default ACCEPT)"))
        else:
            fw_active = True
            backend = "iptables"
            detail = "rules present"
    if not fw_active:
        detail = "none detected"
        findings.append(("Firewall", "high", "no firewall detected"))
    return {"active": fw_active, "backend": backend, "detail": detail, "findings": findings}


def _render_firewall(console, data: dict) -> None:
    if data["active"]:
        console.print(f"[green]✓[/green] {data['backend']} — {data['detail']}")
    else:
        console.print("[red]✗[/red] no firewall detected")


# ---------------------------------------------------------------- Command


def _collect_audit(no_suid: bool) -> dict:
    sections: dict[str, dict] = {
        "ssh": _collect_ssh(),
        "ports": _collect_ports(),
        "firewall": _collect_firewall(),
        "sudoers": _collect_sudoers(),
        "kernel": _collect_kernel(),
    }
    if not no_suid:
        sections["suid"] = _collect_suid()
    findings: list[tuple[str, str, str]] = []
    for section in sections.values():
        findings += section["findings"]
    summary: dict[str, int] = {}
    for _area, sev, _issue in findings:
        summary[sev] = summary.get(sev, 0) + 1
    return {"findings": findings, "summary": summary, "sections": sections}


def _render_findings(console, findings: list[tuple[str, str, str]]) -> None:
    if not findings:
        ok(console, "no issues detected — looks tidy!")
        return
    center_rule(console, "Findings")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Area")
    t.add_column("Severity")
    t.add_column("Issue")
    for area, sev, issue in sorted(findings, key=lambda f: -_severity_score(f[1])):
        color = {
            "critical": "red",
            "high": "red",
            "medium": "yellow",
            "low": "blue",
            "info": "dim",
        }[sev]
        t.add_row(area, f"[{color}]{sev}[/{color}]", issue)
    console.print(t)


@click.group("sec")
@click.pass_context
def sec(ctx: click.Context) -> None:
    """Security audit: ports, SSH hardening, SUID, kernel hardness, firewall."""
    pass


@sec.command("audit")
@click.option("--no-suid", is_flag=True, help="Skip SUID scan (fast).")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "md"]),
    default="text",
    help="Output format: rich text (default) or Markdown report.",
)
@json_option
@click.pass_context
def _sec_audit(ctx: click.Context, no_suid: bool, fmt: str, json_mode: bool | None = None) -> None:
    """Full security audit of the host."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    console.print(Panel.fit("[bold cyan]lx security audit[/bold cyan]", border_style="cyan"))
    data = _collect_audit(no_suid)
    if emit(ctx, data, command="sec audit"):
        return
    if fmt == "md":
        _render_md(console, data)
        return
    sections = data["sections"]
    _render_ssh(console, sections["ssh"])
    _render_ports(console, sections["ports"])
    _render_firewall(console, sections["firewall"])
    _render_sudoers(console, sections["sudoers"])
    center_rule(console, "Kernel security params")
    _render_kernel(console, sections["kernel"])
    if not no_suid:
        center_rule(console, "SUID/SGID binaries (scanning /usr, /bin, /sbin)")
        _render_suid(console, sections["suid"])
    _render_findings(console, data["findings"])


@sec.command("ssh")
@json_watch_options
@click.pass_context
def _sec_ssh(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show only SSH configuration."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_ssh()
    if emit(ctx, data, command="sec ssh"):
        return
    _render_ssh(console, data)
    if data["findings"]:
        console.print()
        center_rule(console, "SSH findings")
        for _area, sev, issue in data["findings"]:
            color = (
                "red" if sev in ("critical", "high") else ("yellow" if sev == "medium" else "dim")
            )
            console.print(f"  [{color}][{sev}][/{color}] {issue}")


@sec.command("ports")
@json_watch_options
@click.pass_context
def _sec_ports(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show only listening ports."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_ports()
    if emit(ctx, data, command="sec ports"):
        return
    _render_ports(console, data)


@sec.command("suid")
@json_option
@click.pass_context
def _sec_suid(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Scan SUID/SGID binaries only."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    center_rule(console, "SUID/SGID binaries (scanning /usr, /bin, /sbin)")
    data = _collect_suid()
    if emit(ctx, data, command="sec suid"):
        return
    _render_suid(console, data)


@sec.command("hardssh")
@json_option
@click.pass_context
def _sec_hardssh(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Apply recommended SSH hardening (writes a drop-in conf). Requires root."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    drop_in = Path("/etc/ssh/sshd_config.d/99-lx-hardening.conf")
    rules = {
        "PermitRootLogin": "no",
        "PasswordAuthentication": "no",
        "PermitEmptyPasswords": "no",
        "X11Forwarding": "no",
        "MaxAuthTries": "3",
        "ClientAliveInterval": "300",
        "ClientAliveCountMax": "2",
    }
    try:
        drop_in.parent.mkdir(parents=True, exist_ok=True)
        with drop_in.open("w") as fh:
            for k, v in rules.items():
                fh.write(f"{k} {v}\n")
    except OSError as exc:
        err(console, f"cannot write {drop_in}: {exc} (retry with sudo)")
        raise click.exceptions.Exit(1) from exc
    if emit(ctx, {"ok": True, "file": str(drop_in), "rules": rules}, command="sec hardssh"):
        return
    ok(console, f"wrote {len(rules)} hardening rules to {drop_in}")
    warn(console, "verify ssh before restarting: sudo sshd -t && sudo systemctl restart sshd")
    warn(console, "make sure you have key auth working, else you may lock yourself out!")


# ---------------------------------------------------------------- Users


def _collect_users(passwd_text: str | None = None, group_text: str | None = None) -> dict:
    if passwd_text is None:
        passwd_text = Path("/etc/passwd").read_text() if Path("/etc/passwd").exists() else ""
    if group_text is None:
        group_text = Path("/etc/group").read_text() if Path("/etc/group").exists() else ""
    passwd = parse_passwd(passwd_text)
    sudo_members: list[str] = []
    for line in group_text.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] in ("sudo", "wheel"):
            sudo_members.extend(m for m in parts[3].split(",") if m)
    sudo_members = sorted(set(sudo_members))
    login_users = [u for u in passwd if u["is_login_shell"]]
    return {
        "users": passwd,
        "login_users": login_users,
        "sudo_members": sudo_members,
        "count": len(passwd),
        "login_count": len(login_users),
    }


def _render_users(console, data: dict) -> None:
    center_rule(console, f"System users ({data['count']}, {data['login_count']} with login shells)")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("User")
    t.add_column("UID")
    t.add_column("GID")
    t.add_column("Shell")
    t.add_column("Home")
    for u in data["users"][:40]:
        t.add_row(
            u["name"],
            str(u["uid"]),
            str(u["gid"]),
            u["shell"],
            u["home"],
        )
    console.print(t)
    if data["count"] > 40:
        console.print(f"[dim]…{data['count'] - 40} more (use --json for all)[/dim]")
    if data["sudo_members"]:
        console.print(f"[bold]sudo/wheel:[/bold] {', '.join(data['sudo_members'])}")


# ---------------------------------------------------------------- Updates


def _security_apt_packages() -> set[str]:
    """Package names present in security-suite apt lists (best-effort)."""
    import gzip
    import re as _re

    found: set[str] = set()
    lists = Path("/var/lib/apt/lists")
    if not lists.is_dir():
        return found
    for p in lists.iterdir():
        if "security" not in p.name or "Packages" not in p.name:
            continue
        try:
            if p.name.endswith(".gz"):
                text = gzip.decompress(p.read_bytes()).decode(errors="ignore")
            else:
                text = p.read_text(errors="ignore")
        except (OSError, EOFError):
            continue
        found.update(_re.findall(r"^Package:\s*(\S+)", text, flags=_re.M))
    return found


def _collect_updates() -> dict:
    be = None
    import shutil

    from lx.commands.pkg import BACKENDS

    for b in BACKENDS:
        if shutil.which(b):
            be = b
            break
    if not be:
        return {
            "backend": None,
            "ok": False,
            "error": "no supported package manager detected",
            "pending": 0,
            "security": 0,
            "packages": [],
        }
    security = 0
    packages: list[dict] = []
    if be == "apt":
        res = run(["apt-get", "-s", "upgrade"], timeout=120)
        parsed = parse_apt_simulate(res.stdout)
        sec_names = _security_apt_packages()
        for pkg in parsed["packages"]["upgraded"]:
            is_sec = pkg in sec_names
            security += 1 if is_sec else 0
            packages.append({"name": pkg, "old": None, "new": None, "security": is_sec})
    elif be == "dnf":
        res = run(["dnf", "check-update", "--security"], timeout=120)
        for line in res.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and "." in fields[0]:
                security += 1
                packages.append({"name": fields[0], "old": None, "new": None, "security": True})
    elif be == "pacman" or be == "zypper":
        from lx.commands.pkg import _collect_dryrun as _dry

        data = _dry()
        for p in data.get("upgradable", []):
            packages.append(
                {"name": p["name"], "old": p.get("old"), "new": p.get("new"), "security": None}
            )
        security = 0
    else:
        return {
            "backend": be,
            "ok": True,
            "error": None,
            "pending": 0,
            "security": None,
            "packages": [],
            "unsupported": f"{be} security-update listing not supported",
        }
    return {
        "backend": be,
        "ok": True,
        "error": None,
        "pending": len(packages),
        "security": security,
        "packages": packages,
    }


def _render_updates(console, data: dict) -> None:
    if data.get("error"):
        err(console, data["error"])
        return
    if data.get("unsupported"):
        warn(console, data["unsupported"])
        return
    center_rule(console, f"Pending updates via {data['backend']} ({data['pending']})")
    if not data["packages"]:
        ok(console, "no pending updates")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("NAME")
    t.add_column("SECURITY")
    for p in data["packages"][:40]:
        sec = "yes" if p["security"] else ("?" if p["security"] is None else "no")
        color = "red" if sec == "yes" else "dim"
        t.add_row(p["name"], f"[{color}]{sec}[/{color}]")
    console.print(t)
    if data["pending"] > 40:
        console.print(f"[dim]…{data['pending'] - 40} more[/dim]")
    if data.get("security"):
        warn(console, f"{data['security']} security update(s) pending — run: sudo lx pkg upgrade")


# ---------------------------------------------------------------- World-writable


def _collect_worldwritable() -> dict:
    findings = []
    for root in _WORLD_WRITABLE_ROOTS:
        if not Path(root).exists():
            continue
        for path in Path(root).rglob("*"):
            try:
                st = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) and not stat.S_ISDIR(st.st_mode):
                continue
            if not (st.st_mode & stat.S_IWOTH):
                continue
            findings.append(
                {
                    "path": str(path),
                    "type": "dir" if stat.S_ISDIR(st.st_mode) else "file",
                    "mode": oct(st.st_mode & 0o7777),
                    "owner": f"{st.st_uid}:{st.st_gid}",
                }
            )
            if len(findings) >= _WORLD_WRITABLE_CAP:
                break
        if len(findings) >= _WORLD_WRITABLE_CAP:
            break
    return {
        "findings": findings,
        "count": len(findings),
        "truncated": len(findings) >= _WORLD_WRITABLE_CAP,
    }


def _render_worldwritable(console, data: dict) -> None:
    center_rule(console, "World-writable files/dirs in system dirs")
    if not data["findings"]:
        ok(console, "no world-writable entries in /etc, /usr, /var, /opt")
        return
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Type")
    t.add_column("Mode")
    t.add_column("Owner")
    t.add_column("Path")
    for f in data["findings"][:40]:
        t.add_row(f["type"], f["mode"], f["owner"], f["path"])
    console.print(t)
    if data["truncated"]:
        console.print(f"[dim]…truncated at {_WORLD_WRITABLE_CAP} (use --json for full list)[/dim]")


# ---------------------------------------------------------------- SSH keys


def _collect_sshkeys(passwd_text: str | None = None, ssh_dir: Path | None = None) -> dict:
    ssh_dir = ssh_dir or Path("/etc/ssh")
    if passwd_text is None:
        try:
            passwd_text = Path("/etc/passwd").read_text() if Path("/etc/passwd").exists() else ""
        except OSError:
            passwd_text = ""
    users = []
    for u in parse_passwd(passwd_text):
        if not u["is_login_shell"] or u["uid"] < 1000:
            continue
        keys_file = Path(u["home"]) / ".ssh" / "authorized_keys"
        entry = {
            "user": u["name"],
            "home": u["home"],
            "count": 0,
            "keys": [],
            "exists": False,
            "world_readable": False,
        }
        if keys_file.exists():
            try:
                st = keys_file.stat()
                entry["exists"] = True
                entry["world_readable"] = bool(st.st_mode & 0o004)
                entry["keys"] = parse_authorized_keys(keys_file.read_text(errors="ignore"))
                entry["count"] = len(entry["keys"])
            except OSError:
                pass
        users.append(entry)
    host_keys = []
    for p in ssh_dir.glob("*_key.pub") if ssh_dir.is_dir() else []:
        try:
            for k in parse_authorized_keys(p.read_text(errors="ignore")):
                host_keys.append({"type": k["type"], "comment": k["comment"]})
        except OSError:
            continue
    return {"users": users, "host_keys": host_keys}


def _render_sshkeys(console, data: dict) -> None:
    center_rule(console, "User authorized_keys")
    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("User")
    t.add_column("Keys")
    t.add_column("World-readable")
    for u in data["users"]:
        warn_flag = "yes" if u["world_readable"] else "no"
        color = "red" if u["world_readable"] else "dim"
        t.add_row(u["user"], str(u["count"]), f"[{color}]{warn_flag}[/{color}]")
    console.print(t)
    if any(u["world_readable"] for u in data["users"]):
        warn(console, "world-readable authorized_keys files should be chmod 600")
    center_rule(console, "Host keys")
    for k in data["host_keys"]:
        console.print(f"  [dim]{k['type']}[/dim] {k['comment'] or '—'}")


# ---------------------------------------------------------------- audit md format


def _render_md(console, data: dict) -> None:
    from io import StringIO

    buf = StringIO()
    buf.write("# lx security audit\n\n")
    buf.write(f"- Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    buf.write(f"- Host: {os.uname().nodename}\n")
    buf.write(
        f"- Summary: {', '.join(f'{sev} {n}' for sev, n in sorted(data['summary'].items())) or 'no issues'}\n\n"
    )
    sections = data["sections"]
    if data["findings"]:
        buf.write("## Findings\n\n")
        buf.write("| Area | Severity | Issue |\n|---|---|---|\n")
        for area, sev, issue in sorted(data["findings"], key=lambda f: -_severity_score(f[1])):
            buf.write(f"| {area} | {sev} | {issue} |\n")
        buf.write("\n")
    if sections.get("ssh") and sections["ssh"].get("config_present"):
        buf.write("## SSH\n\n")
        buf.write("| Setting | Value |\n|---|---|\n")
        for k, v in sections["ssh"]["config"].items():
            buf.write(f"| {k} | {v or 'unset'} |\n")
        buf.write("\n")
    ports = sections.get("ports", {})
    if ports.get("listening"):
        buf.write("## Listening ports\n\n")
        buf.write("| Proto | Address | PID | Process |\n|---|---|---|---|\n")
        for row in ports["listening"]:
            buf.write(
                f"| {row['proto']} | {row['local']} | {row['pid'] or '—'} | {row['process'] or '—'} |\n"
            )
        buf.write("\n")
    if sections.get("sudoers", {}).get("users"):
        buf.write(f"## Sudoers\n\n{', '.join(sections['sudoers']['users'])}\n\n")
    suid = sections.get("suid", {})
    if suid.get("hits"):
        buf.write(f"## SUID/SGID ({suid['count']})\n\n")
        for path, kind in suid["hits"][:100]:
            buf.write(f"- [{kind}] {path}\n")
        buf.write("\n")
    console.print(buf.getvalue())


@sec.command("users")
@json_watch_options
@click.pass_context
def _sec_users(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """List system users and sudo/wheel membership."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_users()
    if emit(ctx, data, command="sec users"):
        return
    _render_users(console, data)


@sec.command("updates")
@json_watch_options
@click.pass_context
def _sec_updates(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Show pending package updates, flagging security ones."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_updates()
    if emit(ctx, data, command="sec updates"):
        return
    _render_updates(console, data)


@sec.command("worldwritable")
@json_option
@click.pass_context
def _sec_worldwritable(ctx: click.Context, json_mode: bool | None = None) -> None:
    """Scan system dirs for world-writable files/dirs."""
    apply_flags(ctx, json_mode)
    console = ctx.obj.console
    center_rule(console, "Scanning /etc /usr /var /opt for world-writable entries…")
    data = _collect_worldwritable()
    if emit(ctx, data, command="sec worldwritable"):
        return
    _render_worldwritable(console, data)


@sec.command("sshkeys")
@json_watch_options
@click.pass_context
def _sec_sshkeys(
    ctx: click.Context, json_mode: bool | None = None, watch_secs: float | None = None
) -> None:
    """Inspect user authorized_keys files and host keys."""
    apply_flags(ctx, json_mode, watch_secs)
    console = ctx.obj.console
    data = _collect_sshkeys()
    if emit(ctx, data, command="sec sshkeys"):
        return
    _render_sshkeys(console, data)
