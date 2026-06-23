import asyncio
import sys
import argparse
import re
from typing import List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import ProgressBar

from src.ipc import IPCClient
from src.utils import format_bytes, format_rate, BRAND_NAME

console = Console()

def is_rate_string(value: str) -> bool:
    """Helper to detect if a command line token represents a bandwidth speed rate."""
    return bool(re.match(r"^\d+(?:bit|bps|kbit|kbps|mbit|mbps|gbit|gbps|k|m|g)?$", value.lower()))

async def run_cli() -> None:
    parser = argparse.ArgumentParser(description=f"{BRAND_NAME} CLI Control Interface")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # 1. LIMIT command
    limit_parser = subparsers.add_parser("limit", help="Apply global or per-application bandwidth limits")
    limit_parser.add_argument("target_or_rate", nargs="?", help="Bandwidth rate (global) or application name (per-app)")
    limit_parser.add_argument("rate", nargs="?", help="Bandwidth rate (if application name specified first)")
    limit_parser.add_argument("--down", help="Download rate limit (e.g., 20mbit)")
    limit_parser.add_argument("--up", help="Upload rate limit (e.g., 5mbit)")
    limit_parser.add_argument("--type", choices=["process", "executable", "user"], default="process", 
                              help="Rule match granularity (default: process name)")
    
    # 2. BLOCK command
    block_parser = subparsers.add_parser("block", help="Block specific applications from accessing the network")
    block_parser.add_argument("app", help="Application name, executable path, or user")
    block_parser.add_argument("--type", choices=["process", "executable", "user"], default="process", help="Target match granularity")
    
    # 3. ALLOW command
    allow_parser = subparsers.add_parser("allow", help="Allow specific applications (or remove limits/blocks)")
    allow_parser.add_argument("app", help="Application name, executable path, or user")
    allow_parser.add_argument("--type", choices=["process", "executable", "user"], default="process", help="Target match granularity")
    
    # 4. STATS command
    stats_parser = subparsers.add_parser("stats", help="Show live network usage statistics")
    
    # 5. TOP command
    top_parser = subparsers.add_parser("top", help="Show top applications by accumulated historical usage")
    
    # 6. REPORT command
    report_parser = subparsers.add_parser("report", help="Generate historical bandwidth usage report")
    report_parser.add_argument("period", choices=["day", "week", "month", "year"], help="Report period")
    
    # 7. QUOTA command
    quota_parser = subparsers.add_parser("quota", help="Set network bandwidth consumption quotas")
    quota_parser.add_argument("limit", help="Data quota size (e.g., 50GB, 500MB)")
    quota_parser.add_argument("--period", choices=["daily", "weekly", "monthly"], default="monthly", help="Quota cycle period")
    quota_parser.add_argument("--warning", type=float, default=0.8, help="Warning notification threshold (default: 0.8)")
    
    # 8. QUOTA-STATUS command
    subparsers.add_parser("quota-status", help="Show current status of all quota cycles")
    
    # 9. RESET-QUOTAS command
    subparsers.add_parser("reset-quotas", help="Reset all quota warnings and block limits")
    
    args = parser.parse_args()
    client = IPCClient()
    
    # RPC Execution Router
    try:
        if args.command == "limit":
            await handle_limit(client, args)
        elif args.command == "block":
            await handle_block(client, args)
        elif args.command == "allow":
            await handle_allow(client, args)
        elif args.command == "stats":
            await handle_stats(client)
        elif args.command == "top":
            await handle_top(client)
        elif args.command == "report":
            await handle_report(client, args.period)
        elif args.command == "quota":
            await handle_quota(client, args)
        elif args.command == "quota-status":
            await handle_quota_status(client)
        elif args.command == "reset-quotas":
            await handle_reset_quotas(client)
    except Exception as e:
        console.print(f"[bold red]CLI execution error: {e}[/bold red]")
        sys.exit(1)

