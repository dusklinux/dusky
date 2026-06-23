import subprocess
import os
from typing import Optional, Dict, Set, Tuple

from src.utils import logger, get_default_interface, normalize_rate

class TrafficShaper:
    """Manages Linux traffic control (tc) HTB hierarchy for egress, and direct policing on ingress (no IFB required)."""
    
    def __init__(self, interface: Optional[str] = None):
        self.interface = interface or get_default_interface()
        self.active_port_filters: Set[Tuple[int, int]] = set() # Set of (port, class_id)
        self.class_counter = 10  # Starting class ID for dynamic throttling classes
        self.cgroup_to_class: Dict[str, int] = {} # cgroup_name -> class_id
        # Ingress policing rates
        self.class_down_rates: Dict[int, str] = {} # class_id -> rate

    def init_qdiscs(self) -> None:
        """Initialize the HTB disciplines on egress and ingress qdisc on the interface."""
        logger.info(f"Initializing traffic shaping qdiscs on interface {self.interface}...")
        
        # Remove any existing root and ingress qdiscs to start clean
        subprocess.run(["tc", "qdisc", "del", "dev", self.interface, "root"], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["tc", "qdisc", "del", "dev", self.interface, "ingress"], check=False, stderr=subprocess.DEVNULL)
        
        # 1. Egress (outbound limit hierarchy)
        # Default traffic goes to class 1:2
        subprocess.run(["tc", "qdisc", "add", "dev", self.interface, "root", "handle", "1:", "htb", "default", "2"], check=True)
        # Parent class (1:1) has a high speed fallback (can be restricted for global output limit)
        subprocess.run(["tc", "class", "add", "dev", self.interface, "parent", "1:", "classid", "1:1", "htb", "rate", "10gbps"], check=True)
        # Default unthrottled class (1:2)
        subprocess.run(["tc", "class", "add", "dev", self.interface, "parent", "1:1", "classid", "1:2", "htb", "rate", "10gbps", "ceil", "10gbps"], check=True)
        subprocess.run(["tc", "qdisc", "add", "dev", self.interface, "parent", "1:2", "handle", "2:", "fq_codel"], check=True)
        
        # 2. Ingress (inbound direct policing setup)
        subprocess.run(["tc", "qdisc", "add", "dev", self.interface, "handle", "ffff:", "ingress"], check=True)
        
        logger.info("Traffic shaper qdiscs initialized successfully (Direct Ingress Policing Mode).")

    def set_global_limits(self, limit_down: Optional[str], limit_up: Optional[str]) -> None:
        """Update global rate limits. Egress is updated via HTB class 1:1, ingress is updated via direct policing filter."""
        # Egress (outbound)
        rate_up = normalize_rate(limit_up) if limit_up else "10gbps"
        cmd_up = ["tc", "class", "change", "dev", self.interface, "parent", "1:", "classid", "1:1", "htb", "rate", rate_up]
        res_up = subprocess.run(cmd_up, capture_output=True, text=True)
        if res_up.returncode != 0:
            logger.error(f"Failed to set global upload limit: {res_up.stderr}")
            
        # Ingress (inbound global police filter)
        # Clear existing global ingress filter (prio 49999)
        subprocess.run(["tc", "filter", "del", "dev", self.interface, "parent", "ffff:", "protocol", "ip", "prio", "49999"], check=False, stderr=subprocess.DEVNULL)
        
        if limit_down:
            rate_down = normalize_rate(limit_down)
            # Add ingress policing filter to match all traffic (u32 match u32 0 0)
            cmd_down = [
                "tc", "filter", "add", "dev", self.interface, "parent", "ffff:", "protocol", "ip", "prio", "49999",
                "u32", "match", "u32", "0", "0", "police", "rate", rate_down, "burst", "100k", "drop"
            ]
            res_down = subprocess.run(cmd_down, capture_output=True, text=True)
            if res_down.returncode != 0:
                logger.error(f"Failed to set global download limit: {res_down.stderr}")
            
        logger.info(f"Global limits updated: Down={limit_down or 'unlimited'}, Up={limit_up or 'unlimited'}")

    def add_throttle_class(self, cgroup_name: str, rate_down: Optional[str], rate_up: Optional[str]) -> int:
        """Add or update an HTB class for a throttled application / cgroup egress, and register ingress rate."""
        if cgroup_name in self.cgroup_to_class:
            class_id = self.cgroup_to_class[cgroup_name]
            self.update_throttle_class(class_id, rate_down, rate_up)
            return class_id
            
        class_id = self.class_counter
        self.class_counter += 1
        self.cgroup_to_class[cgroup_name] = class_id
        
        # 1. Egress (outbound class)
        r_up = normalize_rate(rate_up) if rate_up else "10gbps"
        subprocess.run([
            "tc", "class", "add", "dev", self.interface, "parent", "1:1", "classid", f"1:{class_id}", "htb", "rate", r_up, "ceil", r_up
        ], check=True)
        subprocess.run(["tc", "qdisc", "add", "dev", self.interface, "parent", f"1:{class_id}", "handle", f"{class_id}:", "fq_codel"], check=False)
        # Classify packets matching packet mark equal to class_id
        subprocess.run([
            "tc", "filter", "add", "dev", self.interface, "protocol", "ip", "parent", "1:", "prio", "1", 
            "handle", str(class_id), "fw", "classid", f"1:{class_id}"
        ], check=True)
        
        # 2. Ingress (register inbound rate for dynamic port policing)
        self.class_down_rates[class_id] = rate_down if rate_down else "10gbps"
        
        logger.info(f"Added throttle class 1:{class_id} for cgroup {cgroup_name} (Down: {rate_down or 'unlimited'}, Up: {rate_up or 'unlimited'})")
        return class_id

    def update_throttle_class(self, class_id: int, rate_down: Optional[str], rate_up: Optional[str]) -> None:
        """Update bandwidth speeds dynamically on an active throttle class."""
        if rate_up:
            r_up = normalize_rate(rate_up)
            subprocess.run([
                "tc", "class", "change", "dev", self.interface, "parent", "1:1", "classid", f"1:{class_id}", "htb", "rate", r_up, "ceil", r_up
            ], check=True)
        if rate_down:
            self.class_down_rates[class_id] = rate_down
            # Re-apply any active port filters for this class with new rate
            ports_to_update = [p for p, cid in self.active_port_filters if cid == class_id]
            for port in ports_to_update:
                self.remove_inbound_port_filter(port)
                self.add_inbound_port_filter(port, class_id)

    def remove_throttle_class(self, cgroup_name: str) -> None:
        """Remove a dynamic throttle class and its associated port filters."""
        if cgroup_name not in self.cgroup_to_class:
            return
            
        class_id = self.cgroup_to_class[cgroup_name]
        del self.cgroup_to_class[cgroup_name]
        if class_id in self.class_down_rates:
            del self.class_down_rates[class_id]
            
        # Remove any dynamic port filters assigned to this class
        ports_to_remove = [p for p, cid in self.active_port_filters if cid == class_id]
        for port in ports_to_remove:
            self.remove_inbound_port_filter(port)
            
        # Delete tc egress filter and class
        subprocess.run(["tc", "filter", "del", "dev", self.interface, "protocol", "ip", "parent", "1:", "prio", "1", "handle", str(class_id), "fw"], check=False)
        subprocess.run(["tc", "class", "del", "dev", self.interface, "classid", f"1:{class_id}"], check=False)
        
        logger.info(f"Removed throttle class 1:{class_id} for cgroup {cgroup_name}")

    def add_inbound_port_filter(self, port: int, class_id: int) -> None:
        """Dynamically add an ingress police filter matching local destination port on physical interface."""
        filter_key = (port, class_id)
        if filter_key in self.active_port_filters:
            return
            
        rate_down_str = self.class_down_rates.get(class_id, "10gbps")
        rate_down = normalize_rate(rate_down_str)
        
        cmd = [
            "tc", "filter", "add", "dev", self.interface, "parent", "ffff:", "protocol", "ip", "prio", "1",
            "u32", "match", "ip", "dport", str(port), "0xffff", "police", "rate", rate_down, "burst", "50k", "drop"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            self.active_port_filters.add(filter_key)
        else:
            # Silence port conflicts
            pass

    def remove_inbound_port_filter(self, port: int) -> None:
        """Remove a dynamic port ingress police filter."""
        matches = [f for f in self.active_port_filters if f[0] == port]
        for m in matches:
            cmd = [
                "tc", "filter", "del", "dev", self.interface, "parent", "ffff:", "protocol", "ip", "prio", "1",
                "u32", "match", "ip", "dport", str(port), "0xffff"
            ]
            subprocess.run(cmd, check=False)
            self.active_port_filters.remove(m)

    def cleanup(self) -> None:
        """Reset all traffic shaping queues and filters to restore default settings on shutdown."""
        logger.info("Cleaning up traffic control qdiscs and filters...")
        
        # Delete all active port filters
        for port, _ in list(self.active_port_filters):
            self.remove_inbound_port_filter(port)
            
        # Delete all throttled classes
        for class_id in list(self.cgroup_to_class.values()):
            subprocess.run(["tc", "filter", "del", "dev", self.interface, "protocol", "ip", "parent", "1:", "prio", "1", "handle", str(class_id), "fw"], check=False)
            subprocess.run(["tc", "class", "del", "dev", self.interface, "classid", f"1:{class_id}"], check=False)
            
        self.cgroup_to_class.clear()
        self.class_down_rates.clear()
        
        # Remove root and ingress qdiscs
        subprocess.run(["tc", "qdisc", "del", "dev", self.interface, "root"], check=False, stderr=subprocess.DEVNULL)
        subprocess.run(["tc", "qdisc", "del", "dev", self.interface, "ingress"], check=False, stderr=subprocess.DEVNULL)
        
        logger.info("Traffic control configurations cleaned.")
