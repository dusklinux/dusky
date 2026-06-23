import asyncio
from typing import Dict, Any, List
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane, DataTable, Static, Label
from textual.containers import Grid, Horizontal, Vertical
from textual.reactive import reactive

from src.ipc import IPCClient
from src.utils import format_bytes, format_rate, BRAND_NAME

class DuskyTuiApp(App):
    """Modern Textual TUI for Dusky Network Limiter dashboard, process traffic viewer, and rule manager."""
    
    TITLE = BRAND_NAME
    SUB_TITLE = "Real-time Network Traffic Control Console"
    CSS = """
    Screen {
        background: #0f1419;
    }
    Header {
        background: #1f2430;
        color: #ffcc66;
        text-style: bold;
    }
    Footer {
        background: #1f2430;
        color: #cbccc6;
    }
    TabbedContent {
        background: #0f1419;
        margin-top: 1;
    }
    TabPane {
        padding: 1 2;
    }
    .metric-card {
        background: #151a24;
        border: solid #252a34;
        height: 6;
        content-align: center middle;
        text-align: center;
        margin: 1 1;
        padding: 1;
    }
    .metric-title {
        color: #707a8c;
        text-style: bold;
    }
    .metric-value {
        color: #ffcc66;
        text-style: bold;
        font-size: 110%;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit Console"),
        ("r", "refresh", "Force Refresh"),
        ("l", "limit_proc", "Limit App"),
        ("b", "block_proc", "Block App"),
    ]
    
    # Reactive properties for automatic UI updates
    total_up_rate = reactive("0.0 KB/s")
    total_down_rate = reactive("0.0 KB/s")
    active_conns_count = reactive("0")
    active_procs_count = reactive("0")
    
    def __init__(self):
        super().__init__()
        self.client = IPCClient()
        self.is_active = True

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Overview Dashboard"):
                with Grid(columns=4, rows=1):
                    with Vertical(classes="metric-card"):
                        yield Label("TOTAL UPLOAD SPEED", classes="metric-title")
                        yield Label(self.total_up_rate, id="lbl_total_up")
                    with Vertical(classes="metric-card"):
                        yield Label("TOTAL DOWNLOAD SPEED", classes="metric-title")
                        yield Label(self.total_down_rate, id="lbl_total_down")
                    with Vertical(classes="metric-card"):
                        yield Label("ACTIVE CONNECTIONS", classes="metric-title")
                        yield Label(self.active_conns_count, id="lbl_active_conns")
                    with Vertical(classes="metric-card"):
                        yield Label("TALKING PROCESSES", classes="metric-title")
                        yield Label(self.active_procs_count, id="lbl_active_procs")
                
                yield Static("\n[bold yellow]System Status & Gateway Information[/bold yellow]\n", id="status_panel")
                yield Static("Loading network throttle statistics...", id="global_rules_panel")
                
            with TabPane("Live Process Telemetry"):
                yield Label("Scroll and select processes. Shortcuts: [L] Limit Application | [B] Block Application")
                yield DataTable(id="process_table")
                
            with TabPane("Active Control Rules & Quotas"):
                yield Static("Rules List", id="rules_panel")
                yield Static("Quota Limits", id="quotas_panel")
                
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize UI grids and spawn polling loops."""
        # Initialize Process table columns
        table = self.query_one("#process_table", DataTable)
        table.cursor_type = "row"
        table.add_column("PID", width=8)
        table.add_column("Command name", width=18)
        table.add_column("System User", width=12)
        table.add_column("Upload Rate", width=15)
        table.add_column("Download Rate", width=15)
        table.add_column("Conns", width=8)
        
        # Start background polling timer (every 1 second)
        self.set_interval(1.0, self.refresh_data)

    async def refresh_data(self) -> None:
        """Fetch stats from daemon and update reactive TUI properties."""
        if not self.is_active:
            return
            
        # 1. Fetch live rates
        rates_res = await self.client.send_request("get_live_rates")
        # 2. Fetch system status
        status_res = await self.client.send_request("get_status")
        # 3. Fetch rules
        rules_res = await self.client.send_request("get_rules")
        # 4. Fetch quotas
        quota_res = await self.client.send_request("get_quota_status")
        
        if rates_res.get("status") != "ok":
            self.query_one("#global_rules_panel", Static).update(
                f"[bold red]Failed to connect to daemon: {rates_res.get('error')}[/bold red]"
            )
            return
            
        processes = rates_res.get("result", [])
        
        # Calculate totals
        tot_up = sum(p["rate_sent"] for p in processes)
        tot_down = sum(p["rate_recv"] for p in processes)
        tot_conns = sum(len(p["connections"]) for p in processes)
        
        self.total_up_rate = format_rate(tot_up)
        self.total_down_rate = format_rate(tot_down)
        self.active_conns_count = str(tot_conns)
        self.active_procs_count = str(len(processes))
        
        # Update overview labels
        self.query_one("#lbl_total_up", Label).update(self.total_up_rate)
        self.query_one("#lbl_total_down", Label).update(self.total_down_rate)
        self.query_one("#lbl_active_conns", Label).update(self.active_conns_count)
        self.query_one("#lbl_active_procs", Label).update(self.active_procs_count)
        
        # Update system status overview
        if status_res.get("status") == "ok":
            status_data = status_res["result"]
            g_lim = status_data["global_limits"]
            sys_info = (
                f"Active Interface:  [bold cyan]{status_data['interface']}[/bold cyan]\n"
                f"Global Upload:     [bold magenta]{g_lim['limit_up'] or 'Unlimited'}[/bold magenta]\n"
                f"Global Download:   [bold magenta]{g_lim['limit_down'] or 'Unlimited'}[/bold magenta]\n"
                f"Default Policy:    [bold yellow]{'DEFAULT-DENY (Allowlist)' if status_data['firewall_default_deny'] else 'DEFAULT-ALLOW (Blocklist)'}[/bold yellow]\n"
                f"Quota Throttled:   [bold red]{'YES' if status_data['is_quota_throttled'] else 'NO'}[/bold red]\n"
            )
            self.query_one("#status_panel", Static).update(sys_info)

        # Update process telemetry table
        table = self.query_one("#process_table", DataTable)
        table.clear()
        
        for p in sorted(processes, key=lambda x: x["rate_sent"] + x["rate_recv"], reverse=True):
            table.add_row(
                str(p["pid"]),
                p["comm"],
                p["username"],
                format_rate(p["rate_sent"]),
                format_rate(p["rate_recv"]),
                str(len(p["connections"])),
                key=p["comm"] # row key is the app name
            )
            
        # Update active rules panel
        if rules_res.get("status") == "ok":
            rules = rules_res["result"]
            rules_text = "[bold yellow]Active Traffic Rules[/bold yellow]\n\n"
            if not rules:
                rules_text += "[dim]No rules configured[/dim]"
            else:
                for r in rules:
                    if r["target_type"] == "global":
                        continue
                    limits = f" (Down: {r['limit_down']}, Up: {r['limit_up']})" if r["policy"] == "limit" else ""
                    rules_text += f"• [bold cyan]{r['target_value']}[/bold cyan] ({r['target_type']}) -> [bold red]{r['policy'].upper()}{limits}[/bold red]\n"
            self.query_one("#rules_panel", Static).update(rules_text)
            
        # Update quotas panel
        if quota_res.get("status") == "ok":
            quotas = quota_res["result"]
            quota_text = "\n[bold yellow]Quota Consumption[/bold yellow]\n\n"
            if not quotas:
                quota_text += "[dim]No quotas configured[/dim]"
            else:
                for q in quotas:
                    pct = (q["used_bytes"] / q["limit_bytes"]) * 100 if q["limit_bytes"] > 0 else 0
                    blocked_txt = " [bold red](EXCEEDED/RESTRICTED)[/bold red]" if q["blocked"] else ""
                    quota_text += (
                        f"• {q['period'].upper()}: {format_bytes(q['used_bytes'])} / {format_bytes(q['limit_bytes'])} "
                        f"({pct:.1f}%){blocked_txt}\n"
                    )
            self.query_one("#quotas_panel", Static).update(quota_text)

    async def action_refresh(self) -> None:
        await self.refresh_data()

    async def action_limit_proc(self) -> None:
        """Action handler when pressing 'l' (Limit application)."""
        table = self.query_one("#process_table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is not None:
            # Get selected app name from table
            row_key = table.coordinate_to_cell_key(cursor_row, 0)
            app_name = str(table.get_row(row_key)[1])
            
            # Show action prompt or apply simple default limit of 5mbit
            res = await self.client.send_request("add_rule", {
                "target_type": "process",
                "target_value": app_name,
                "policy": "limit",
                "limit_down": "5mbit",
                "limit_up": "5mbit"
            })
            if res.get("status") == "ok":
                self.notify(f"Limited {app_name} to 5mbit.")
                await self.refresh_data()

    async def action_block_proc(self) -> None:
        """Action handler when pressing 'b' (Block application)."""
        table = self.query_one("#process_table", DataTable)
        cursor_row = table.cursor_row
        if cursor_row is not None:
            row_key = table.coordinate_to_cell_key(cursor_row, 0)
            app_name = str(table.get_row(row_key)[1])
            
            res = await self.client.send_request("add_rule", {
                "target_type": "process",
                "target_value": app_name,
                "policy": "block"
            })
            if res.get("status") == "ok":
                self.notify(f"Blocked {app_name} from network access.")
                await self.refresh_data()

def main():
    DuskyTuiApp().run()

if __name__ == "__main__":
    main()
