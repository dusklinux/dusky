#!/usr/bin/env python3
import asyncio
import signal
import sys
import os
import psutil
from datetime import datetime, date as datetime_date
from typing import Dict, Any, List, Optional, Tuple

from src.utils import logger, DB_PATH, get_default_interface
from src.database import (
    init_db, save_traffic_stats, save_dns_logs, get_rules, add_rule, delete_rule_by_value,
    get_quotas, set_quota, get_historical_report, get_top_talkers, cleanup_old_logs,
    reset_quota_status, get_quota_usage, QuotaUsage
)
from src.ebpf_collector import EbpfCollector
from src.traffic_shaper import TrafficShaper
from src.firewall import FirewallManager
from src.quota_manager import QuotaManager
from src.ipc import IPCServer

class DuskyDaemon:
    """Core daemon managing background threads, eBPF telemetry, tc bandwidth shaping, nftables, and IPC."""
    
    def __init__(self):
        self.collector = EbpfCollector()
        self.shaper = TrafficShaper()
        self.firewall = FirewallManager()
        self.quota = QuotaManager(self.shaper)
        self.ipc = IPCServer(self.handle_ipc_request)
        
        self.running = False
        self.rules_cache: List[Dict[str, Any]] = []
        
        # Live memory stats
        self.live_process_rates: Dict[int, Dict[str, Any]] = {}  # pid -> stats
        self.recent_connections: List[Dict[str, Any]] = []
        self.db_stats_buffer: List[Dict[str, Any]] = []
        self.db_dns_buffer: List[Dict[str, Any]] = []
        
        # Track dynamic socket ports for active throttling classes
        # (pid, sport) -> class_id
        self.tracked_ports: Dict[Tuple[int, int], int] = {}
        
    async def start(self) -> None:
        """Start daemon submodules and launch main loops."""
        self.running = True
        
        # 1. Initialize SQLite Database
        await init_db()
        
        # 2. Setup systems integrations
        self.shaper.init_qdiscs()
        self.firewall.init_firewall()
        
        # 3. Load and apply stored rules from database
        await self.reload_rules_from_db()
        
        # 4. Start eBPF engine
        self.collector.start()
        
        # 5. Start IPC Server
        await self.ipc.start()
        
        # 6. Run background tasks
        self.task1 = asyncio.create_task(self.main_loop())
        self.task2 = asyncio.create_task(self.database_flush_loop())
        self.task3 = asyncio.create_task(self.retention_cleanup_loop())
        
        logger.info("Dusky Network Limiter daemon started successfully.")

    async def stop(self) -> None:
        """Gracefully release kernel hooks, flush memory databases, and close unix sockets."""
        logger.info("Shutting down daemon...")
        self.running = False
        
        # Stop IPC server
        await self.ipc.stop()
        
        # Stop eBPF collector
        self.collector.stop()
        
        # Flush final buffers to database
        await self.flush_buffers_to_db()
        
        # Clean traffic shaping classes & nftables chains
        self.shaper.cleanup()
        self.firewall.cleanup()
        
        logger.info("Daemon shutdown complete.")

    async def reload_rules_from_db(self) -> None:
        """Load rules from database and configure traffic classes and nftables chains accordingly."""
        rules = await get_rules()
        self.rules_cache = rules
        
        # Reset firewalls and classes
        self.firewall.blocked_apps.clear()
        self.firewall.allowed_apps.clear()
        self.firewall.throttled_classes.clear()
        
        default_deny = False
        
        # First check if there is a default deny rule
        for r in rules:
            if r["target_type"] == "global" and r["target_value"] == "firewall_mode":
                default_deny = (r["policy"] == "default_deny")
                
        self.firewall.set_default_policy(default_deny)
        
        # Re-apply other rules
        for r in rules:
            val = r["target_value"]
            policy = r["policy"]
            target_type = r["target_type"]
            
            if target_type == "global":
                if val == "bandwidth":
                    self.shaper.set_global_limits(r["limit_down"], r["limit_up"])
                continue
                
            if policy == "block":
                self.firewall.add_block_rule(val)
            elif policy == "allow":
                self.firewall.add_allow_rule(val)
            elif policy == "limit":
                class_id = self.shaper.add_throttle_class(val, r["limit_down"], r["limit_up"])
                self.firewall.register_throttle_class(val, class_id)
                
        # Force placement of existing processes into correct cgroups
        self.contain_active_processes()

    def contain_active_processes(self) -> None:
        """Scan running processes and move them to their respective policy cgroups (blocked, limited, allowed)."""
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'username']):
            try:
                pid = proc.info['pid']
                name = proc.info['name']
                exe = proc.info['exe']
                username = proc.info['username']
                
                # Check match against rules
                for r in self.rules_cache:
                    target_type = r["target_type"]
                    target_val = r["target_value"]
                    policy = r["policy"]
                    
                    is_match = False
                    if target_type == "process" and name == target_val:
                        is_match = True
                    elif target_type == "executable" and exe == target_val:
                        is_match = True
                    elif target_type == "user" and username == target_val:
                        is_match = True
                        
                    if is_match:
                        # Move to correct cgroup
                        cgroup_name = self.firewall.get_cgroup_for_app(target_val, policy)
                        self.move_pid_to_cgroup(pid, cgroup_name)
            except Exception:
                pass

    def move_pid_to_cgroup(self, pid: int, cgroup_path: str) -> None:
        """Move PID to cgroup v2 directory (relative path under /sys/fs/cgroup/)."""
        full_path = os.path.join("/sys/fs/cgroup", cgroup_path)
        os.makedirs(full_path, exist_ok=True)
        try:
            with open(os.path.join(full_path, "cgroup.procs"), "w") as f:
                f.write(str(pid))
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.error(f"Root privilege required to write to cgroups path: {full_path}")
        except Exception as e:
            logger.debug(f"Cgroup assignment debug info (PID {pid} -> {cgroup_path}): {e}")

    async def main_loop(self) -> None:
        """1-second tick loop reading eBPF counters, managing cgroup Containment and socket port filters."""
        while self.running:
            start_time = time.time()
            try:
                # 1. Harvest eBPF & DNS logs
                stats, dns_logs = self.collector.harvest_stats()
                
                if stats or dns_logs:
                    logger.info(f"Telemetry harvest: {len(stats)} stats, {len(dns_logs)} DNS logs.")
                
                # Update buffers
                self.db_stats_buffer.extend(stats)
                self.db_dns_buffer.extend(dns_logs)
                
                # Sum session traffic for quota checks
                sent_sum = sum(item["bytes_sent"] for item in stats)
                recv_sum = sum(item["bytes_recv"] for item in stats)
                
                # 2. Check and enforce quotas
                await self.quota.update_and_check_quotas(sent_sum, recv_sum)
                
                # 3. Dynamic PID containment & local port matching
                current_active_ports: Dict[Tuple[int, int], int] = {}
                
                # Group stats by process for live rate dashboard
                new_process_rates = {}
                
                for item in stats:
                    pid = item["pid"]
                    comm = item["comm"]
                    exe = item["exe"]
                    username = item["username"]
                    sport = item["sport"]
                    dport = item["dport"]
                    proto = item["protocol"]
                    
                    # Accumulate live stats for dashboard
                    if pid not in new_process_rates:
                        new_process_rates[pid] = {
                            "pid": pid,
                            "comm": comm,
                            "exe": exe,
                            "username": username,
                            "rate_sent": 0,
                            "rate_recv": 0,
                            "connections": set()
                        }
                    
                    new_process_rates[pid]["rate_sent"] += item["bytes_sent"]
                    new_process_rates[pid]["rate_recv"] += item["bytes_recv"]
                    new_process_rates[pid]["connections"].add((item["saddr"], sport, item["daddr"], dport, proto))
                    
                    # Match PID against throttle limits
                    matched_limit_class: Optional[int] = None
                    matched_cgroup_path: Optional[str] = None
                    
                    for r in self.rules_cache:
                        target_type = r["target_type"]
                        target_val = r["target_value"]
                        policy = r["policy"]
                        
                        is_match = False
                        if target_type == "process" and comm == target_val:
                            is_match = True
                        elif target_type == "executable" and exe == target_val:
                            is_match = True
                        elif target_type == "user" and username == target_val:
                            is_match = True
                            
                        if is_match:
                            if policy == "block":
                                matched_cgroup_path = self.firewall.get_cgroup_for_app(target_val, "block")
                                break
                            elif policy == "limit":
                                class_id = self.shaper.cgroup_to_class.get(f"throttle_{target_val}")
                                if class_id:
                                    matched_limit_class = class_id
                                    matched_cgroup_path = self.firewall.get_cgroup_for_app(target_val, "limit")
                                    break
                                    
                    # Force process cgroup containment if needed
                    if matched_cgroup_path:
                        self.move_pid_to_cgroup(pid, matched_cgroup_path)
                        
                    # Apply local ingress port filter for throttled classes
                    if matched_limit_class and proto in ("TCP", "UDP"):
                        current_active_ports[(pid, sport)] = matched_limit_class
                        self.shaper.add_inbound_port_filter(sport, matched_limit_class)

                # Format connections for TUI
                self.live_process_rates = new_process_rates
                
                # Purge obsolete dynamic port filters
                for port_key, class_id in list(self.tracked_ports.items()):
                    if port_key not in current_active_ports:
                        pid, sport = port_key
                        self.shaper.remove_inbound_port_filter(sport)
                        
                self.tracked_ports = current_active_ports
                
            except Exception as e:
                logger.error(f"Error in main daemon loop: {e}")
                
            # Keep loop precisely at 1s intervals
            elapsed = time.time() - start_time
            sleep_time = max(0.1, 1.0 - elapsed)
            await asyncio.sleep(sleep_time)

    async def database_flush_loop(self) -> None:
        """Periodically flushes network accounting events and DNS query logs to SQLite (every 10s)."""
        while self.running:
            await asyncio.sleep(10)
            await self.flush_buffers_to_db()

    async def flush_buffers_to_db(self) -> None:
        """Write in-memory buffers to database asynchronously."""
        # Flush Stats
        if self.db_stats_buffer:
            buf = self.db_stats_buffer.copy()
            self.db_stats_buffer.clear()
            try:
                await save_traffic_stats(buf)
            except Exception as e:
                logger.error(f"Failed to flush stats to database: {e}")
                # Restore stats to buffer
                self.db_stats_buffer.extend(buf)
                
        # Flush DNS Logs
        if self.db_dns_buffer:
            buf = self.db_dns_buffer.copy()
            self.db_dns_buffer.clear()
            try:
                await save_dns_logs(buf)
            except Exception as e:
                logger.error(f"Failed to flush DNS logs to database: {e}")
                self.db_dns_buffer.extend(buf)

    async def retention_cleanup_loop(self) -> None:
        """Run a database retention cleanup task daily to delete stats older than 30 days."""
        while self.running:
            try:
                await cleanup_old_logs(days=30)
            except Exception as e:
                logger.error(f"Error running database retention policy: {e}")
            # Run once every 24 hours
            await asyncio.sleep(86400)

    async def handle_ipc_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """RPC router for incoming IPC messages from the CLI and TUI clients."""
        method = request.get("method")
        params = request.get("params", {})
        
        try:
            if method == "get_status":
                return {
                    "status": "ok",
                    "result": {
                        "interface": self.shaper.interface,
                        "global_limits": {
                            "limit_down": next((r["limit_down"] for r in self.rules_cache if r["target_type"] == "global" and r["target_value"] == "bandwidth"), None),
                            "limit_up": next((r["limit_up"] for r in self.rules_cache if r["target_type"] == "global" and r["target_value"] == "bandwidth"), None)
                        },
                        "firewall_default_deny": self.firewall.default_deny,
                        "is_quota_throttled": self.quota.is_throttled
                    }
                }
                
            elif method == "set_global_limit":
                limit_down = params.get("limit_down")
                limit_up = params.get("limit_up")
                # Save to DB
                await add_rule("global", "bandwidth", "limit", limit_down, limit_up)
                await self.reload_rules_from_db()
                return {"status": "ok"}
                
            elif method == "add_rule":
                target_type = params["target_type"]
                target_value = params["target_value"]
                policy = params["policy"]
                limit_down = params.get("limit_down")
                limit_up = params.get("limit_up")
                
                await add_rule(target_type, target_value, policy, limit_down, limit_up)
                await self.reload_rules_from_db()
                return {"status": "ok"}
                
            elif method == "remove_rule":
                target_value = params["target_value"]
                await delete_rule_by_value(target_value)
                await self.reload_rules_from_db()
                return {"status": "ok"}
                
            elif method == "get_rules":
                return {"status": "ok", "result": self.rules_cache}
                
            elif method == "get_live_rates":
                # Convert set of connections to lists for JSON serialization
                proc_list = []
                for pid, info in self.live_process_rates.items():
                    conns = []
                    for c in info["connections"]:
                        conns.append({
                            "saddr": c[0], "sport": c[1], "daddr": c[2], "dport": c[3], "proto": c[4]
                        })
                    proc_list.append({
                        "pid": pid,
                        "comm": info["comm"],
                        "exe": info["exe"],
                        "username": info["username"],
                        "rate_sent": info["rate_sent"],
                        "rate_recv": info["rate_recv"],
                        "connections": conns
                    })
                return {"status": "ok", "result": proc_list}
                
            elif method == "get_historical_report":
                period = params["period"]
                report = await get_historical_report(period)
                return {"status": "ok", "result": report}
                
            elif method == "get_top_talkers":
                limit = params.get("limit", 10)
                talkers = await get_top_talkers(limit)
                return {"status": "ok", "result": talkers}
                
            elif method == "set_quota":
                period = params["period"]
                limit_bytes = params["limit_bytes"]
                warn_threshold = params.get("warning_threshold", 0.8)
                await set_quota(period, limit_bytes, warn_threshold)
                return {"status": "ok"}
                
            elif method == "get_quota_status":
                quotas = await get_quotas()
                status_list = []
                for q in quotas:
                    period = q["period"]
                    limit = q["limit_bytes"]
                    p_start = self.quota.get_period_start(period)
                    usage = await get_quota_usage(period, p_start)
                    total_bytes = 0
                    blocked = False
                    if usage:
                        total_bytes = usage["bytes_sent"] + usage["bytes_recv"]
                        blocked = usage["blocked"]
                    status_list.append({
                        "period": period,
                        "limit_bytes": limit,
                        "used_bytes": total_bytes,
                        "blocked": blocked
                    })
                return {"status": "ok", "result": status_list}
                
            elif method == "reset_quotas":
                await self.quota.reset_quotas()
                # Reload DB configurations
                await self.reload_rules_from_db()
                return {"status": "ok"}
                
            else:
                return {"status": "error", "error": f"Unknown method: {method}"}
        except Exception as e:
            logger.error(f"Error handling IPC request ({method}): {e}")
            return {"status": "error", "error": str(e)}

import time

# Script entrypoint for running as systemd daemon
def main():
    if os.geteuid() != 0:
        print("Dusky Network Limiter daemon must be run as root.")
        sys.exit(1)
        
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    daemon = DuskyDaemon()
    
    async def handle_stop():
        try:
            await daemon.stop()
        finally:
            loop.stop()
            
    # Register termination signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(handle_stop()))
        
    try:
        loop.run_until_complete(daemon.start())
        # Run until cancelled
        loop.run_forever()
    except Exception as e:
        logger.error(f"Fatal error starting daemon: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