async def handle_limit(client: IPCClient, args: Any) -> None:
    limit_down = args.down
    limit_up = args.up
    
    # Determine if global or per-app limit
    target = args.target_or_rate
    rate = args.rate
    
    if not target and not limit_down and not limit_up:
        console.print("[bold red]Error: No limits or rates specified. Use 'netctl limit 20mbit' or 'netctl limit --down 20mbit'.[/bold red]")
        return
        
    is_global = False
    
    # Case 1: 'netctl limit 20mbit' or similar
    if target and is_rate_string(target):
        is_global = True
        limit_down = target
        limit_up = target
    # Case 2: 'netctl limit --down 20mbit --up 5mbit'
    elif not target:
        is_global = True
        
    if is_global:
        # Global limit
        res = await client.send_request("set_global_limit", {
            "limit_down": limit_down,
            "limit_up": limit_up
        })
        if res.get("status") == "ok":
            console.print(f"[bold green]Global bandwidth limits updated successfully: Down={limit_down or 'unlimited'}, Up={limit_up or 'unlimited'}.[/bold green]")
        else:
            console.print(f"[bold red]Failed to set global limits: {res.get('error')}[/bold red]")
    else:
        # Per-application limit: 'netctl limit firefox 5mbit' or similar
        app_rate = rate or limit_down or limit_up
        if not app_rate:
            console.print(f"[bold red]Error: No limit rate specified for app '{target}'.[/bold red]")
            return
            
        rate_val = app_rate if is_rate_string(app_rate) else "10gbps"
        final_down = limit_down or rate_val
        final_up = limit_up or rate_val
        
        res = await client.send_request("add_rule", {
            "target_type": args.type,
            "target_value": target,
            "policy": "limit",
            "limit_down": final_down,
            "limit_up": final_up
        })
        if res.get("status") == "ok":
            console.print(f"[bold green]Bandwidth limit applied to application '{target}': Down={final_down}, Up={final_up}.[/bold green]")
        else:
            console.print(f"[bold red]Failed to apply limit: {res.get('error')}[/bold red]")

async def handle_block(client: IPCClient, args: Any) -> None:
    res = await client.send_request("add_rule", {
        "target_type": args.type,
        "target_value": args.app,
        "policy": "block"
    })
    if res.get("status") == "ok":
        console.print(f"[bold green]Application '{args.app}' blocked from accessing the network.[/bold green]")
    else:
        console.print(f"[bold red]Failed to block application: {res.get('error')}[/bold red]")

async def handle_allow(client: IPCClient, args: Any) -> None:
    # Allow command acts as a whitelist addition (if default deny is active)
    # or it cleans up block/limit rules. Let's do both:
    # First: check if we are in default deny
    status_res = await client.send_request("get_status")
    default_deny = False
    if status_res.get("status") == "ok":
        default_deny = status_res["result"]["firewall_default_deny"]
        
    if default_deny:
        res = await client.send_request("add_rule", {
            "target_type": args.type,
            "target_value": args.app,
            "policy": "allow"
        })
    else:
        res = await client.send_request("remove_rule", {
            "target_value": args.app
        })
        
    if res.get("status") == "ok":
        if default_deny:
            console.print(f"[bold green]Application '{args.app}' added to firewall allowlist.[/bold green]")
        else:
            console.print(f"[bold green]Restrictions removed for application '{args.app}'.[/bold green]")
    else:
        console.print(f"[bold red]Failed to alter rules: {res.get('error')}[/bold red]")

async def handle_stats(client: IPCClient) -> None:
    res = await client.send_request("get_live_rates")
    if res.get("status") != "ok":
        console.print(f"[bold red]Failed to fetch statistics: {res.get('error')}[/bold red]")
        return
        
    processes = res.get("result", [])
    if not processes:
        console.print("[yellow]No active network traffic detected.[/yellow]")
        return
        
    table = Table(title="Live Network Traffic Stats")
    table.add_column("PID", style="cyan", justify="right")
    table.add_column("Command", style="green")
    table.add_column("User", style="magenta")
    table.add_column("Upload", style="blue", justify="right")
    table.add_column("Download", style="blue", justify="right")
    table.add_column("Conns", style="yellow", justify="right")
    
    for p in sorted(processes, key=lambda x: x["rate_sent"] + x["rate_recv"], reverse=True):
        table.add_row(
            str(p["pid"]),
            p["comm"],
            p["username"],
            format_rate(p["rate_sent"]),
            format_rate(p["rate_recv"]),
            str(len(p["connections"]))
        )
        
    console.print(table)

async def handle_top(client: IPCClient) -> None:
    res = await client.send_request("get_top_talkers")
    if res.get("status") != "ok":
        console.print(f"[bold red]Failed to fetch historical top talkers: {res.get('error')}[/bold red]")
        return
        
    talkers = res.get("result", [])
    if not talkers:
        console.print("[yellow]No historical statistics available in database.[/yellow]")
        return
        
    table = Table(title="Top Talkers (Historical)")
    table.add_column("Application", style="green")
    table.add_column("Executable Path", style="dim")
    table.add_column("Sent", style="cyan", justify="right")
    table.add_column("Received", style="cyan", justify="right")
    table.add_column("Total Accumulated", style="bold magenta", justify="right")
    
    for t in talkers:
        table.add_row(
            t["comm"],
            t["exe"],
            format_bytes(t["bytes_sent"]),
            format_bytes(t["bytes_recv"]),
            format_bytes(t["total_bytes"])
        )
        
    console.print(table)

