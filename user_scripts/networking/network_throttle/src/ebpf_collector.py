import os
import struct
from datetime import datetime, UTC
import socket
import threading
import time
import pwd
import psutil
from typing import Dict, List, Tuple, Any, Optional
from bcc import BPF

from src.utils import logger

# BPF Program in C
BPF_SOURCE = """
#define _Static_assert(...)
#define static_assert(...)
#define BPF_TRACE_FSESSION 0
#define BPF_F_CPU 0
#define BPF_F_ALL_CPUS 0

#define _LINUX_NS_COMMON_H
struct ns_common {
    unsigned int inum;
    unsigned long long ns_id;
};
#define ns_ref_inc(...)
#define ns_ref_put(...) 0
#define ns_ref_get(...) 0
#define ns_ref_read(...) 0

#include <uapi/linux/ptrace.h>
#include <net/sock.h>
#include <bcc/proto.h>
#include <linux/in.h>
#include <linux/in6.h>

#define AF_INET 2
#define AF_INET6 10

struct key_t {
    u32 pid;
    u32 uid;
    u64 cgroup_id;
    u32 saddr[4];
    u32 daddr[4];
    u16 sport;
    u16 dport;
    u8 proto;
    u8 ip_version;
    char comm[16];
};

struct val_t {
    u64 bytes_sent;
    u64 bytes_recv;
    u64 packets_sent;
    u64 packets_recv;
};

// Main map to aggregate traffic statistics
BPF_HASH(stats, struct key_t, struct val_t);

// Temporary storage to track socket pointers during UDP receive calls
BPF_HASH(udp_recv_sk, u64, struct sock *);

// Trace TCP sends
int kprobe__tcp_sendmsg(struct pt_regs *ctx) {
    struct sock *sk = (struct sock *)PT_REGS_PARM1(ctx);
    struct msghdr *msg = (struct msghdr *)PT_REGS_PARM2(ctx);
    size_t size = (size_t)PT_REGS_PARM3(ctx);

    u16 family = 0;
    bpf_probe_read_kernel(&family, sizeof(family), &sk->__sk_common.skc_family);
    if (family != AF_INET && family != AF_INET6) return 0;

    struct key_t key = {};
    key.pid = bpf_get_current_pid_tgid() >> 32;
    key.uid = bpf_get_current_uid_gid();
    key.cgroup_id = bpf_get_current_cgroup_id();
    key.proto = 6; // TCP
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    if (family == AF_INET) {
        key.ip_version = 4;
        bpf_probe_read_kernel(&key.saddr[0], sizeof(u32), &sk->__sk_common.skc_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], sizeof(u32), &sk->__sk_common.skc_daddr);
    } else {
        key.ip_version = 6;
        bpf_probe_read_kernel(&key.saddr[0], 16, &sk->__sk_common.skc_v6_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], 16, &sk->__sk_common.skc_v6_daddr);
    }

    u16 sport = 0;
    u16 dport = 0;
    bpf_probe_read_kernel(&sport, sizeof(sport), &sk->__sk_common.skc_num);
    bpf_probe_read_kernel(&dport, sizeof(dport), &sk->__sk_common.skc_dport);
    
    key.sport = sport;
    key.dport = __builtin_bswap16(dport);

    struct val_t *val = stats.lookup(&key);
    if (val) {
        val->bytes_sent += size;
        val->packets_sent += 1;
    } else {
        struct val_t new_val = {};
        new_val.bytes_sent = size;
        new_val.packets_sent = 1;
        stats.update(&key, &new_val);
    }
    return 0;
}

// Trace TCP receives
int kprobe__tcp_cleanup_rbuf(struct pt_regs *ctx) {
    struct sock *sk = (struct sock *)PT_REGS_PARM1(ctx);
    int copied = (int)PT_REGS_PARM2(ctx);
    if (copied <= 0) return 0;
    
    u16 family = 0;
    bpf_probe_read_kernel(&family, sizeof(family), &sk->__sk_common.skc_family);
    if (family != AF_INET && family != AF_INET6) return 0;

    struct key_t key = {};
    key.pid = bpf_get_current_pid_tgid() >> 32;
    key.uid = bpf_get_current_uid_gid();
    key.cgroup_id = bpf_get_current_cgroup_id();
    key.proto = 6; // TCP
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    if (family == AF_INET) {
        key.ip_version = 4;
        bpf_probe_read_kernel(&key.saddr[0], sizeof(u32), &sk->__sk_common.skc_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], sizeof(u32), &sk->__sk_common.skc_daddr);
    } else {
        key.ip_version = 6;
        bpf_probe_read_kernel(&key.saddr[0], 16, &sk->__sk_common.skc_v6_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], 16, &sk->__sk_common.skc_v6_daddr);
    }

    u16 sport = 0;
    u16 dport = 0;
    bpf_probe_read_kernel(&sport, sizeof(sport), &sk->__sk_common.skc_num);
    bpf_probe_read_kernel(&dport, sizeof(dport), &sk->__sk_common.skc_dport);
    
    key.sport = sport;
    key.dport = __builtin_bswap16(dport);

    struct val_t *val = stats.lookup(&key);
    if (val) {
        val->bytes_recv += copied;
        val->packets_recv += 1;
    } else {
        struct val_t new_val = {};
        new_val.bytes_recv = copied;
        new_val.packets_recv = 1;
        stats.update(&key, &new_val);
    }
    return 0;
}

// Trace UDP sends
int kprobe__udp_sendmsg(struct pt_regs *ctx) {
    struct sock *sk = (struct sock *)PT_REGS_PARM1(ctx);
    size_t len = (size_t)PT_REGS_PARM3(ctx);

    u16 family = 0;
    bpf_probe_read_kernel(&family, sizeof(family), &sk->__sk_common.skc_family);
    if (family != AF_INET && family != AF_INET6) return 0;

    struct key_t key = {};
    key.pid = bpf_get_current_pid_tgid() >> 32;
    key.uid = bpf_get_current_uid_gid();
    key.cgroup_id = bpf_get_current_cgroup_id();
    key.proto = 17; // UDP
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    u16 sport = 0;
    u16 dport = 0;
    bpf_probe_read_kernel(&sport, sizeof(sport), &sk->__sk_common.skc_num);
    bpf_probe_read_kernel(&dport, sizeof(dport), &sk->__sk_common.skc_dport);
    key.sport = sport;
    key.dport = __builtin_bswap16(dport);

    if (family == AF_INET) {
        key.ip_version = 4;
        bpf_probe_read_kernel(&key.saddr[0], sizeof(u32), &sk->__sk_common.skc_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], sizeof(u32), &sk->__sk_common.skc_daddr);
    } else {
        key.ip_version = 6;
        bpf_probe_read_kernel(&key.saddr[0], 16, &sk->__sk_common.skc_v6_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], 16, &sk->__sk_common.skc_v6_daddr);
    }

    struct val_t *val = stats.lookup(&key);
    if (val) {
        val->bytes_sent += len;
        val->packets_sent += 1;
    } else {
        struct val_t new_val = {};
        new_val.bytes_sent = len;
        new_val.packets_sent = 1;
        stats.update(&key, &new_val);
    }
    return 0;
}

// Track UDP receives
int kprobe__udp_recvmsg(struct pt_regs *ctx) {
    struct sock *sk = (struct sock *)PT_REGS_PARM1(ctx);
    u64 pid_tgid = bpf_get_current_pid_tgid();
    udp_recv_sk.update(&pid_tgid, &sk);
    return 0;
}

int kretprobe__udp_recvmsg(struct pt_regs *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    struct sock **sk_pp = udp_recv_sk.lookup(&pid_tgid);
    if (!sk_pp) return 0;
    
    struct sock *sk = *sk_pp;
    udp_recv_sk.delete(&pid_tgid);
    
    int copied = PT_REGS_RC(ctx);
    if (copied <= 0) return 0;
    
    u16 family = 0;
    bpf_probe_read_kernel(&family, sizeof(family), &sk->__sk_common.skc_family);
    if (family != AF_INET && family != AF_INET6) return 0;

    struct key_t key = {};
    key.pid = pid_tgid >> 32;
    key.uid = bpf_get_current_uid_gid();
    key.cgroup_id = bpf_get_current_cgroup_id();
    key.proto = 17; // UDP
    bpf_get_current_comm(&key.comm, sizeof(key.comm));

    if (family == AF_INET) {
        key.ip_version = 4;
        bpf_probe_read_kernel(&key.saddr[0], sizeof(u32), &sk->__sk_common.skc_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], sizeof(u32), &sk->__sk_common.skc_daddr);
    } else {
        key.ip_version = 6;
        bpf_probe_read_kernel(&key.saddr[0], 16, &sk->__sk_common.skc_v6_rcv_saddr);
        bpf_probe_read_kernel(&key.daddr[0], 16, &sk->__sk_common.skc_v6_daddr);
    }

    u16 sport = 0;
    u16 dport = 0;
    bpf_probe_read_kernel(&sport, sizeof(sport), &sk->__sk_common.skc_num);
    bpf_probe_read_kernel(&dport, sizeof(dport), &sk->__sk_common.skc_dport);
    
    key.sport = sport;
    key.dport = __builtin_bswap16(dport);

    struct val_t *val = stats.lookup(&key);
    if (val) {
        val->bytes_recv += copied;
        val->packets_recv += 1;
    } else {
        struct val_t new_val = {};
        new_val.bytes_recv = copied;
        new_val.packets_recv = 1;
        stats.update(&key, &new_val);
    }
    return 0;
}
"""

