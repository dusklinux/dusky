#!/usr/bin/env python3
"""
systemd-resolved Engine for Modern Arch Linux (July 2026)
Features: True POSIX Atomicity, Transactional Rollbacks, and Pre-flight Socket Contention Detection.
"""

import os
import re
lazy import shutil
lazy import stat
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine


class SystemdDnsEngine(BaseEngine):
    """
    High-performance, atomic engine for configuring systemd-resolved
    via drop-in files. Designed for systemd 261+ and Python 3.14+.
    """

    def __init__(self, config_path: str = "/etc/systemd/resolved.conf.d/99-dns-tui.conf"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.dropin_dir = self.config_path.parent
        self.cache: dict[str, Any] = {}

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def load_state(self) -> dict[str, Any]:
        """O(n) zero-overhead parser strictly for systemd [Resolve] drop-ins."""
        state: dict[str, Any] = {
            "DNS": "",
            "FallbackDNS": "",
            "DNSOverTLS": "opportunistic",
            "DNSSEC": "no",
            "LLMNR": "no",
            "MulticastDNS": "no",
            "DNSStubListener": "yes",
            "Cache": "yes",
        }

        if not self.config_path.exists():
            self.cache = state
            return self.cache

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                in_resolve = False
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(("#", ";")):
                        continue
                    if line.startswith("["):
                        in_resolve = (line == "[Resolve]")
                        continue
                    
                    if in_resolve and "=" in line:
                        k, v = line.split("=", 1)
                        state[k.strip()] = v.strip()
        except OSError as e:
            print(f"[DnsEngine] State Load Warning: {e}")

        self.cache = state
        return self.cache

    def _check_port_53_conflict(self) -> str | None:
        """
        Pre-flight check: Detects if services like `dnsmasq` or `bind` are 
        already holding Port 53, which would cause a silent stub listener failure.
        """
        try:
            res = subprocess.run(["ss", "-H", "-ltunp"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if not re.search(r':53(?:\s+|\t|$)', line):
                    continue

                if any(name in line for name in ("systemd-resolve", "systemd-resolved")):
                    continue

                parts = line.split()
                local_addr = next((col for col in parts if ":53" in col), None)
                if not local_addr:
                    continue

                clean_addr = re.sub(r'%.*$', '', local_addr)

                if clean_addr in ("127.0.0.53:53", "127.0.0.54:53", "[::1]:53"):
                    continue

                if clean_addr in ("0.0.0.0:53", "*:53", "[::]:53", ":::53", "127.0.0.1:53", "[::ffff:0.0.0.0]:53", "[::ffff:127.0.0.1]:53"):
                    match = re.search(r'users:\(\("([^"]+)"', line)
                    if match:
                        proc_name = match.group(1)
                        if proc_name not in ("systemd-resolve", "systemd-resolved"):
                            return proc_name
                    else:
                        for svc in ("dnsmasq", "named", "unbound", "pihole-FTL", "dnscrypt-proxy", "adguard-home", "coredns", "knot"):
                            chk = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True)
                            if chk.stdout.strip() == "active":
                                return svc
                        return "Conflicting DNS Server (wildcard 0.0.0.0:53)"
        except OSError:
            pass

        return None

    def _sync_resolv_conf_symlink(self, stub_mode: str) -> None:
        """Safely transitions the resolv.conf symlink, overriding immutable locks."""
        etc_resolv = Path("/etc/resolv.conf")
        target_stub = Path("/run/systemd/resolve/stub-resolv.conf")
        target_direct = Path("/run/systemd/resolve/resolv.conf")

        target = target_stub if stub_mode.lower() in ("yes", "udp", "tcp") else target_direct

        try:
            if etc_resolv.is_symlink():
                try:
                    if etc_resolv.resolve() == target.resolve():
                        return
                except OSError:
                    pass

            subprocess.run(["chattr", "-i", str(etc_resolv)], capture_output=True)

            if etc_resolv.exists() or etc_resolv.is_symlink():
                etc_resolv.unlink()

            etc_resolv.symlink_to(target)
        except OSError as e:
            print(f"[DnsEngine] Symlink Sync Warning: {e}")

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """Transactional batch writer with pre-flight checks and automatic rollbacks."""
        if not changes:
            return True, "No pending changes.", ""

        current_state = self.load_state()
        debug_output = ""
        action_cache_flush = False

        for key, _, val, itype in changes:
            if itype == "action":
                if key == "flush_dns_cache":
                    action_cache_flush = True
                continue
            current_state[key] = str(val)

        # Handle independent action triggers
        if action_cache_flush and len(changes) == 1:
            if shutil.which("resolvectl"):
                try:
                    res = subprocess.run(["resolvectl", "flush-caches"], check=True, capture_output=True, text=True)
                    return True, "Global DNS cache flushed successfully.", res.stderr
                except subprocess.CalledProcessError as e:
                    return False, "Failed to flush DNS cache.", e.stderr
            else:
                return False, "resolvectl command not found.", ""

        # Pre-Flight Security Check: Prevent DNS Blackout from dnsmasq contention
        requested_stub = current_state.get("DNSStubListener", "yes").lower()
        if requested_stub in ("yes", "udp", "tcp"):
            conflict_proc = self._check_port_53_conflict()
            if conflict_proc:
                return False, f"Transaction aborted. Port 53 is blocked by '{conflict_proc}'. Disable it first or set Stub Listener to 'no'.", ""

        # Transaction State Snapshot
        old_config_exists = self.config_path.exists()
        old_config_content = self.config_path.read_text(encoding="utf-8") if old_config_exists else ""

        self.dropin_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Managed strictly by the Dusky TUI systemd_dns Engine.",
            "# Do not edit manually; modifications will be overridden.",
            "[Resolve]"
        ]
        
        for k, v in current_state.items():
            lines.append(f"{k}={v}")
            
        content = "\n".join(lines) + "\n"

        # ---------------------------------------------------------
        # ATOMIC WRITE PHASE
        # ---------------------------------------------------------
        tmp_path: Path | None = None

        try:
            tmp_fd, tmp_path_str = tempfile.mkstemp(dir=str(self.dropin_dir), prefix=".99-dns-tui.", suffix=".tmp")
            tmp_path = Path(tmp_path_str)

            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            tmp_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp_path, self.config_path)

            dir_fd = os.open(str(self.dropin_dir), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

            # Route symlinks BEFORE restarting so systemd populates it instantly
            self._sync_resolv_conf_symlink(requested_stub)

            # ---------------------------------------------------------
            # COMMIT & VERIFY PHASE
            # ---------------------------------------------------------
            subprocess.run(["systemctl", "unmask", "systemd-resolved.service"], capture_output=True)
            subprocess.run(["systemctl", "enable", "systemd-resolved.service"], capture_output=True)
            
            res = subprocess.run(["systemctl", "restart", "systemd-resolved.service"], capture_output=True, text=True)
            debug_output += res.stderr
            
            if res.returncode != 0:
                raise RuntimeError(f"systemd-resolved failed to restart: {res.stderr.strip()}")

            if shutil.which("resolvectl"):
                subprocess.run(["resolvectl", "flush-caches"], capture_output=True)

            if shutil.which("nmcli"):
                chk_nm = subprocess.run(["systemctl", "is-active", "--quiet", "NetworkManager.service"], capture_output=True)
                if chk_nm.returncode == 0:
                    subprocess.run(["nmcli", "general", "reload", "dns-full"], capture_output=True)

            return True, "Atomic commit successful. systemd-resolved restarted.", debug_output

        except Exception as e:
            # ---------------------------------------------------------
            # ROLLBACK PHASE
            # ---------------------------------------------------------
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            
            if old_config_exists:
                try:
                    with open(self.config_path, "w", encoding="utf-8") as f:
                        f.write(old_config_content)
                        f.flush()
                        os.fsync(f.fileno())
                except OSError:
                    pass
            else:
                self.config_path.unlink(missing_ok=True)
                
            subprocess.run(["systemctl", "restart", "systemd-resolved.service"], capture_output=True)
            
            return False, f"Transaction failed and was safely rolled back. Reason: {e}", debug_output

