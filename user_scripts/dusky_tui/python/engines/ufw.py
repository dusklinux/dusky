#!/usr/bin/env python3
"""UFW CLI engine for Linux, with serialized writes and owned framework blocks.

Rule and socket reports describe UFW configuration, not end-to-end network
reachability. Domains are resolved IP rules, and default policies retain
existing user/framework allowances and established sessions.
"""

from __future__ import annotations

import os
import re
lazy import json
lazy import socket
import sys
lazy import ipaddress
lazy import shlex
lazy import stat
lazy from datetime import datetime, timezone
lazy from functools import wraps
import logging
lazy import tempfile
lazy import threading
lazy import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Any
lazy from concurrent.futures import ThreadPoolExecutor

from python.frontend.core_types import BaseEngine

logger = logging.getLogger("dusky_ufw_engine")

# Type Aliases (PEP 695)
type RuleDict = dict[str, Any]
type AppProfileDict = dict[str, str]

# Configuration & State Paths
UFW_SYSCTL_CONF = Path("/etc/ufw/sysctl.conf")
UFW_BEFORE_RULES = Path("/etc/ufw/before.rules")
UFW_AFTER_RULES = Path("/etc/ufw/after.rules")
UFW_AFTER6_RULES = Path("/etc/ufw/after6.rules")
UFW_BEFORE6_RULES = Path("/etc/ufw/before6.rules")
UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_AFTER_INIT = Path("/etc/ufw/after.init")
DOMAINS_STORAGE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dusky/settings/firewall/domains.json"

CMD_TIMEOUT_READ = 10
CMD_TIMEOUT_WRITE = 30

COMMON_SERVICES: dict[str, dict[str, str]] = {
    "ssh": {"name": "OpenSSH Server", "port": "22", "proto": "tcp", "comment": "OpenSSH"},
    "http": {"name": "Web HTTP", "port": "80", "proto": "tcp", "comment": "Web HTTP"},
    "https": {"name": "Web HTTPS", "port": "443", "proto": "tcp", "comment": "Web HTTPS"},
    "ftp": {"name": "FTP Control", "port": "21", "proto": "tcp", "comment": "FTP Control"},
    "dns": {"name": "DNS Server", "port": "53", "proto": "both", "comment": "DNS Service"},
    "wireguard": {"name": "WireGuard VPN", "port": "51820", "proto": "udp", "comment": "WireGuard VPN"},
    "tailscale": {"name": "Tailscale Direct P2P", "port": "41641", "proto": "udp", "comment": "Tailscale Direct P2P"},
    "moonlight": {"name": "Moonlight Game Streaming", "port": "47984,47989,48010", "proto": "tcp", "comment": "Moonlight Display"},
    "plex": {"name": "Plex Media Server", "port": "32400", "proto": "tcp", "comment": "Plex Media"},
    "minecraft": {"name": "Minecraft Server", "port": "25565", "proto": "tcp", "comment": "Minecraft Server"},
    "samba": {"name": "Samba File Sharing", "port": "445", "proto": "tcp", "comment": "Samba Share"},
    "vnc": {"name": "VNC Display", "port": "5901", "proto": "tcp", "comment": "VNC Display"},
    "syncthing": {"name": "Syncthing Transfer", "port": "22000", "proto": "tcp", "comment": "Syncthing"},
    "torrent": {"name": "BitTorrent Peer", "port": "6881", "proto": "both", "comment": "BitTorrent"},
}


@dataclass(slots=True, kw_only=True)
class RuleRecord:
    number: int
    to_addr: str
    action: str
    from_addr: str
    comment: str = ""
    is_v6: bool = False
    raw: str = ""

    def to_dict(self) -> RuleDict:
        return {
            "number": self.number,
            "to": self.to_addr,
            "action": self.action,
            "from": self.from_addr,
            "comment": self.comment,
            "is_v6": self.is_v6,
            "raw": self.raw,
        }


class UfwError(RuntimeError):
    """An operation failed; preceding steps may already have applied."""