class DNSLogSniffer(threading.Thread):
    """Sniffs outbound DNS requests from a raw socket to log domain requests and relate to PIDs."""
    
    def __init__(self, ebpf_collector: 'EbpfCollector'):
        super().__init__(name="DNSLogSniffer", daemon=True)
        self.collector = ebpf_collector
        self.running = True
        self.dns_logs: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        
    def stop(self) -> None:
        self.running = False
        
    def get_and_clear_logs(self) -> List[Dict[str, Any]]:
        with self.lock:
            logs = self.dns_logs.copy()
            self.dns_logs.clear()
            return logs

    def run(self) -> None:
        try:
            # Capture raw network packets (protocol 3 is ETH_P_ALL)
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
        except PermissionError:
            logger.error("DNS Sniffer failed to open raw socket. Root privileges are required.")
            return
            
        while self.running:
            try:
                packet, _ = sock.recvfrom(65535)
                self.parse_ethernet_packet(packet)
            except Exception:
                pass

    def parse_ethernet_packet(self, packet: bytes) -> None:
        if len(packet) < 34:
            return
            
        eth_type = struct.unpack("!H", packet[12:14])[0]
        ip_offset = 14
        
        # IPv4
        if eth_type == 0x0800:
            self.parse_ipv4(packet, ip_offset)
            
    def parse_ipv4(self, packet: bytes, offset: int) -> None:
        if len(packet) < offset + 20:
            return
        iph = struct.unpack("!BBHHHBBHII", packet[offset:offset+20])
        version_ihl = iph[0]
        ihl = version_ihl & 0x0F
        iph_len = ihl * 4
        proto = iph[6]
        
        # Check if UDP (17)
        if proto == 17:
            udp_offset = offset + iph_len
            if len(packet) < udp_offset + 8:
                return
            udph = struct.unpack("!HHHH", packet[udp_offset:udp_offset+8])
            sport, dport, length, checksum = udph
            
            # DNS port is 53. Focus on outgoing queries (destination port 53)
            if dport == 53:
                dns_payload = packet[udp_offset + 8:]
                self.parse_dns_query(dns_payload, sport)

    def parse_dns_query(self, payload: bytes, sport: int) -> None:
        if len(payload) < 12:
            return
            
        headers = struct.unpack("!HHHHHH", payload[:12])
        qdcount = headers[2]
        
        if qdcount > 0:
            offset = 12
            # Decode query name
            labels = []
            while True:
                if offset >= len(payload):
                    return
                length = payload[offset]
                if length == 0:
                    offset += 1
                    break
                # Handle compression pointers (0xC0)
                if (length & 0xC0) == 0xC0:
                    offset += 2
                    break
                offset += 1
                label = payload[offset:offset+length].decode("utf-8", errors="ignore")
                labels.append(label)
                offset += length
                
            if offset + 4 <= len(payload):
                qtype_val, qclass_val = struct.unpack("!HH", payload[offset:offset+4])
                qtypes = {1: "A", 28: "AAAA", 5: "CNAME", 15: "MX", 16: "TXT", 2: "NS"}
                qtype = qtypes.get(qtype_val, str(qtype_val))
                query_domain = ".".join(labels)
                
                # Retrieve process metadata from ephemeral source port
                pid, comm = self.collector.resolve_port_to_pid(sport)
                
                log_entry = {
                    "timestamp": datetime.now(UTC).replace(tzinfo=None),
                    "pid": pid or 0,
                    "comm": comm or "unknown",
                    "query": query_domain,
                    "qtype": qtype
                }
                
                with self.lock:
                    self.dns_logs.append(log_entry)