async def handle_report(client: IPCClient, period: str) -> None:
    res = await client.send_request("get_historical_report", {"period": period})
    if res.get("status") != "ok":
        console.print(f"[bold red]Failed to fetch report: {res.get('error')}[/bold red]")
        return
        
    report = res.get("result", {})
    labels = report.get("labels", [])
    sent = report.get("bytes_sent", [])
    recv = report.get("bytes_recv", [])
    
    if not labels:
        console.print("[yellow]No data available for the requested period.[/yellow]")
        return
        
    console.print(Panel(f"[bold magenta]Historical Bandwidth Report - Last {period.capitalize()}[/bold magenta]\n"
                        f"Total Sent: {format_bytes(report['total_sent'])} | "
                        f"Total Received: {format_bytes(report['total_recv'])}"))
                        
    # Draw simple textual bar charts
    max_val = max(max(sent) if sent else 1, max(recv) if recv else 1)
    
    table = Table(box=None, padding=(0, 2))
    table.add_column("Date/Time", style="dim")
    table.add_column("Upload/Download Visual Bars", width=50)
    table.add_column("Sent/Recv", style="bold")
    
    for l, s, r in zip(labels, sent, recv):
        # Scale bars to 20 chars max
        s_bar = "█" * int((s / max_val) * 20)
        r_bar = "▒" * int((r / max_val) * 20)
        
        table.add_row(
            l,
            f"[blue]{s_bar}[/blue]\n[green]{r_bar}[/green]",
            f"S: {format_bytes(s)}\nR: {format_bytes(r)}"
        )
        
    console.print(table)

async def handle_quota(client: IPCClient, args: Any) -> None:
    # Parse quota limit bytes
    match = re.match(r"^(\d+)\s*([a-zA-Z]+)$", args.limit.strip())
    if not match:
        console.print("[bold red]Error: Invalid quota format. Use e.g. '50GB', '500MB'.[/bold red]")
        return
        
    num, unit = float(match.group(1)), match.group(2).lower()
    units = {'b': 1, 'kb': 1024, 'mb': 1024**2, 'gb': 1024**3, 'tb': 1024**4, 'k': 1024, 'm': 1024**2, 'g': 1024**3, 't': 1024**4}
    if unit not in units:
        console.print(f"[bold red]Error: Unknown unit '{unit}'.[/bold red]")
        return
        
    limit_bytes = int(num * units[unit])
    
    res = await client.send_request("set_quota", {
        "period": args.period,
        "limit_bytes": limit_bytes,
        "warning_threshold": args.warning
    })
    
    if res.get("status") == "ok":
        console.print(f"[bold green]Quota successfully configured: {args.period} limit set to {args.limit} (warns at {args.warning*100:.0f}%).[/bold green]")
    else:
        console.print(f"[bold red]Failed to set quota: {res.get('error')}[/bold red]")

async def handle_quota_status(client: IPCClient) -> None:
    res = await client.send_request("get_quota_status")
    if res.get("status") != "ok":
        console.print(f"[bold red]Failed to query quota status: {res.get('error')}[/bold red]")
        return
        
    quotas = res.get("result", [])
    if not quotas:
        console.print("[yellow]No quotas configured on system.[/yellow]")
        return
        
    for q in quotas:
        period = q["period"].upper()
        limit = q["limit_bytes"]
        used = q["used_bytes"]
        blocked = q["blocked"]
        
        pct = (used / limit) if limit > 0 else 0
        color = "red" if pct >= 1.0 or blocked else ("yellow" if pct >= 0.8 else "green")
        
        console.print(f"\n[bold]{period} QUOTA STATUS[/bold]")
        console.print(f"Limit: {format_bytes(limit)}")
        console.print(f"Used:  {format_bytes(used)} ({pct*100:.1f}%)")
        
        # Draw progress bar
        bar = ProgressBar(total=100, completed=min(100, int(pct * 100)))
        console.print(bar)
        
        if blocked:
            console.print("[bold red]STATUS: BLOCKED / RESTRICTED (Quota Exceeded)[/bold red]")
        else:
            console.print(f"[bold {color}]STATUS: ACTIVE[/bold {color}]")

async def handle_reset_quotas(client: IPCClient) -> None:
    res = await client.send_request("reset_quotas")
    if res.get("status") == "ok":
        console.print("[bold green]Quota warnings and block limits reset successfully.[/bold green]")
    else:
        console.print(f"[bold red]Failed to reset quotas: {res.get('error')}[/bold red]")

def main():
    asyncio.run(run_cli())

if __name__ == "__main__":
    main()