def operation(method):
    """Serialize mutations and report failures at the outer API boundary."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            outer = self._operation_depth == 0
            self._operation_depth += 1
            try:
                result = method(self, *args, **kwargs)
                if not result[0] and not outer:
                    raise UfwError(result[1])
                return result
            except (UfwError, OSError, ValueError, subprocess.SubprocessError) as exc:
                if not outer:
                    raise
                logger.error("%s: %s", method.__name__, exc)
                message = f"Operation failed (earlier steps may have applied): {exc}"
                return (False, message, "") if method.__name__ == "write_value" else (False, message)
            finally:
                self._operation_depth -= 1
    return wrapped


class UfwEngine(BaseEngine):
    """
    Comprehensive, high-performance engine for managing UFW firewall.
    Integrates directly with Dusky TUI architecture.
    """
    _instance: UfwEngine | None = None

    def __init__(self, config_path: str = "/etc/default/ufw"):
        UfwEngine._instance = self
        self.config_path = Path(config_path).expanduser().resolve()
        self._lock = threading.RLock()
        self._operation_depth = 0
        self.cache: dict[str, Any] = {}
        self.app: Any = None

    def set_app(self, app: Any) -> None:
        self.app = app

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    # =========================================================================
    # 1. COMMAND EXECUTION & PRIVILEGE WRAPPER
    # =========================================================================
    @staticmethod
    def _cmd_prefix() -> list[str]:
        return [] if os.geteuid() == 0 else ["sudo", "-n"]

    def _run_cmd(self, cmd: list[str], timeout: int | None = None, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        read = cmd[0] in {"ss", "ip"} or (cmd[0] == "ufw" and cmd[1] in {"status", "show", "app"})
        if timeout is None:
            timeout = CMD_TIMEOUT_READ if read else CMD_TIMEOUT_WRITE
        full_cmd = self._cmd_prefix() + cmd
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL, timeout=timeout,
                                    env={**os.environ, "LC_ALL": "C"})
        except subprocess.TimeoutExpired as exc:
            raise UfwError(f"{shlex.join(cmd)} timed out after {timeout}s; check firewall state before retrying") from exc
        if check and result.returncode:
            raise UfwError(f"{shlex.join(cmd)}: {result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'}")
        return result

    @staticmethod
    def _atomic_write(path: Path, content: str, *, mode: int = 0o644) -> None:
        """Replace complete files while preserving existing mode and ownership."""
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.stat() if path.exists() else None
        name = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
                name = tmp.name
                os.fchmod(tmp.fileno(), stat.S_IMODE(existing.st_mode) if existing else mode)
                if existing and os.geteuid() == 0:
                    os.fchown(tmp.fileno(), existing.st_uid, existing.st_gid)
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(name, path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _ports(port: str) -> tuple[tuple[int, int], ...]:
        """UFW numeric multiport syntax: at most 15 slots, two per range."""
        intervals = []
        slots = 0
        for part in port.split(","):
            if not re.fullmatch(r"[0-9]+(?::[0-9]+)?", part):
                raise ValueError("Use numeric ports, comma lists, or colon ranges without spaces.")
            values = [int(v) for v in part.split(":")]
            low, high = values[0], values[-1]
            if not 1 <= low <= high <= 65535:
                raise ValueError("Ports must be 1–65535 with ascending ranges.")
            slots += len(values)
            intervals.append((low, high))
        if slots > 15:
            raise ValueError("UFW allows at most 15 port slots; each range counts as two.")
        return tuple(intervals)

    @staticmethod
    def _protocols(proto: str) -> list[str]:
        if proto not in {"tcp", "udp", "both"}:
            raise ValueError("Choose tcp, udp, or both.")
        return ["tcp", "udp"] if proto == "both" else [proto]

    @staticmethod
    def _address(value: str) -> str:
        return "any" if value == "any" else str(ipaddress.ip_network(value, strict=False))

    @staticmethod
    def _port_field(value: str) -> tuple[str, str, str] | None:
        """Parse a numeric destination field, never numbers inside an IP address."""
        value = value.replace(" (v6)", "")
        match = re.fullmatch(r"(?:(?P<addr>[^ ]+) )?(?P<ports>[0-9,:]+)(?:/(?P<proto>tcp|udp))?", value)
        if not match:
            return None
        return match['addr'] or "Anywhere", match['ports'], match['proto'] or "both"

    @classmethod
    def _rule_has_port(cls, rule: RuleRecord, port: int, proto: str) -> bool:
        field = cls._port_field(rule.to_addr.split(" on ", 1)[0])
        return bool(field and field[2] in {proto, "both"} and
                    any(lo <= port <= hi for lo, hi in cls._ports(field[1])))

    def _stored_rule_commands(self) -> list[list[str]]:
        report = self._run_cmd(["ufw", "show", "added"])
        return [shlex.split(line)[1:] for line in report.stdout.splitlines() if line.startswith("ufw ")]

    def _delete_tagged(self, *, tags: set[str] | None = None, prefixes: tuple[str, ...] = ()) -> int:
        """Delete exact rule signatures, including when UFW is inactive.

        UFW's show-added CLI is the documented replayable rule representation.
        Deletion by signature removes both families without unstable indices.
        """
        count = 0
        for args in self._stored_rule_commands():
            if "comment" not in args:
                continue
            comment = args[args.index("comment") + 1]
            if comment not in (tags or set()) and not (prefixes and comment.startswith(prefixes)):
                continue
            delete = ["ufw", "--force"]
            if args[0] == "route":
                delete += ["route", "delete", *args[1:]]
            else:
                delete += ["delete", *args]
            self._run_cmd(delete)
            count += 1
        return count

    def _read_domain_registry(self) -> dict[str, Any]:
        if not DOMAINS_STORAGE.exists():
            return {"whitelist_mode": False, "domains": []}
        try:
            data = json.loads(DOMAINS_STORAGE.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("domains"), list):
                raise ValueError("Expected a domain registry object with a domains list")
            if not isinstance(data.get("whitelist_mode", False), bool):
                raise ValueError("whitelist_mode must be a boolean")
            for entry in data["domains"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("domain"), str):
                    raise ValueError("Each domain entry needs a domain name")
                if entry.get("action", "allow") not in {"allow", "deny"}:
                    raise ValueError("Invalid domain action")
                ports = entry.get("ports", "80,443")
                if not isinstance(ports, str):
                    raise ValueError("Domain ports must be a string")
                if ports != "any":
                    self._ports(ports)
                if not isinstance(entry.get("ips", []), list):
                    raise ValueError("Domain IPs must be a list")
                for ip in entry.get("ips", []):
                    if not isinstance(ip, str):
                        raise ValueError("Domain IPs must be strings")
                    ipaddress.ip_address(ip)
            return data
        except (ValueError, TypeError) as exc:
            raise UfwError(f"Invalid domain registry {DOMAINS_STORAGE}: {exc}") from exc

    def _write_domain_registry(self, data: dict[str, Any]) -> bool:
        self._atomic_write(DOMAINS_STORAGE, json.dumps(data, indent=4) + "\n", mode=0o600)
        return True

    # =========================================================================
    # 2. STATUS & RULES PARSING
    # =========================================================================
    def get_status_verbose(self) -> dict[str, Any]:
        res = self._run_cmd(["ufw", "status", "verbose"])
        lines = res.stdout.splitlines()
        def settings(path):
            if not path.exists():
                return {}
            return dict(re.findall(r'^\s*([A-Z_]+)=["\']?([^"\'\n#]+)', path.read_text(encoding="utf-8"), re.MULTILINE))
        defaults = settings(self.config_path)
        config = settings(UFW_CONF)
        policy = {"ACCEPT": "allow", "DROP": "deny", "REJECT": "reject", "SKIP": "skip"}
        info: dict[str, Any] = {
            "active": False,
            "logging": config.get("LOGLEVEL", "off").strip(),
            "default_incoming": policy.get(defaults.get("DEFAULT_INPUT_POLICY", "DROP").strip(), "deny"),
            "default_outgoing": policy.get(defaults.get("DEFAULT_OUTPUT_POLICY", "ACCEPT").strip(), "allow"),
            "default_routed": policy.get(defaults.get("DEFAULT_FORWARD_POLICY", "DROP").strip(), "deny"),
            "new_profiles": policy.get(defaults.get("DEFAULT_APPLICATION_POLICY", "SKIP").strip(), "skip"),
            "raw": res.stdout,
        }

        for line in lines:
            line = line.strip()
            if line.startswith("Status:"):
                info["active"] = bool(re.search(r"Status:\s*active\b", line, re.IGNORECASE))
            elif line.startswith("Logging:"):
                match = re.search(r"Logging:\s*(?:on\s*\(([a-z]+)\)|([a-z]+))", line, re.IGNORECASE)
                if match:
                    info["logging"] = (match.group(1) or match.group(2) or "off").lower()
            elif line.startswith("Default:"):
                inc_m = re.search(r"([a-z]+)\s*\(incoming\)", line, re.IGNORECASE)
                out_m = re.search(r"([a-z]+)\s*\(outgoing\)", line, re.IGNORECASE)
                rt_m = re.search(r"([a-z]+)\s*\(routed\)", line, re.IGNORECASE)
                if inc_m:
                    info["default_incoming"] = inc_m.group(1).lower()
                if out_m:
                    info["default_outgoing"] = out_m.group(1).lower()
                if rt_m and rt_m.group(1).lower() != "disabled":
                    info["default_routed"] = rt_m.group(1).lower()
                info["routing_disabled"] = bool(rt_m and rt_m.group(1).lower() == "disabled")
            elif line.startswith("New profiles:"):
                match = re.search(r"New profiles:\s*([a-z]+)", line, re.IGNORECASE)
                if match:
                    info["new_profiles"] = match.group(1).lower()

        return info

    def get_numbered_rules(self) -> list[RuleRecord]:
        res = self._run_cmd(["ufw", "status", "numbered"])
        rules: list[RuleRecord] = []
        action_pattern = re.compile(
            r"\b(ALLOW\s+IN|ALLOW\s+OUT|ALLOW\s+FWD|DENY\s+IN|DENY\s+OUT|DENY\s+FWD|"
            r"REJECT\s+IN|REJECT\s+OUT|REJECT\s+FWD|LIMIT\s+IN|LIMIT\s+OUT|LIMIT\s+FWD|"
            r"ALLOW|DENY|REJECT|LIMIT)\b",
            re.IGNORECASE,
        )

        for line in res.stdout.splitlines():
            line_str = line.strip()
            num_match = re.prefixmatch(r"^\[\s*(\d+)\]\s+(.*)$", line_str)
            if not num_match:
                continue

            num = int(num_match.group(1))
            body = num_match.group(2).strip()

            comment = ""
            if "#" in body:
                body_part, comment_part = body.split("#", 1)
                body = body_part.strip()
                comment = comment_part.strip()

            act_match = action_pattern.search(body)
            if act_match:
                to_addr = body[: act_match.start()].strip()
                action = act_match.group(1).strip()
                from_addr = body[act_match.end() :].strip()
            else:
                parts = body.split()
                to_addr = parts[0] if parts else ""
                action = parts[1] if len(parts) > 1 else ""
                from_addr = " ".join(parts[2:]) if len(parts) > 2 else ""

            is_v6 = "(v6)" in to_addr or "(v6)" in from_addr
            rules.append(
                RuleRecord(
                    number=num,
                    to_addr=to_addr,
                    action=action,
                    from_addr=from_addr,
                    comment=comment,
                    is_v6=is_v6,
                    raw=line_str,
                )
            )

        return rules

    # =========================================================================
    # 3. DETAILED PORT INSPECTOR & PROBER (Open/Closed/Filtered)
    # =========================================================================
    def get_detailed_port_map(self) -> list[dict[str, Any]]:
        """Report socket bindings and relevant UI rules, not remote reachability.

        UFW's numbered listing excludes before/after rules, Docker and other
        tables. Conditional rules cannot establish an effective verdict without
        a source address and interface. Never label a socket exposed to the WAN.
        """
        res = self._run_cmd(["ss", "-H", "-tlunp"])
        rules = self.get_numbered_rules()
        status = self.get_status_verbose()
        ports_list = []
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            endpoint = parts[4] if parts[1] in {"LISTEN", "UNCONN"} else parts[3]
            ip, port = endpoint.rsplit(":", 1)
            ip = ip.strip("[]")
            if not port.isdigit():
                continue
            port_num = int(port)
            address = None if ip == "*" else ipaddress.ip_address(ip.split("%", 1)[0])
            proc = re.search(r'users:\(\("([^"\n]+)",pid=(\d+)', line)
            candidates = [r for r in rules if r.action.endswith(" IN") and
                          (address is None or r.is_v6 == (address.version == 6)) and
                          (self._rule_has_port(r, port_num, parts[0]) or
                           r.to_addr.split(" on ", 1)[0].replace(" (v6)", "") == "Anywhere")]
            broad = next((r for r in candidates if r.from_addr.replace(" (v6)", "") == "Anywhere"
                          and " on " not in r.to_addr and
                          (r.to_addr.replace(" (v6)", "") == "Anywhere" or
                           self._port_field(r.to_addr) and self._port_field(r.to_addr)[0] == "Anywhere")), None)
            if address and address.is_loopback:
                fw_status = "PROTECTED"
            elif not status["active"]:
                fw_status = "UFW INACTIVE"
            elif candidates and (address is None or not broad or candidates[0] is not broad):
                fw_status = "CONDITIONAL"
            elif broad:
                fw_status = f"RULE {broad.action.split()[0]}"
            else:
                fw_status = f"DEFAULT {status['default_incoming'].upper()}"
            scope = "Localhost Only" if address and address.is_loopback else (
                "All Interfaces" if ip == "*" or address.is_unspecified else str(address))
            ports_list.append({"port": port_num, "proto": parts[0], "ip": ip,
                               "process": proc[1] if proc else "unknown", "pid": int(proc[2]) if proc else 0,
                               "scope": scope, "fw_status": fw_status,
                               "matched_rule": candidates[0].raw if candidates else ""})
        return sorted(ports_list, key=lambda item: (item["port"], item["proto"], item["ip"]))

    def probe_port(self, port: int, proto: str = "tcp", host: str = "127.0.0.1") -> dict[str, Any]:
        """A loopback connection checks a service, not remote firewall access."""
        port = int(port)
        if not 1 <= port <= 65535 or proto != "tcp":
            raise ValueError("The active probe requires a TCP port from 1 to 65535.")
        reachable = False
        try:
            with socket.create_connection((host, int(port)), timeout=0.4):
                reachable = True
        except OSError:
            pass
        port_map = self.get_detailed_port_map()
        listening = next((p for p in port_map if p["port"] == port and p["proto"] == proto), None)
        status = "LOCAL CONNECT OK" if reachable else ("LISTENING" if listening else "NO LISTENER")
        return {"port": port, "proto": proto, "listening": listening is not None,
                "process": listening["process"] if listening else "none", "reachable": reachable,
                "status": status, "matching_rules": [p["matched_rule"] for p in port_map
                    if p["port"] == port and p["proto"] == proto and p["matched_rule"]],
                "summary": f"TCP {port}: {status}. Remote firewall access is not tested."}

    # =========================================================================
    # 4. QUICK PORT MANAGEMENT (Open / Close / Delete)
    # =========================================================================
    @operation
    def open_port(self, port: str, proto: str = "tcp", scope: str = "any", comment: str = "") -> tuple[bool, str]:
        port = port.strip()
        self._ports(port)
        for protocol in self._protocols(proto):
            sources = ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"] if scope == "lan" else [scope or "any"]
            for source in sources:
                self._address(source)
                cmd = (["ufw", "allow", f"{port}/{protocol}"] if source == "any" else
                       ["ufw", "allow", "from", source, "to", "any", "port", port, "proto", protocol])
                if comment:
                    cmd += ["comment", comment]
                self._run_cmd(cmd)
        return True, f"Allow rules added for {port}/{proto}; earlier rules still take precedence."

    @operation
    def close_port(self, port: str, proto: str = "tcp", action: str = "deny", comment: str = "") -> tuple[bool, str]:
        port = port.strip()
        self._ports(port)
        if action not in {"deny", "reject"}:
            raise ValueError("Close action must be deny or reject.")
        # Prepend a block without deleting unrelated scopes or multiport rules.
        for protocol in self._protocols(proto):
            cmd = ["ufw", "prepend", action, f"{port}/{protocol}"]
            if comment:
                cmd += ["comment", comment]
            self._run_cmd(cmd)
        return True, f"Top-priority ingress {action} rules added for {port}/{proto}; framework rules and established sessions still apply."

    @operation
    def delete_port_rules(self, port: str, proto: str = "tcp") -> tuple[bool, str]:
        wanted = self._ports(port.strip())
        protocols = set(self._protocols(proto))
        removed = 0
        for args in self._stored_rule_commands():
            if args[0] == "route" or "out" in args[:4]:
                continue
            spec = None
            protocol = "both"
            # Full syntax must match a destination port, never a source port.
            if "to" in args:
                at = args.index("to") + 2
                if at < len(args) and args[at] == "port":
                    spec = args[at + 1]
                    if "proto" in args:
                        protocol = args[args.index("proto") + 1]
            else:
                simple = re.fullmatch(r"([0-9,:]+)(?:/(tcp|udp))?", args[1]) if len(args) > 1 else None
                if simple:
                    spec, protocol = simple[1], simple[2] or "both"
            if spec and protocol in protocols and self._ports(spec) == wanted:
                self._run_cmd(["ufw", "--force", "delete", *args])
                removed += 1
        return True, f"Removed {removed} ingress rule signature(s) with exactly {port}/{proto}; mixed-protocol and broader rules retained."

    # =========================================================================
    # 5. COMMON SERVICES
    # =========================================================================
    def is_service_allowed(self, svc_key: str, *, rules: list[RuleRecord] | None = None,
                           commands: list[list[str]] | None = None) -> bool:
        """Whether this switch owns unscoped ingress allow rules for every service port.

        This is rule presence, not an effective policy verdict. Disable removes
        only the rules owned by this switch.
        """
        svc = COMMON_SERVICES.get(svc_key)
        if not svc:
            return False
        if rules is None:
            rules = []
            for args in (self._stored_rule_commands() if commands is None else commands):
                if args[0] != "allow" or len(args) < 2 or "comment" not in args:
                    continue
                # Switches create simple unscoped ingress rules; broader or
                # manually changed signatures do not pin the switch on.
                if not re.fullmatch(r"[0-9,:]+/(?:tcp|udp)", args[1]):
                    continue
                rules.append(RuleRecord(number=0, to_addr=args[1], action="ALLOW IN", from_addr="Anywhere",
                                        comment=args[args.index("comment") + 1]))
        numbered = rules
        specs = [(svc["port"], svc["proto"])]
        if svc_key == "moonlight":
            specs.append(("47998:48000", "udp"))
        return all(any(r.action == "ALLOW IN" and r.comment == f"dusky:service:{svc_key}" and r.from_addr.replace(" (v6)", "") == "Anywhere"
                       and self._port_field(r.to_addr) and self._port_field(r.to_addr)[0] == "Anywhere"
                       and self._rule_has_port(r, port, protocol) for r in numbered)
                   for spec, proto in specs for protocol in self._protocols(proto)
                   for low, high in self._ports(spec) for port in range(low, high + 1))

    @operation
    def toggle_service(self, svc_key: str, enable: bool) -> tuple[bool, str]:
        svc = COMMON_SERVICES.get(svc_key)
        if not svc:
            return False, f"Unknown service: {svc_key}"
        tag = f"dusky:service:{svc_key}"
        if enable:
            self.open_port(svc["port"], proto=svc["proto"], comment=tag)
            if svc_key == "moonlight":
                self.open_port("47998:48000", proto="udp", comment=tag)
            return True, f"Explicit allow rules added for {svc['name']}."
        self._delete_tagged(tags={tag})
        return True, f"Managed {svc['name']} rules removed; other rules and defaults are retained."

    # =========================================================================
    # 6. ACTIVE CONNECTIONS & IP BANNING
    # =========================================================================
    def get_active_connections(self) -> list[dict[str, Any]]:
        res = self._run_cmd(["ss", "-H", "-tunp", "state", "established"])
        conns: list[dict[str, Any]] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            parts = line.split()
            if len(parts) < 5:
                continue

            proto = parts[0]
            if len(parts) >= 6 and parts[1].isalpha() and not parts[1].isdigit():
                local_str = parts[4]
                peer_str = parts[5]
            else:
                local_str = parts[3]
                peer_str = parts[4]

            loc_ip, _, loc_port = local_str.rpartition(":")
            peer_ip, _, peer_port = peer_str.rpartition(":")
            loc_ip = loc_ip.strip("[]")
            peer_ip = peer_ip.strip("[]")

            proc = ""
            pid = ""
            m_proc = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
            if m_proc:
                proc = m_proc.group(1)
                pid = m_proc.group(2)

            conns.append({
                "proto": proto,
                "local_ip": loc_ip,
                "local_port": loc_port,
                "remote_ip": peer_ip,
                "remote_port": peer_port,
                "process": proc or "system",
                "pid": pid,
                "raw": line,
            })

        return conns

    @operation
    def ban_ip(self, ip: str) -> tuple[bool, str]:
        ip = str(ipaddress.ip_address(ip.strip()))
        self._run_cmd(["ufw", "prepend", "deny", "from", ip, "comment", f"Banned: {ip}"])
        return True, f"Ingress deny rule added for {ip}; existing sessions and framework allowances may continue."

    @operation
    def unban_ip(self, ip: str) -> tuple[bool, str]:
        ip = str(ipaddress.ip_address(ip.strip()))
        count = self._delete_tagged(tags={f"Banned: {ip}"})
        return True, f"Removed {count} managed ban rule(s) for {ip}."

    def get_banned_ips(self, *, rules: list[RuleRecord] | None = None) -> list[str]:
        numbered = self.get_numbered_rules() if rules is None else rules
        banned: list[str] = []
        for r in numbered:
            if "DENY" in r.action and r.comment.startswith("Banned:"):
                banned.append(r.from_addr or r.to_addr)
        return list(dict.fromkeys(banned))

    @operation
    def panic_lockdown(self, enable: bool) -> tuple[bool, str]:
        if enable:
            self._run_cmd(["ufw", "default", "deny", "incoming"])
            self._run_cmd(["ufw", "default", "deny", "outgoing"])
            self._run_cmd(["ufw", "default", "deny", "routed"])
            return True, "Default policies set to deny; existing rules, framework allowances and established sessions are retained."
        else:
            self._run_cmd(["ufw", "default", "deny", "incoming"])
            self._run_cmd(["ufw", "default", "allow", "outgoing"])
            self._run_cmd(["ufw", "default", "deny", "routed"])
            return True, "Standard traffic policies restored."

    # =========================================================================
    # 7. ICMP PING STEALTH & PORT FORWARDING
    # =========================================================================
    @staticmethod
    def get_icmp_ping_stealth() -> bool:
        files = [f for f in (UFW_BEFORE_RULES, UFW_BEFORE6_RULES) if f.exists()]
        return bool(files) and all(re.search(r"^-A ufw6?-before-input .*echo-request -j DROP$",
                        f.read_text(encoding="utf-8"), re.MULTILINE) for f in files)

    @operation
    def set_icmp_ping_stealth(self, stealth: bool) -> tuple[bool, str]:
        updates = {}
        for path in (UFW_BEFORE_RULES, UFW_BEFORE6_RULES):
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            updated, count = re.subn(r"(^-A ufw6?-before-input .*echo-request -j )(?:ACCEPT|DROP)$",
                            lambda m: m[1] + ("DROP" if stealth else "ACCEPT"), content, flags=re.MULTILINE)
            if not count:
                raise ValueError(f"No standard input echo-request rule found in {path}")
            updates[path] = updated
        if not updates:
            raise ValueError("No UFW before rules found")
        self._update_framework(updates)
        return True, "Input echo requests " + ("dropped." if stealth else "accepted.")

    def _update_framework(self, updates: dict[Path, str]) -> None:
        """Rollback stored framework files if UFW cannot reload them."""
        originals = {p: p.read_text(encoding="utf-8") if p.exists() else None for p in updates}
        original_modes = {p: stat.S_IMODE(p.stat().st_mode) for p in updates if p.exists()}
        try:
            for path, content in updates.items():
                self._atomic_write(path, content, mode=0o755 if path == UFW_AFTER_INIT else 0o644)
                if path == UFW_AFTER_INIT:
                    path.chmod(path.stat().st_mode | 0o100)
            self._run_cmd(["ufw", "reload"])
        except (OSError, UfwError):
            for path, content in originals.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    self._atomic_write(path, content)
                    path.chmod(original_modes[path])
            try:
                self._run_cmd(["ufw", "reload"])
            except (OSError, UfwError) as exc:
                logger.error("Restored files but reload also failed: %s", exc)
            raise

    def _validate_restore(self, body: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as test_file:
            test_file.write(body + "\n")
            test_file.flush()
            validation = self._run_cmd(["iptables-restore", "--test", "--noflush", test_file.name])
        # The nft backend can exit zero here with a capability warning that
        # becomes a commit error. Check that observed diagnostic explicitly.
        unsupported = re.search(r"Extension (\S+) revision .*not supported", validation.stderr)
        if unsupported:
            raise UfwError(f"{unsupported[1]} kernel extension unavailable: {validation.stderr.strip()}")

    @staticmethod
    def _managed_block(content: str, name: str, body: str) -> str:
        begin, end = f"# BEGIN DUSKY {name}", f"# END DUSKY {name}"
        content = re.sub(rf"^{re.escape(begin)}\n.*?^{re.escape(end)}\n?", "", content,
                         flags=re.MULTILINE | re.DOTALL)
        return content.rstrip() + f"\n\n{begin}\n{body.rstrip()}\n{end}\n"

    @staticmethod
    def _framework_hook() -> str:
        # UFW restores with --noflush. Declared user chains are rebuilt, but
        # builtin NAT jumps would accumulate. Hooks install one jump and remove
        # owned jumps/chains on stop, including after disabling the firewall.
        original = UFW_AFTER_INIT.read_text(encoding="utf-8") if UFW_AFTER_INIT.exists() else "#!/bin/sh\n"
        if not re.prefixmatch(r"^#![^\n]*(?:/sh|/bash|env (?:sh|bash))(?:\s|$)", original):
            raise ValueError("after.init must be a sh/bash script to integrate Dusky hooks")
        begin, end = "# BEGIN DUSKY HOOKS", "# END DUSKY HOOKS"
        original = re.sub(rf"^{begin}\n.*?^{end}\n?", "", original, flags=re.MULTILINE | re.DOTALL)
        body = r"""