class EbpfCollector:
    """Manages compilation, loading, and interface interactions with the BCC eBPF agent."""
    
    def __init__(self):
        self.bpf: Optional[BPF] = None
        self.dns_sniffer: Optional[DNSLogSniffer] = None
        self.port_to_pid_map: Dict[int, Tuple[int, str]] = {}
        self.lock = threading.Lock()
        self.usernames_cache: Dict[int, str] = {}
        self.exes_cache: Dict[int, str] = {}
        
    def start(self) -> None:
        """Load and compile the BCC eBPF program, hook kprobes, and spin up DNS sniffer."""
        logger.info("Compiling and loading eBPF probes...")
        try:
            self.bpf = BPF(text=BPF_SOURCE)
            # Attach probes
            self.bpf.attach_kprobe(event="tcp_sendmsg", fn_name="kprobe__tcp_sendmsg")
            self.bpf.attach_kprobe(event="tcp_cleanup_rbuf", fn_name="kprobe__tcp_cleanup_rbuf")
            self.bpf.attach_kprobe(event="udp_sendmsg", fn_name="kprobe__udp_sendmsg")
            self.bpf.attach_kprobe(event="udp_recvmsg", fn_name="kprobe__udp_recvmsg")
            self.bpf.attach_kretprobe(event="udp_recvmsg", fn_name="kretprobe__udp_recvmsg")
            logger.info("eBPF probes successfully loaded and attached.")
        except Exception as e:
            logger.error(f"Failed to initialize eBPF: {e}")
            raise e
            
        self.dns_sniffer = DNSLogSniffer(self)
        self.dns_sniffer.start()
        logger.info("DNS log sniffer started.")

    def stop(self) -> None:
        """Stop threads and release eBPF resources."""
        if self.dns_sniffer:
            self.dns_sniffer.stop()
            self.dns_sniffer.join()
        logger.info("eBPF Collector stopped.")

    def resolve_port_to_pid(self, sport: int) -> Tuple[Optional[int], Optional[str]]:
        """Lookup PID and command from local port."""
        with self.lock:
            return self.port_to_pid_map.get(sport, (None, None))

    def _get_username(self, uid: int) -> str:
        if uid not in self.usernames_cache:
            try:
                self.usernames_cache[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                self.usernames_cache[uid] = str(uid)
        return self.usernames_cache[uid]

    def _get_executable_path(self, pid: int) -> str:
        if pid not in self.exes_cache:
            try:
                p = psutil.Process(pid)
                self.exes_cache[pid] = p.exe()
            except Exception:
                self.exes_cache[pid] = "unknown"
        return self.exes_cache[pid]

    def harvest_stats(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Poll the eBPF maps and fetch logs from the DNS sniffer."""
        if self.bpf is None:
            return [], []
            
        stats_map = self.bpf["stats"]
        harvested_stats: List[Dict[str, Any]] = []
        
        # Save local port mappings for DNS queries
        new_port_map = {}
        
        keys_to_delete = []
        for key, val in stats_map.items():
            # Parse addresses
            if key.ip_version == 4:
                saddr = socket.inet_ntop(socket.AF_INET, struct.pack("<I", key.saddr[0]))
                daddr = socket.inet_ntop(socket.AF_INET, struct.pack("<I", key.daddr[0]))
            else:
                saddr = socket.inet_ntop(socket.AF_INET6, struct.pack("<IIII", key.saddr[0], key.saddr[1], key.saddr[2], key.saddr[3]))
                daddr = socket.inet_ntop(socket.AF_INET6, struct.pack("<IIII", key.daddr[0], key.daddr[1], key.daddr[2], key.daddr[3]))
                
            proto = "TCP" if key.proto == 6 else "UDP"
            comm = key.comm.decode("utf-8", errors="ignore")
            
            # Map source port for DNS sniffer
            if key.proto == 17 and key.dport == 53:
                new_port_map[key.sport] = (key.pid, comm)
                
            # Get cached properties
            username = self._get_username(key.uid)
            exe = self._get_executable_path(key.pid)
            
            stat_item = {
                "timestamp": datetime.now(UTC).replace(tzinfo=None),
                "pid": key.pid,
                "comm": comm,
                "exe": exe,
                "uid": key.uid,
                "username": username,
                "cgroup_path": f"/sys/fs/cgroup/netctl_pid_{key.pid}" if key.cgroup_id else "/sys/fs/cgroup",
                "bytes_sent": val.bytes_sent,
                "bytes_recv": val.bytes_recv,
                "packets_sent": val.packets_sent,
                "packets_recv": val.packets_recv,
                "protocol": proto,
                # Port and IP fields for live connections tracking
                "sport": key.sport,
                "dport": key.dport,
                "saddr": saddr,
                "daddr": daddr
            }
            
            harvested_stats.append(stat_item)
            keys_to_delete.append(key)

        # Safely remove keys to prevent map overflow and reset stats count
        for key in keys_to_delete:
            try:
                del stats_map[key]
            except KeyError:
                pass
                
        # Update our thread-safe port mapper
        with self.lock:
            # Merge and clean old maps (limit size to prevent memory leaks)
            if len(self.port_to_pid_map) > 5000:
                self.port_to_pid_map.clear()
            self.port_to_pid_map.update(new_port_map)
            
        # Clear caches periodically to handle terminated PIDs
        if len(self.exes_cache) > 2000:
            self.exes_cache.clear()
            
        dns_logs = []
        if self.dns_sniffer:
            dns_logs = self.dns_sniffer.get_and_clear_logs()
            
        return harvested_stats, dns_logs
