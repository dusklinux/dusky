import subprocess
import os
import shutil
from typing import List, Dict, Any, Set

from src.utils import logger

class FirewallManager:
    """Manages nftables rulesets and cgroup v2 directories for blocking, allowing, or marking packets."""
    
    def __init__(self, cgroup_root: str = "/sys/fs/cgroup/netctl"):
        self.table_name = "dusky_netctl"
        self.cgroup_root = cgroup_root
        os.makedirs(self.cgroup_root, exist_ok=True)
        # Track active block/allow/throttle definitions
        self.blocked_apps: Set[str] = set()
        self.allowed_apps: Set[str] = set()
        self.throttled_classes: Dict[str, int] = {} # cgroup_name -> class_id
        self.default_deny = False

    def init_firewall(self) -> None:
        """Create the nftables table and output filter chain."""
        logger.info("Initializing nftables table and chain...")
        subprocess.run(["nft", "add", "table", "inet", self.table_name], check=True)
        subprocess.run([
            "nft", "add", "chain", "inet", self.table_name, "output", 
            "{ type filter hook output priority filter ; policy accept ; }"
        ], check=True)
        self.apply_rules()

    def set_default_policy(self, default_deny: bool) -> None:
        """Toggle firewall between default-allow (blocklist) and default-deny (allowlist)."""
        self.default_deny = default_deny
        logger.info(f"Firewall policy set to: {'DEFAULT-DENY (Allowlist)' if default_deny else 'DEFAULT-ALLOW (Blocklist)'}")
        self.apply_rules()

    def add_block_rule(self, app_name: str) -> None:
        """Add an application to the blocklist."""
        self.blocked_apps.add(app_name)
        # Create matching cgroup path
        cgroup_path = os.path.join(self.cgroup_root, f"block_{app_name}")
        os.makedirs(cgroup_path, exist_ok=True)
        logger.info(f"Cgroup created for blocking app '{app_name}': {cgroup_path}")
        self.apply_rules()

    def remove_block_rule(self, app_name: str) -> None:
        """Remove an application from the blocklist."""
        self.blocked_apps.discard(app_name)
        cgroup_path = os.path.join(self.cgroup_root, f"block_{app_name}")
        if os.path.exists(cgroup_path):
            try:
                shutil.rmtree(cgroup_path)
            except Exception:
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
        self.apply_rules()

    def add_allow_rule(self, app_name: str) -> None:
        """Add an application to the allowlist (relevant in default-deny mode)."""
        self.allowed_apps.add(app_name)
        cgroup_path = os.path.join(self.cgroup_root, f"allow_{app_name}")
        os.makedirs(cgroup_path, exist_ok=True)
        logger.info(f"Cgroup created for allowing app '{app_name}': {cgroup_path}")
        self.apply_rules()

    def remove_allow_rule(self, app_name: str) -> None:
        """Remove an application from the allowlist."""
        self.allowed_apps.discard(app_name)
        cgroup_path = os.path.join(self.cgroup_root, f"allow_{app_name}")
        if os.path.exists(cgroup_path):
            try:
                shutil.rmtree(cgroup_path)
            except Exception:
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
        self.apply_rules()

    def register_throttle_class(self, cgroup_name: str, class_id: int) -> None:
        """Register a dynamic throttle class with the firewall so outbound packets are marked."""
        self.throttled_classes[cgroup_name] = class_id
        # Create matching cgroup path (relative to /sys/fs/cgroup/ is netctl/throttle_<class_id>)
        cgroup_path = os.path.join(self.cgroup_root, f"throttle_{class_id}")
        os.makedirs(cgroup_path, exist_ok=True)
        self.apply_rules()

    def unregister_throttle_class(self, cgroup_name: str) -> None:
        """Remove a dynamic throttle class marking rule."""
        if cgroup_name in self.throttled_classes:
            class_id = self.throttled_classes[cgroup_name]
            del self.throttled_classes[cgroup_name]
            cgroup_path = os.path.join(self.cgroup_root, f"throttle_{class_id}")
            if os.path.exists(cgroup_path):
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
            self.apply_rules()

    def apply_rules(self) -> None:
        """Recompile and apply nftables configuration atomically by flushing and rebuilding the chain."""
        # Get relative cgroup path from /sys/fs/cgroup
        rel_cgroup = os.path.relpath(self.cgroup_root, "/sys/fs/cgroup")
        
        # 1. Flush output chain
        subprocess.run(["nft", "flush", "chain", "inet", self.table_name, "output"], check=True)
        
        # 2. Add local interfaces accept rules
        subprocess.run(["nft", "add", "rule", "inet", self.table_name, "output", "oifname", "lo", "accept"], check=True)
        
        # 3. Add dynamic mark rules for throttling (Must be executed before drops or accepts)
        for cgroup_name, class_id in self.throttled_classes.items():
            # If default deny is enabled, we mark AND accept. If not, we just mark.
            accept_suffix = " accept" if self.default_deny else ""
            rule_cmd = [
                "nft", "add", "rule", "inet", self.table_name, "output",
                "socket", "cgroupv2", "level", "2", f"{rel_cgroup}/throttle_{class_id}",
                "meta", "mark", "set", str(class_id) + accept_suffix
            ]
            subprocess.run(rule_cmd, check=True)

        if self.default_deny:
            # ALLOWLIST MODE
            # Accept DHCP client outbound (port 67)
            subprocess.run(["nft", "add", "rule", "inet", self.table_name, "output", "udp", "dport", "67", "accept"], check=True)
            # Accept DNS queries outbound (port 53)
            subprocess.run(["nft", "add", "rule", "inet", self.table_name, "output", "udp", "dport", "53", "accept"], check=True)
            subprocess.run(["nft", "add", "rule", "inet", self.table_name, "output", "tcp", "dport", "53", "accept"], check=True)
            
            # Allow specific applications
            for app in self.allowed_apps:
                subprocess.run([
                    "nft", "add", "rule", "inet", self.table_name, "output",
                    "socket", "cgroupv2", "level", "2", f"{rel_cgroup}/allow_{app}", "accept"
                ], check=True)
                
            # Drop everything else
            subprocess.run(["nft", "add", "rule", "inet", self.table_name, "output", "drop"], check=True)
        else:
            # BLOCKLIST MODE
            # Drop specific applications
            for app in self.blocked_apps:
                subprocess.run([
                    "nft", "add", "rule", "inet", self.table_name, "output",
                    "socket", "cgroupv2", "level", "2", f"{rel_cgroup}/block_{app}", "drop"
                ], check=True)

    def get_cgroup_for_app(self, app_name: str, policy: str) -> str:
        """Get the relative cgroup path for an application and its policy."""
        rel_cgroup = os.path.relpath(self.cgroup_root, "/sys/fs/cgroup")
        if policy == "block":
            return f"{rel_cgroup}/block_{app_name}"
        elif policy == "allow":
            return f"{rel_cgroup}/allow_{app_name}"
        elif policy == "limit":
            # For limits, the cgroup is associated with the class ID
            class_id = self.throttled_classes.get(f"throttle_{app_name}", 0)
            return f"{rel_cgroup}/throttle_{class_id}"
        return rel_cgroup

    def cleanup(self) -> None:
        """Reset the firewall by deleting the netctl table on shutdown."""
        logger.info("Cleaning up nftables firewall configurations...")
        subprocess.run(["nft", "delete", "table", "inet", self.table_name], check=False)
        
        # Clean cgroup directories using rmdir on leaf folders
        for app in list(self.blocked_apps):
            cgroup_path = os.path.join(self.cgroup_root, f"block_{app}")
            if os.path.exists(cgroup_path):
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
        for app in list(self.allowed_apps):
            cgroup_path = os.path.join(self.cgroup_root, f"allow_{app}")
            if os.path.exists(cgroup_path):
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
        for cgroup_name, class_id in list(self.throttled_classes.items()):
            cgroup_path = os.path.join(self.cgroup_root, f"throttle_{class_id}")
            if os.path.exists(cgroup_path):
                try:
                    os.rmdir(cgroup_path)
                except Exception:
                    pass
                    
        # Remove parent cgroup root
        if os.path.exists(self.cgroup_root):
            try:
                os.rmdir(self.cgroup_root)
            except Exception:
                pass
        logger.info("Firewall cleaned.")