# BEGIN DUSKY HOOKS
(
    set -e
    dusky_jump() {
        dusky_cmd=$1 dusky_table=$2 dusky_parent=$3 dusky_chain=$4
        command -v "$dusky_cmd" >/dev/null || return 0
        # Missing chains are normal before the first start and for absent Docker.
        if "$dusky_cmd" -w 5 -t "$dusky_table" -S "$dusky_chain" >/dev/null 2>&1; then
            if "$dusky_cmd" -w 5 -t "$dusky_table" -S "$dusky_parent" >/dev/null 2>&1; then
                while "$dusky_cmd" -w 5 -t "$dusky_table" -C "$dusky_parent" -j "$dusky_chain" 2>/dev/null; do
                    "$dusky_cmd" -w 5 -t "$dusky_table" -D "$dusky_parent" -j "$dusky_chain"
                done
                if [ "$dusky_action" = start ]; then
                    "$dusky_cmd" -w 5 -t "$dusky_table" -I "$dusky_parent" 1 -j "$dusky_chain"
                fi
            fi
            if [ "$dusky_action" != start ]; then
                "$dusky_cmd" -w 5 -t "$dusky_table" -F "$dusky_chain"
                "$dusky_cmd" -w 5 -t "$dusky_table" -X "$dusky_chain"
            fi
        fi
    }
    dusky_action=$1
    case "$dusky_action" in
        start|stop|flush-all)
            dusky_jump iptables nat PREROUTING dusky-dnat
            dusky_jump iptables nat POSTROUTING dusky-waydroid
            dusky_jump iptables filter DOCKER-USER dusky-docker
            dusky_jump ip6tables filter DOCKER-USER dusky-docker
            ;;
    esac
) || exit $?
# END DUSKY HOOKS
"""
        first, _, rest = original.partition("\n")
        return first + "\n" + body.lstrip("\n") + rest

    @staticmethod
    def get_port_forwards() -> list[dict[str, str]]:
        if not UFW_BEFORE_RULES.exists():
            return []
        matches = re.findall(r"^-A (?:dusky-dnat|PREROUTING) -p (tcp|udp) --dport ([0-9]+) -j DNAT --to-destination ([^\s]+)$",
                             UFW_BEFORE_RULES.read_text(encoding="utf-8"), re.MULTILINE)
        return [{"proto": proto, "ext_port": port, "destination": dst} for proto, port, dst in matches]

    @operation
    def add_port_forward(self, wan_port: str, dest_ip: str, dest_port: str, proto: str = "tcp") -> tuple[bool, str]:
        if "," in wan_port or ":" in wan_port or "," in dest_port or ":" in dest_port:
            raise ValueError("DNAT requires single numeric ports.")
        self._ports(wan_port)
        self._ports(dest_port)
        if proto not in {"tcp", "udp"}:
            raise ValueError("DNAT requires tcp or udp.")
        dest_ip = str(ipaddress.IPv4Address(dest_ip))
        if not self.get_sysctl_forwarding():
            raise ValueError("Enable kernel IP forwarding before adding a forward.")
        content = UFW_BEFORE_RULES.read_text(encoding="utf-8")
        # Replace only owned DNAT rules for this port/protocol. Legacy rules are
        # visible in the report but never silently adopted or removed.
        owned = re.search(r"# BEGIN DUSKY DNAT\n(.*?)# END DUSKY DNAT", content, re.DOTALL)
        lines = [line for line in (owned[1].splitlines() if owned else []) if line.startswith("-A dusky-dnat ")]
        prefix = f"-A dusky-dnat -p {proto} --dport {wan_port} "
        lines = [line for line in lines if not line.startswith(prefix)]
        lines.append(prefix + f"-j DNAT --to-destination {dest_ip}:{dest_port}")
        body = "*nat\n:dusky-dnat - [0:0]\n" + "\n".join(lines) + "\nCOMMIT"
        # Kernel target support varies with the ISO build. Check before storing
        # files or companion routes; --test constructs but never commits rules.
        self._validate_restore(body)
        tag = f"dusky:dnat:{wan_port}/{proto}"
        self._update_framework({UFW_BEFORE_RULES: self._managed_block(content, "DNAT", body),
                                UFW_AFTER_INIT: self._framework_hook()})
        self._delete_tagged(tags={tag})
        self._run_cmd(["ufw", "route", "allow", "proto", proto, "to", dest_ip, "port", dest_port, "comment", tag])
        return True, f"IPv4 DNAT {wan_port}/{proto} -> {dest_ip}:{dest_port} configured on all ingress interfaces."

    @operation
    def remove_port_forward(self, wan_port: str, proto: str = "tcp") -> tuple[bool, str]:
        self._ports(wan_port)
        if not wan_port.isdigit() or proto not in {"tcp", "udp"}:
            raise ValueError("Use a single numeric port and tcp or udp.")
        content = UFW_BEFORE_RULES.read_text(encoding="utf-8")
        owned = re.search(r"# BEGIN DUSKY DNAT\n(.*?)# END DUSKY DNAT", content, re.DOTALL)
        if not owned:
            return False, "No Dusky DNAT block found; manually configured forwards are retained."
        prefix = f"-A dusky-dnat -p {proto} --dport {wan_port} "
        lines = [line for line in owned[1].splitlines() if line.startswith("-A dusky-dnat ") and not line.startswith(prefix)]
        body = "*nat\n:dusky-dnat - [0:0]\n" + "\n".join(lines) + "\nCOMMIT"
        self._update_framework({UFW_BEFORE_RULES: self._managed_block(content, "DNAT", body),
                                UFW_AFTER_INIT: self._framework_hook()})
        self._delete_tagged(tags={f"dusky:dnat:{wan_port}/{proto}"})
        return True, f"Managed {wan_port}/{proto} forward removed."

    # =========================================================================
    # 8. APP PROFILES & SYSTEM HELPERS
    # =========================================================================
    def get_app_profile_names(self) -> list[str]:
        report = self._run_cmd(["ufw", "app", "list"]).stdout.splitlines()
        return sorted(line.strip() for line in report[1:] if line.strip())

    def get_app_profiles(self) -> list[AppProfileDict]:
        profiles: list[AppProfileDict] = []
        app_names = self.get_app_profile_names()

        for name in sorted(app_names):
            info_res = self._run_cmd(["ufw", "app", "info", name])
            title = ""
            desc = ""
            ports = ""
            if info_res.returncode == 0:
                collecting_ports = False
                collecting_description = False
                port_lines = []
                for line in info_res.stdout.splitlines():
                    ls = line.strip()
                    if ls.startswith("Title:"):
                        title = ls.split(":", 1)[1].strip()
                    elif ls.startswith("Description:"):
                        desc = ls.split(":", 1)[1].strip()
                        collecting_description = True
                    elif ls in {"Port:", "Ports:"}:
                        collecting_description = False
                        collecting_ports = True
                    elif collecting_ports and ls:
                        port_lines.append(ls)
                    elif collecting_description and ls:
                        desc += " " + ls
                ports = " | ".join(port_lines)

            profiles.append({
                "name": name,
                "title": title or name,
                "description": desc or "No description available",
                "ports": ports or "dynamic/any",
            })

        return profiles

    def get_listening_ports(self) -> list[dict[str, Any]]:
        res = self._run_cmd(["ufw", "show", "listening"])
        items: list[dict[str, Any]] = []
        current_proto = ""
        current_item: dict[str, Any] | None = None

        for line in res.stdout.splitlines():
            s = line.strip()
            if s in ("tcp:", "udp:", "tcp6:", "udp6:"):
                current_proto = s.rstrip(":")
                continue

            match_entry = re.prefixmatch(r"^(\d+)\s+([^\s]+)\s+\(([^)]+)\)$", s)
            if match_entry:
                if current_item:
                    items.append(current_item)
                current_item = {
                    "proto": current_proto,
                    "port": int(match_entry.group(1)),
                    "bound_addr": match_entry.group(2),
                    "process": match_entry.group(3),
                    "rules": [],
                }
                continue

            if current_item and s.startswith("["):
                current_item["rules"].append(s)

        if current_item:
            items.append(current_item)

        return items

    def get_report(self, report_name: str) -> str:
        valid_reports = {
            "listening",
            "added",
            "user-rules",
            "before-rules",
            "after-rules",
            "logging-rules",
            "builtins",
            "raw",
        }
        if report_name not in valid_reports:
            return f"Error: Invalid report type '{report_name}'. Valid: {', '.join(sorted(valid_reports))}"

        res = self._run_cmd(["ufw", "show", report_name])
        return res.stdout

    @staticmethod
    def get_network_interfaces() -> list[str]:
        try:
            net_path = Path("/sys/class/net")
            if net_path.exists():
                return sorted([p.name for p in net_path.iterdir() if p.is_dir() or p.is_symlink()])
        except OSError:
            pass
        return []

    @staticmethod
    def detect_wan_interface() -> str:
        """Use the lowest-metric default route, without guessing by interface name."""
        try:
            result = subprocess.run(["ip", "-j", "route", "show", "default"], capture_output=True,
                                    text=True, timeout=5, check=True, env={**os.environ, "LC_ALL": "C"})
            routes = sorted(json.loads(result.stdout), key=lambda route: route.get("metric", 0))
            return next((route["dev"] for route in routes if route.get("dev")), "")
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""

    # =========================================================================
    # 9. DOMAIN RESOLUTION & SYNC
    # =========================================================================
    @staticmethod
    def resolve_domain_ips(domain: str) -> list[str]:
        """Bound libc/NSS resolution in a child process; threads cannot cancel it."""
        script = ("import json,socket,sys; print(json.dumps(sorted({a[4][0] for a in "
                  "socket.getaddrinfo(sys.argv[1],None,socket.AF_UNSPEC,socket.SOCK_STREAM)})))")
        try:
            result = subprocess.run([sys.executable, "-c", script, domain.strip().lower()],
                                    capture_output=True, text=True, timeout=CMD_TIMEOUT_READ, check=True)
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.warning("DNS resolution failed for %s: %s", domain, exc)
            return []

    def _ipv6_enabled(self) -> bool:
        return Path("/proc/sys/net/ipv6").exists() and bool(re.search(r'^IPV6=["\']?yes',
                            self.config_path.read_text(encoding="utf-8"), re.MULTILINE))

    @operation
    def sync_domains(self) -> tuple[bool, str]:
        data = self._read_domain_registry()
        domains = data.get("domains", [])
        failed = []
        with ThreadPoolExecutor(max_workers=min(8, len(domains) or 1)) as executor:
            futures = {executor.submit(self.resolve_domain_ips, d["domain"]): d for d in domains}
            for future, entry in futures.items():
                ips = future.result()
                if ips:
                    entry["ips"] = ips
                    entry["last_resolved"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                else:
                    failed.append(entry["domain"])
        # Failed resolutions retain last-known addresses; report the limitation.
        self._apply_domain_rules(data)
        self._write_domain_registry(data)
        if failed:
            return False, "Applied available addresses; DNS failed (last-known addresses retained): " + ", ".join(failed)
        return True, f"Synchronized {len(domains)} domains. Rules filter resolved IPs, not domain names."

    def _apply_domain_rules(self, data: dict[str, Any]) -> None:
        self._scrub_domain_rules()
        if data.get("whitelist_mode"):
            # UFW before rules already permit loopback and DHCP replies.
            # DNS must be allowed before switching outbound defaults to deny.
            self._run_cmd(["ufw", "allow", "out", "to", "any", "port", "53", "comment", "core:dns"])
            self._run_cmd(["ufw", "allow", "out", "to", "any", "port", "67,68", "proto", "udp", "comment", "core:dhcp"])
            self._run_cmd(["ufw", "allow", "out", "to", "any", "port", "546,547", "proto", "udp", "comment", "core:dhcp6"])
        # Prepend IP blocks so existing broad user allowances cannot shadow them.
        for entry in sorted(data.get("domains", []), key=lambda d: d.get("action") != "deny"):
            name, action = entry["domain"], entry.get("action", "allow")
            ports = entry.get("ports", "80,443")
            if action not in {"allow", "deny"}:
                raise ValueError("Domain action must be allow or deny")
            if ports != "any":
                self._ports(ports)
            for ip in entry.get("ips", []):
                address = ipaddress.ip_address(ip)
                if address.version == 6 and not self._ipv6_enabled():
                    continue
                ip = str(address)
                cmd = ["ufw"] + (["prepend"] if action == "deny" else []) + [action, "out", "to", ip]
                if ports != "any":
                    cmd += ["port", ports, "proto", "tcp"]
                cmd += ["comment", ("domain:" if action == "allow" else "block:") + name]
                self._run_cmd(cmd)

    def _scrub_domain_rules(self) -> None:
        self._delete_tagged(tags={"core:dns", "core:dhcp", "core:dhcp6", "core:loopback"}, prefixes=("domain:", "block:"))

    # =========================================================================
    # 10. FRAMEWORK (Sysctl, Waydroid, Docker)
    # =========================================================================
    @staticmethod
    def get_sysctl_forwarding() -> bool:
        if not UFW_SYSCTL_CONF.exists():
            return False
        values = re.findall(r"^\s*net[/.]ipv4[/.]ip_forward\s*=\s*([01])\b",
                            UFW_SYSCTL_CONF.read_text(encoding="utf-8"), re.MULTILINE)
        return bool(values) and values[-1] == "1"

    @operation
    def set_sysctl_forwarding(self, enabled: bool) -> tuple[bool, str]:
        content = UFW_SYSCTL_CONF.read_text(encoding="utf-8") if UFW_SYSCTL_CONF.exists() else ""
        keys = ["net/ipv4/ip_forward"]
        if Path("/proc/sys/net/ipv6").exists():
            keys += ["net/ipv6/conf/default/forwarding", "net/ipv6/conf/all/forwarding"]
        for key in keys:
            pattern = r"^#?\s*" + key.replace("/", r"[/.]") + r"\s*=.*$"
            line = f"{key}={int(enabled)}"
            if re.search(pattern, content, re.MULTILINE):
                content = re.sub(pattern, line, content, flags=re.MULTILINE)
            else:
                content = content.rstrip() + "\n" + line + "\n"
        self._update_framework({UFW_SYSCTL_CONF: content})
        # UFW doesn't apply sysctls while inactive and suppresses sysctl errors.
        # Apply only the touched keys and check them explicitly.
        self._run_cmd(["sysctl", "-w", *[f"{key}={int(enabled)}" for key in keys]])
        return True, f"Stored and applied IP forwarding={int(enabled)}."

    @staticmethod
    def get_waydroid_nat() -> bool:
        return UFW_BEFORE_RULES.exists() and bool(re.search(r"^-A dusky-waydroid .* -j MASQUERADE$",
                    UFW_BEFORE_RULES.read_text(encoding="utf-8"), re.MULTILINE))

    @operation
    def set_waydroid_nat(self, enabled: bool) -> tuple[bool, str]:
        content = UFW_BEFORE_RULES.read_text(encoding="utf-8")
        rules = []
        if enabled:
            interface = str(self.cache.get("framework/waydroid_interface", "waydroid0"))
            result = self._run_cmd(["ip", "-j", "-4", "address", "show", "dev", interface])
            networks = {str(ipaddress.ip_interface(f"{addr['local']}/{addr['prefixlen']}").network)
                        for link in json.loads(result.stdout) for addr in link.get("addr_info", [])
                        if addr.get("family") == "inet"}
            wan = str(self.cache.get("framework/wan_interface", "")) or self.detect_wan_interface()
            if not networks or not wan:
                return False, "An IPv4 container subnet and egress interface are required."
            self._validate_interface(interface)
            self._validate_interface(wan)
            rules = [f"-A dusky-waydroid -s {network} -o {wan} -j MASQUERADE" for network in sorted(networks)]
        body = "*nat\n:dusky-waydroid - [0:0]\n" + "\n".join(rules) + "\nCOMMIT"
        self._update_framework({UFW_BEFORE_RULES: self._managed_block(content, "WAYDROID", body),
                                UFW_AFTER_INIT: self._framework_hook()})
        return True, "Managed container NAT " + ("configured; forwarding and route allowances are also required." if enabled else "removed.")

    @staticmethod
    def get_docker_mitigation() -> bool:
        return UFW_AFTER_RULES.exists() and bool(re.search(r"^-A dusky-docker .* -j DROP$",
                    UFW_AFTER_RULES.read_text(encoding="utf-8"), re.MULTILINE))

    @operation
    def set_docker_mitigation(self, enabled: bool) -> tuple[bool, str]:
        wan = str(self.cache.get("framework/wan_interface", "")) or self.detect_wan_interface()
        if enabled:
            if not wan:
                return False, "Choose an egress interface or configure a default route."
            self._validate_interface(wan)
            # Only Docker's iptables backend exposes DOCKER-USER. Do not create
            # an unreferenced chain and claim a native nftables backend is guarded.
            self._run_cmd(["iptables", "-w", "5", "-S", "DOCKER-USER"])
            self._run_cmd(["iptables", "-w", "5", "-C", "FORWARD", "-j", "DOCKER-USER"])
        updates = {UFW_AFTER_INIT: self._framework_hook()}
        for path in (UFW_AFTER_RULES, UFW_AFTER6_RULES):
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            rules = (f"-A dusky-docker -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN\n"
                     f"-A dusky-docker -i {wan} -j DROP\n") if enabled else ""
            updates[path] = self._managed_block(content, "DOCKER", "*filter\n:dusky-docker - [0:0]\n" + rules + "COMMIT")
        self._update_framework(updates)
        return True, "Docker iptables guard " + ("configured for " + wan + "; Docker must retain its DOCKER-USER hook." if enabled else "removed.")

    @staticmethod
    def _validate_interface(interface: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,15}", interface):
            raise ValueError("Invalid interface name")

    # =========================================================================
    # 11. PRESETS
    # =========================================================================
    @operation
    def apply_preset(self, preset_name: str) -> tuple[bool, str]:
        match preset_name:
            case "dusky_full":
                wan = str(self.cache.get("framework/wan_interface", "")) or self.detect_wan_interface()
                self.set_sysctl_forwarding(True)
                interfaces = self.get_network_interfaces()
                container = str(self.cache.get("framework/waydroid_interface", "waydroid0"))
                if container in interfaces:
                    self.set_waydroid_nat(True)
                # Docker integration is explicit: it depends on the backend and
                # daemon lifecycle and cannot be guaranteed by this preset.
                self._set_policies("deny", "allow", "deny")
                result = self._run_cmd(["sshd", "-T"], check=False)
                ports = re.findall(r"^port ([0-9]+)$", result.stdout, re.MULTILINE) if result.returncode == 0 else []
                for port in ports:
                    self.open_port(port, "tcp", comment="OpenSSH")
                self.open_port("41641", "udp", comment="Tailscale Direct P2P")
                trusted = str(self.cache.get("framework/trusted_interfaces", "tailscale0,waydroid0,virbr0,docker0,wg0,tun0,tap0")).split(",")
                for iface in (name.strip() for name in trusted if name.strip() in interfaces):
                    self._run_cmd(["ufw", "allow", "in", "on", iface, "comment", f"Trust IN: {iface}"])
                    if wan:
                        self._run_cmd(["ufw", "route", "allow", "in", "on", iface, "out", "on", wan, "comment", f"Forward: {iface} -> WAN"])
                self._enable_firewall()
                return True, "Dusky provisioning applied to present interfaces; existing rules retained. Docker guard is a separate control."
            case "strict_workstation":
                self._set_policies("deny", "allow", "deny")
                self.open_port("22", "tcp", comment="OpenSSH")
                self._enable_firewall()
                return True, "Workstation defaults applied; existing rules retained."
            case "lockdown_whitelist":
                return self._set_whitelist(True)
            case "dev_lan":
                for subnet in ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"):
                    self._run_cmd(["ufw", "allow", "from", subnet, "comment", f"Dev LAN: {subnet}"])
                self.open_port("3000,5173,8000,8080", comment="Dev Server")
                return True, "Private LAN and development ingress rules added."
            case "stealth":
                self._set_policies("reject", "allow", "deny")
                self._run_cmd(["ufw", "prepend", "limit", "22/tcp", "comment", "SSH rate-limit"])
                self._run_cmd(["ufw", "logging", "medium"])
                self.set_icmp_ping_stealth(True)
                return True, "Reject defaults, SSH rate limit, input ping drop and medium logs configured."
            case "streaming_moonlight":
                self.toggle_service("moonlight", True)
                self.open_port("5901", comment="VNC Streaming")
                return True, "Moonlight and VNC ingress rules added."
            case "factory_reset":
                self._run_cmd(["ufw", "--force", "reset"])
                self._reset_domain_mode()
                return True, "UFW reset and disabled; domain registry retained with allowlist mode off."
            case _:
                return False, f"Unknown preset: {preset_name}"

    def _enable_firewall(self) -> None:
        self._run_cmd(["systemctl", "enable", "ufw.service"])
        self._run_cmd(["ufw", "--force", "enable"])

    def _set_policies(self, incoming: str, outgoing: str, routed: str) -> None:
        for policy, direction in ((incoming, "incoming"), (outgoing, "outgoing"), (routed, "routed")):
            self._run_cmd(["ufw", "default", policy, direction])

    def _reset_domain_mode(self) -> None:
        data = self._read_domain_registry()
        data["whitelist_mode"] = False
        if DOMAINS_STORAGE.exists():
            self._write_domain_registry(data)

    def _set_whitelist(self, enabled: bool) -> tuple[bool, str]:
        data = self._read_domain_registry()
        data["whitelist_mode"] = enabled
        if enabled:
            # Resolve while egress still works. Do not switch defaults when a
            # new domain has no usable address.
            for entry in data["domains"]:
                resolved = self.resolve_domain_ips(entry["domain"])
                if resolved:
                    entry["ips"] = resolved
                    entry["last_resolved"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                elif not entry.get("ips"):
                    return False, f"Cannot resolve {entry['domain']}; default policies were not changed."
            self._apply_domain_rules(data)
            self._set_policies("deny", "deny", "deny")
            self._enable_firewall()
        else:
            self._set_policies("deny", "allow", "deny")
            # Keep registered allow/block entries; remove only mode support.
            self._delete_tagged(tags={"core:dns", "core:dhcp", "core:dhcp6", "core:loopback"})
        self._write_domain_registry(data)
        return True, "Domain allowlist defaults " + ("enabled" if enabled else "disabled") + "; existing allowances and established sessions retained."

    # =========================================================================
    # 12. BASEENGINE CONTRACT (load_state & write_value)
    # =========================================================================
    def load_state(self) -> dict[str, Any]:
        with self._lock:
            state: dict[str, Any] = {}

            # Status
            status_info = self.get_status_verbose()
            state["status/firewall_enabled"] = "true" if status_info["active"] else "false"
            state["status/logging_level"] = status_info["logging"]
            state["status/default_incoming"] = status_info["default_incoming"]
            state["status/default_outgoing"] = status_info["default_outgoing"]
            state["status/default_routed"] = status_info["default_routed"]
            state["status/new_profiles"] = status_info["new_profiles"]

            # Framework
            state["framework/ip_forward"] = "true" if self.get_sysctl_forwarding() else "false"
            state["framework/waydroid_nat"] = "true" if self.get_waydroid_nat() else "false"
            state["framework/docker_mitigation"] = "true" if self.get_docker_mitigation() else "false"
            state["framework/icmp_stealth"] = "true" if self.get_icmp_ping_stealth() else "false"

            # Domain filter
            domain_data = self._read_domain_registry()
            state["domains/whitelist_mode"] = "true" if domain_data.get("whitelist_mode") else "false"

            # One rule snapshot serves every service in this state load.
            stored_commands = self._stored_rule_commands()
            for svc_key in COMMON_SERVICES:
                state[f"services/{svc_key}"] = "true" if self.is_service_allowed(svc_key, commands=stored_commands) else "false"

            # Quick port defaults
            state["ports/quick_port"] = "8080"
            state["ports/quick_proto"] = "tcp"
            state["ports/quick_scope"] = "any"
            state["ports/quick_action"] = "allow"
            state["ports/quick_comment"] = "Custom Port Rule"
            state["ports/probe_port"] = "22"

            # Rule Builder defaults
            builder_defaults = {
                "action": "allow",
                "direction": "in",
                "proto": "any",
                "port": "",
                "source": "any",
                "dest": "any",
                "interface": "any",
                "out_interface": "any",
                "log": "none",
                "comment": "",
                "placement": "append",
                "insert_num": 1,
            }
            for k, v in builder_defaults.items():
                state[f"builder/{k}"] = str(v)

            # NAT defaults
            state["nat/forward_ext_port"] = "8080"
            state["nat/forward_dest_ip"] = ""
            state["nat/forward_dest_port"] = "80"
            state["nat/forward_proto"] = "tcp"

            # Connections
            state["connections/ban_ip_target"] = ""
            state["reports/selected_report"] = "listening"

            # Action trigger resets
            for act_key in (
                "action_reload", "action_reset", "action_apply_rule", "action_delete_rule",
                "action_sync_domains", "action_add_domain", "action_remove_domain",
                "action_open_port", "action_close_port", "action_reject_port", "action_delete_port_rules",
                "action_probe_port", "action_ban_ip", "action_unban_ip", "action_panic_lockdown",
                "action_panic_restore", "action_add_forward", "action_remove_forward",
            ):
                state[f"actions/{act_key}"] = "false"

            draft_defaults = {
                "builder/target_delete_num": "1", "builder/source_port": "", "ports/probe_result": "Not run", "domains/draft_domain": "",
                "domains/draft_action": "allow", "domains/draft_ports": "80,443",
                "app/target_app": "", "framework/wan_interface": "",
                "framework/waydroid_interface": "waydroid0",
                "framework/trusted_interfaces": "tailscale0,waydroid0,virbr0,docker0,wg0,tun0,tap0",
            }
            state.update(draft_defaults)
            for key in tuple(state):
                if key.startswith(("ports/", "builder/", "nat/", "connections/", "reports/", "app/")) or key in draft_defaults:
                    state[key] = self.cache.get(key, state[key])
            for action in ("app_allow", "app_deny", "action_preset_dusky_full", "action_preset_strict_workstation",
                           "action_preset_lockdown_whitelist", "action_preset_dev_lan", "action_preset_stealth",
                           "action_preset_streaming_moonlight", "action_preset_factory_reset"):
                state[f"actions/{action}"] = "false"
            self.cache = state
            return self.cache

    @operation
    def write_value(
        self, target_key: str, target_scope: str, new_value: str, item_type: str = "string"
    ) -> tuple[bool, str, str]:
        logger.info("UfwEngine write_value: key=%s scope=%s val=%s", target_key, target_scope, new_value)

        # 1. Firewall Power & Global Policies
        if (target_scope == "actions" or target_key.startswith("action_")) and new_value.lower() in {"false", "0", "no", "off"}:
            return True, "Trigger idle.", ""
        if target_key == "firewall_enabled":
            if new_value.lower() in {"true", "1", "yes", "on"}:
                self._enable_firewall()
            else:
                self._run_cmd(["ufw", "disable"])
            return True, "Firewall state changed.", ""

        if target_key == "logging_level":
            res = self._run_cmd(["ufw", "logging", new_value.strip().lower()])
            return True, res.stdout.strip(), ""

        if target_key == "default_incoming":
            res = self._run_cmd(["ufw", "default", new_value.strip().lower(), "incoming"])
            return True, res.stdout.strip(), ""

        if target_key == "default_outgoing":
            res = self._run_cmd(["ufw", "default", new_value.strip().lower(), "outgoing"])
            return True, res.stdout.strip(), ""

        if target_key == "default_routed":
            res = self._run_cmd(["ufw", "default", new_value.strip().lower(), "routed"])
            return True, res.stdout.strip(), ""

        # 2. Quick Port Actions
        if target_key == "action_open_port":
            port = str(self.cache.get("ports/quick_port", "")).strip()
            proto = str(self.cache.get("ports/quick_proto", "tcp")).strip()
            scope = str(self.cache.get("ports/quick_scope", "any")).strip()
            comment = str(self.cache.get("ports/quick_comment", "Quick Open")).strip()
            ok, msg = self.open_port(port, proto=proto, scope=scope, comment=comment)
            return ok, msg, ""

        if target_key == "action_close_port":
            port = str(self.cache.get("ports/quick_port", "")).strip()
            proto = str(self.cache.get("ports/quick_proto", "tcp")).strip()
            comment = str(self.cache.get("ports/quick_comment", "Quick Close")).strip()
            ok, msg = self.close_port(port, proto=proto, action="deny", comment=comment)
            return ok, msg, ""

        if target_key == "action_reject_port":
            port = str(self.cache.get("ports/quick_port", "")).strip()
            proto = str(self.cache.get("ports/quick_proto", "tcp")).strip()
            comment = str(self.cache.get("ports/quick_comment", "Quick Reject")).strip()
            ok, msg = self.close_port(port, proto=proto, action="reject", comment=comment)
            return ok, msg, ""

        if target_key == "action_delete_port_rules":
            port = str(self.cache.get("ports/quick_port", "")).strip()
            proto = str(self.cache.get("ports/quick_proto", "tcp")).strip()
            ok, msg = self.delete_port_rules(port, proto=proto)
            return ok, msg, ""

        if target_key == "action_probe_port":
            port_val = str(self.cache.get("ports/probe_port", "22")).strip()
            if not port_val.isdigit():
                return False, "Enter a numeric port to probe.", ""
            probe_data = self.probe_port(int(port_val), proto="tcp")
            self.cache["ports/probe_result"] = probe_data["summary"]
            return True, probe_data["summary"], ""

        # 3. Common Services
        if target_scope == "services" or target_key in COMMON_SERVICES:
            svc_key = target_key
            en = new_value in ("true", "1", "yes", "on")
            ok, msg = self.toggle_service(svc_key, en)
            return ok, msg, ""

        # 4. Active Connections, IP Ban & Panic
        if target_key == "action_ban_ip":
            target_ip = str(self.cache.get("connections/ban_ip_target", "")).strip()
            ok, msg = self.ban_ip(target_ip)
            return ok, msg, ""

        if target_key == "action_unban_ip":
            target_ip = str(self.cache.get("connections/ban_ip_target", "")).strip()
            ok, msg = self.unban_ip(target_ip)
            return ok, msg, ""

        if target_key == "action_panic_lockdown":
            ok, msg = self.panic_lockdown(True)
            return ok, msg, ""

        if target_key == "action_panic_restore":
            ok, msg = self.panic_lockdown(False)
            return ok, msg, ""

        if target_key == "icmp_stealth":
            en = new_value in ("true", "1", "yes", "on")
            ok, msg = self.set_icmp_ping_stealth(en)
            return ok, msg, ""

        # 5. Port Forwarding
        if target_key == "action_add_forward":
            ext_p = str(self.cache.get("nat/forward_ext_port", "")).strip()
            dst_ip = str(self.cache.get("nat/forward_dest_ip", "")).strip()
            dst_p = str(self.cache.get("nat/forward_dest_port", "")).strip()
            proto = str(self.cache.get("nat/forward_proto", "tcp")).strip()
            ok, msg = self.add_port_forward(ext_p, dst_ip, dst_p, proto=proto)
            return ok, msg, ""

        if target_key == "action_remove_forward":
            ext_p = str(self.cache.get("nat/forward_ext_port", "")).strip()
            proto = str(self.cache.get("nat/forward_proto", "tcp")).strip()
            ok, msg = self.remove_port_forward(ext_p, proto=proto)
            return ok, msg, ""

        # 6. Global Actions & Framework
        if target_key == "action_reload":
            res = self._run_cmd(["ufw", "reload"])
            return True, "Firewall reloaded.", ""

        if target_key == "action_reset":
            res = self._run_cmd(["ufw", "--force", "reset"])
            self._reset_domain_mode()
            return True, "Firewall reset and disabled.", ""

        if target_key == "ip_forward":
            en = new_value in ("true", "1", "yes", "on")
            ok, msg = self.set_sysctl_forwarding(en)
            return ok, msg, ""

        if target_key == "waydroid_nat":
            en = new_value in ("true", "1", "yes", "on")
            ok, msg = self.set_waydroid_nat(en)
            return ok, msg, ""

        if target_key == "docker_mitigation":
            en = new_value in ("true", "1", "yes", "on")
            ok, msg = self.set_docker_mitigation(en)
            return ok, msg, ""

        # 7. Domain Whitelist / Blacklist
        if target_key == "whitelist_mode":
            ok, msg = self._set_whitelist(new_value in {"true", "1", "yes", "on"})
            return ok, msg, ""

        if target_key == "action_sync_domains":
            ok, msg = self.sync_domains()
            return ok, msg, ""

        if target_key == "action_add_domain":
            domain = str(self.cache.get("domains/draft_domain", "")).strip().lower()
            action = str(self.cache.get("domains/draft_action", "allow")).strip().lower()
            ports = str(self.cache.get("domains/draft_ports", "80,443")).strip()

            if not domain:
                return False, "Domain name cannot be empty.", ""

            domain = domain.encode("idna").decode("ascii")
            if not re.fullmatch(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain):
                raise ValueError("Enter a domain name without a URL scheme or path.")
            if action not in {"allow", "deny"}:
                raise ValueError("Choose allow or deny.")
            if ports != "any":
                self._ports(ports)
            data = self._read_domain_registry()
            filtered = [d for d in data.get("domains", []) if d["domain"] != domain]
            filtered.append({
                "domain": domain,
                "action": action,
                "ports": ports,
                "ips": [],
                "last_resolved": "",
            })
            data["domains"] = filtered
            self._write_domain_registry(data)
            ok, msg = self.sync_domains()
            if not ok:
                return False, msg, ""
            return True, f"Domain '{domain}' registered ({action}).", ""

        if target_key == "action_remove_domain":
            domain = str(self.cache.get("domains/draft_domain", "")).strip().lower()
            if not domain:
                return False, "Specify the domain name to remove in the draft input.", ""
            domain = domain.encode("idna").decode("ascii")

            data = self._read_domain_registry()
            data["domains"] = [d for d in data.get("domains", []) if d["domain"] != domain]
            self._write_domain_registry(data)

            self._delete_tagged(tags={f"domain:{domain}", f"block:{domain}"})
            return True, f"Domain '{domain}' removed from registry and firewall.", ""

        # 8. Rule Builder Execution
        if target_key == "action_apply_rule":
            cmd = self._construct_rule_command()
            res = self._run_cmd(cmd, timeout=CMD_TIMEOUT_WRITE)
            if res.returncode == 0:
                return True, f"Rule created: {' '.join(cmd)}", ""
            return False, f"Rule error: {res.stderr.strip() or res.stdout.strip()}", ""

        if target_key == "action_delete_rule":
            target_num_str = str(self.cache.get("builder/target_delete_num", "")).strip()
            if not target_num_str or not target_num_str.isdigit():
                return False, "Specify a valid rule number to delete.", ""
            res = self._run_cmd(["ufw", "--force", "delete", target_num_str])
            if res.returncode == 0:
                return True, f"Deleted rule #{target_num_str}.", ""
            return False, f"Failed to delete rule #{target_num_str}: {res.stderr.strip()}", ""

        # 9. Presets
        if target_key.startswith("action_preset_"):
            preset_name = target_key.removeprefix("action_preset_")
            ok, msg = self.apply_preset(preset_name)
            return ok, msg, ""

        # 10. Application Integration
        if target_key == "app_allow":
            app_name = str(self.cache.get("app/target_app", "")).strip()
            if not app_name:
                return False, "Select an application profile.", ""
            self._validate_restore('*filter\n:dusky-profile-check - [0:0]\n-A dusky-profile-check -m comment --comment dusky -j RETURN\nCOMMIT')
            res = self._run_cmd(["ufw", "allow", app_name])
            return True, res.stdout.strip(), ""

        if target_key == "app_deny":
            app_name = str(self.cache.get("app/target_app", "")).strip()
            if not app_name:
                return False, "Select an application profile.", ""
            self._validate_restore('*filter\n:dusky-profile-check - [0:0]\n-A dusky-profile-check -m comment --comment dusky -j RETURN\nCOMMIT')
            res = self._run_cmd(["ufw", "prepend", "deny", app_name])
            return True, res.stdout.strip(), ""

        # In-memory cache update
        self.cache[f"{target_scope}/{target_key}" if target_scope else target_key] = new_value
        return True, "Value updated in cache.", ""

    def _construct_rule_command(self) -> list[str]:
        def draft(key, default):
            return str(self.cache.get(f"builder/{key}", default)).strip()
        action, direction = draft("action", "allow"), draft("direction", "in")
        proto, port = draft("proto", "any"), draft("port", "")
        source, dest = draft("source", "any") or "any", draft("dest", "any") or "any"
        interface, out = draft("interface", "any"), draft("out_interface", "any")
        logging, comment = draft("log", "none"), draft("comment", "")
        placement, index = draft("placement", "append"), draft("insert_num", "1")
        if action not in {"allow", "deny", "reject", "limit"} or direction not in {"in", "out", "route"}:
            raise ValueError("Invalid rule action or direction")
        if proto not in {"any", "tcp", "udp", "ah", "esp", "gre", "vrrp", "ipv6", "igmp"}:
            raise ValueError("Invalid UFW protocol")
        source_port = draft("source_port", "")
        for selected_port in (port, source_port):
            if not selected_port:
                continue
            self._ports(selected_port)
            if proto not in {"any", "tcp", "udp"}:
                raise ValueError("This protocol cannot have ports")
            if proto == "any" and ("," in selected_port or ":" in selected_port):
                raise ValueError("Select tcp or udp for port lists and ranges")
        self._address(source)
        self._address(dest)
        if logging not in {"none", "log", "log-all"}:
            raise ValueError("Invalid per-rule logging mode")
        cmd = ["ufw"] + (["route"] if direction == "route" else [])
        if placement == "insert":
            if not index.isdigit() or int(index) < 1:
                raise ValueError("Insert index must be positive")
            cmd += ["insert", index]
        elif placement == "prepend":
            cmd += ["prepend"]
        elif placement != "append":
            raise ValueError("Invalid rule placement")
        cmd += [action]
        if direction in {"in", "out"}:
            cmd += [direction]
            if interface != "any":
                self._validate_interface(interface)
                cmd += ["on", interface]
        else:
            for label, iface in (("in", interface), ("out", out)):
                if iface != "any":
                    self._validate_interface(iface)
                    cmd += [label, "on", iface]
        if logging != "none":
            cmd += [logging]
        if proto != "any":
            cmd += ["proto", proto]
        cmd += ["from", source]
        if source_port:
            cmd += ["port", source_port]
        cmd += ["to", dest]
        if port:
            cmd += ["port", port]
        if comment:
            cmd += ["comment", comment]
        return cmd
