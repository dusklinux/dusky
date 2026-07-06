import subprocess
import threading
import time
import json
import re
import logging
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine, ConfigItem

logger = logging.getLogger("dusky_network_engine")

# =============================================================================
#  NMCLI OUTPUT PARSER
#  nmcli -t uses '\:' to escape literal colons in field values.
#  A naive str.split(':') breaks on connection names containing colons.
# =============================================================================
_NMCLI_FIELD_SPLIT = re.compile(r'(?<!\\):')

def _split_nmcli_line(line: str) -> list[str]:
    """Split an nmcli -t output line by unescaped colons, then unescape fields."""
    return [f.replace("\\:", ":") for f in _NMCLI_FIELD_SPLIT.split(line)]

# =============================================================================
#  ENGINE
# =============================================================================
class NetworkManagerEngine(BaseEngine):
    """
    Async-first NetworkManager engine for the Dusky TUI ecosystem.
    
    Features:
    - Instant startup from cached scan results (~/.cache/dusky_tui/wifi_cache.json)
    - Background polling thread for state changes + periodic rescans
    - Dynamic schema rebuilding (tabs 0 & 1 are rebuilt on every state change)
    - Thread-safe Textual integration via call_from_thread
    """

    def __init__(self, config_path: str = ""):
        self.cache_dir = Path.home() / ".cache" / "dusky_tui"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._target_path = str(self.cache_dir / "wifi_cache.json")

        self.app = None
        self.shutdown_event = threading.Event()
        self.rescan_event = threading.Event()

        # In-memory hotspot config
        self._hotspot_ssid = "MyHotspot"
        self._hotspot_password = ""

        # Load cached scan results for instant startup
        self._cached_scans: list[dict] = []
        cache_path = Path(self._target_path)
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self._cached_scans = json.load(f)
            except Exception as e:
                logger.error(f"Error loading wifi cache: {e}")

        # Start background loop
        self._bg_thread = threading.Thread(target=self._background_loop, daemon=True)
        self._bg_thread.start()

    def set_app(self, app) -> None:
        """Called by main.py before app.run(). Only stores reference.
        The background loop handles first _rebuild_schema via call_from_thread
        once the Textual event loop is running."""
        self.app = app
        # Force immediate state change detection on next background loop iteration
        self.rescan_event.set()

    @property
    def target_path(self) -> str:
        return self._target_path

    # =========================================================================
    #  BaseEngine Contract
    # =========================================================================

    def load_state(self) -> dict[str, Any]:
        """Returns current NM state. Called by the TUI's watch_target_file timer.
        Must include ALL schema keys so on_mount and watch_target_file
        set exists_in_target = True rather than marking items [Missing]."""
        state: dict[str, Any] = {}

        radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip()
        state["status/wifi_radio"] = "true" if radio == "enabled" else "false"

        for conn in self._get_saved_wifi():
            state[f"saved/{conn['uuid']}"] = "true" if conn["autoconnect"] else "false"

        # Hotspot config
        state["hotspot/hotspot_ssid"] = self._hotspot_ssid
        state["hotspot/hotspot_password"] = self._hotspot_password

        active = self._get_active_wifi_connection()
        state["hotspot/hotspot_status_info"] = "Active" if active and active.get("mode") == "ap" else "Inactive"

        # Trigger bools must appear in state so on_mount sets exists_in_target = True
        state["network/rescan"] = "false"
        state["hotspot/start_hotspot_24"] = "false"
        state["hotspot/start_hotspot_5"] = "false"
        state["hotspot/stop_hotspot"] = "false"
        state["status_action/disconnect"] = "false"
        state["status_action/restart_nm"] = "false"
        state["status_action/rescan"] = "false"
        state["clipboard/status_ssid"] = "false"
        state["clipboard/status_ip"] = "false"
        state["clipboard/status_gateway"] = "false"
        state["clipboard/status_dns"] = "false"
        state["clipboard/status_device"] = "false"

        # Include ALL current dynamic items so watch_target_file doesn't
        # reset exists_in_target=False on items it can't find in state
        if self.app and hasattr(self.app, 'schema'):
            for tab_idx in (0, 1):
                for item in self.app.schema.get(tab_idx, []):
                    if item.type_ in ("action", "menu"):
                        continue  # Already skipped by watch_target_file
                    cache_key = f"{item.scope}/{item.key}" if item.scope else item.key
                    if cache_key not in state:
                        state[cache_key] = item.serialize(item.value)

        return state

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """Routes all mutations through a unified command dispatcher."""
        logger.info(f"write_value: key={target_key}, scope={target_scope}, val={new_value}")

        # ---- Rescan button ----
        if target_key == "rescan":
            self.rescan_event.set()
            return True, "WiFi rescan triggered.", ""

        # ---- Radio toggle ----
        if target_key == "wifi_radio":
            action = "on" if new_value == "true" else "off"
            res = subprocess.run(
                ["nmcli", "radio", "wifi", action],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, f"WiFi radio turned {action}.", ""
            return False, f"Failed to set radio: {res.stderr.strip()}", res.stderr

        # ---- Autoconnect toggle (uuid as key, scope=saved) ----
        if target_scope == "saved" and self._is_uuid(target_key):
            yn = "yes" if new_value == "true" else "no"
            res = subprocess.run(
                ["nmcli", "connection", "modify", "uuid", target_key, "connection.autoconnect", yn],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                return True, f"Autoconnect set to {yn}.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        # ---- Hotspot configuration ----
        if target_scope == "hotspot":
            return self._handle_hotspot(target_key, new_value)

        # ---- Network actions (connect, disconnect, forget, password connect) ----
        if target_scope == "network":
            return self._handle_network_action(target_key, new_value)

        # ---- Saved profile actions ----
        if target_scope == "saved_action":
            return self._handle_saved_action(target_key)

        # ---- Status tab actions ----
        if target_scope == "status_action":
            return self._handle_status_action(target_key)

        # ---- Clipboard copy (Connection Info items) ----
        if target_scope == "clipboard":
            return self._handle_clipboard(target_key)

        return True, "OK", ""

    # =========================================================================
    #  ACTION HANDLERS
    # =========================================================================

    def _handle_hotspot(self, key: str, value: str) -> tuple[bool, str, str]:
        if key == "hotspot_ssid":
            self._hotspot_ssid = value
            return True, "Hotspot SSID updated.", ""

        if key == "hotspot_password":
            if value and len(value) < 8:
                return False, "Password must be at least 8 characters.", ""
            self._hotspot_password = value
            return True, "Hotspot password updated.", ""

        if key in ("start_hotspot_24", "start_hotspot_5"):
            band = "bg" if key == "start_hotspot_24" else "a"
            wifi_dev = self._get_wifi_device()
            if not wifi_dev:
                return False, "No WiFi device found.", ""

            cmd = ["nmcli", "device", "wifi", "hotspot", "ifname", wifi_dev,
                   "ssid", self._hotspot_ssid, "band", band]
            if self._hotspot_password:
                cmd.extend(["password", self._hotspot_password])

            res = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Hotspot started!", res.stdout
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "stop_hotspot":
            wifi_dev = self._get_wifi_device()
            if not wifi_dev:
                return False, "No WiFi device.", ""
            res = subprocess.run(
                ["nmcli", "device", "disconnect", wifi_dev],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Hotspot stopped.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        return True, "OK", ""

    def _handle_network_action(self, key: str, value: str) -> tuple[bool, str, str]:
        """Handles connect/disconnect/forget from Tab 0 (Networks)."""
        # Password connect (string input submitted)
        if key.startswith("pw__"):
            ssid = key[4:]  # strip pw__ prefix
            if not value:
                return False, "Password cannot be empty.", ""
            threading.Thread(target=self._async_connect, args=(ssid, value), daemon=True).start()
            return True, f"Connecting to {ssid}...", ""

        # Direct connect (saved or open)
        if key.startswith("cn__"):
            ssid = key[4:]
            saved = self._get_saved_wifi()
            match = [c for c in saved if c["name"] == ssid]
            if match:
                threading.Thread(target=self._async_connect_saved, args=(ssid, match[0]["uuid"]), daemon=True).start()
            else:
                threading.Thread(target=self._async_connect, args=(ssid, None), daemon=True).start()
            return True, f"Connecting to {ssid}...", ""

        # Disconnect
        if key.startswith("dc__"):
            ssid = key[4:]
            active = self._get_active_wifi_connection()
            if active and active["ssid"] == ssid:
                threading.Thread(target=self._async_disconnect, args=(ssid, active["uuid"]), daemon=True).start()
                return True, f"Disconnecting from {ssid}...", ""
            return False, "Not connected to this network.", ""

        # Forget
        if key.startswith("fg__"):
            ssid = key[4:]
            saved = self._get_saved_wifi()
            match = [c for c in saved if c["name"] == ssid]
            if match:
                res = subprocess.run(
                    ["nmcli", "connection", "delete", "uuid", match[0]["uuid"]],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
                )
                if res.returncode == 0:
                    self.rescan_event.set()
                    return True, f"Forgot {ssid}.", ""
                return False, f"Failed: {res.stderr.strip()}", res.stderr
            return False, "Connection not found.", ""

        return True, "OK", ""

    def _handle_saved_action(self, key: str) -> tuple[bool, str, str]:
        """Handles connect/disconnect/forget from Tab 1 (Saved)."""
        if key.startswith("cn__"):
            uuid = key[4:]
            threading.Thread(target=self._async_connect_saved, args=(uuid, uuid), daemon=True).start()
            return True, "Connecting...", ""

        if key.startswith("dc__"):
            uuid = key[4:]
            threading.Thread(target=self._async_disconnect, args=(uuid, uuid), daemon=True).start()
            return True, "Disconnecting...", ""

        if key.startswith("fg__"):
            uuid = key[4:]
            res = subprocess.run(
                ["nmcli", "connection", "delete", "uuid", uuid],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "Deleted.", ""
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        return True, "OK", ""

    def _handle_status_action(self, key: str) -> tuple[bool, str, str]:
        """Handles actions from Tab 3 (Status)."""
        if key == "disconnect":
            active = self._get_active_wifi_connection()
            if active:
                threading.Thread(target=self._async_disconnect, args=(active["ssid"], active["uuid"]), daemon=True).start()
                return True, "Disconnecting...", ""
            return False, "No active connection.", ""

        if key == "restart_nm":
            res = subprocess.run(
                ["sudo", "-n", "systemctl", "restart", "NetworkManager"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15
            )
            if res.returncode == 0:
                self.rescan_event.set()
                return True, "NetworkManager restarted.", ""
            err = res.stderr.strip().lower()
            if "password is required" in err or "sudo:" in err:
                return False, "AUTH_REQUIRED", res.stderr
            return False, f"Failed: {res.stderr.strip()}", res.stderr

        if key == "rescan":
            self.rescan_event.set()
            return True, "Rescan triggered.", ""

        return True, "OK", ""

    def _handle_clipboard(self, key: str) -> tuple[bool, str, str]:
        """Copies the value portion of a Connection Info label to clipboard."""
        if not self.app:
            return False, "App not ready.", ""

        # Find the item and extract value from its label (text after the colon)
        for item in self.app.schema.get(3, []):
            if item.key == key and item.scope == "clipboard":
                label = item.label
                if ":" in label:
                    value = label.split(":", 1)[1].strip()
                else:
                    value = label.strip()

                if not value or value == "N/A" or value == "None":
                    return False, "Nothing to copy.", ""

                try:
                    subprocess.run(
                        ["wl-copy", value],
                        stdin=subprocess.DEVNULL, capture_output=True, timeout=3
                    )
                    return True, f"Copied: {value}", ""
                except FileNotFoundError:
                    # Fallback to xclip if wl-copy not available
                    try:
                        subprocess.run(
                            ["xclip", "-selection", "clipboard"],
                            input=value.encode(), capture_output=True, timeout=3
                        )
                        return True, f"Copied: {value}", ""
                    except FileNotFoundError:
                        return False, "No clipboard tool found (wl-copy/xclip).", ""

        return False, "Item not found.", ""

    # =========================================================================
    #  BACKGROUND POLLING WORKER
    # =========================================================================

    def _background_loop(self) -> None:
        """Daemon thread: polls radio/active state every 2s, rescans every 25s."""
        last_radio: str | None = None
        last_active_uuid: str | None = None
        last_scan_time: float = 0.0

        while not self.shutdown_event.is_set():
            try:
                radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip()
                active = self._get_active_wifi_connection()
                active_uuid = active["uuid"] if active else None

                state_changed = (radio != last_radio) or (active_uuid != last_active_uuid)

                now = time.time()
                should_scan = self.rescan_event.is_set() or (now - last_scan_time > 25.0)

                if should_scan and radio == "enabled":
                    self.rescan_event.clear()
                    last_scan_time = now

                    if self.app:
                        self.app.call_from_thread(self.app.notify_status, "Scanning WiFi networks...")

                    # Trigger NM rescan then read results
                    subprocess.run(
                        ["nmcli", "device", "wifi", "list", "--rescan", "yes"],
                        capture_output=True, stdin=subprocess.DEVNULL, timeout=15
                    )
                    self._cached_scans = self._get_scanned_wifi()

                    # Persist to disk cache
                    try:
                        with open(self._target_path, "w", encoding="utf-8") as f:
                            json.dump(self._cached_scans, f)
                    except Exception as e:
                        logger.error(f"Cache write error: {e}")

                    state_changed = True

                if state_changed:
                    last_radio = radio
                    last_active_uuid = active_uuid
                    if self.app:
                        self.app.call_from_thread(self._rebuild_schema)

            except Exception as e:
                logger.error(f"Background loop error: {e}")

            time.sleep(2.0)

    # =========================================================================
    #  ASYNC CONNECTION HELPERS (run in dedicated threads)
    # =========================================================================

    def _async_connect(self, ssid: str, password: str | None) -> None:
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        res = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30)
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Connected to {ssid}!")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    def _async_connect_saved(self, label: str, uuid: str) -> None:
        res = subprocess.run(
            ["nmcli", "connection", "up", "uuid", uuid],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30
        )
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Connected to {label}!")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    def _async_disconnect(self, label: str, uuid: str) -> None:
        res = subprocess.run(
            ["nmcli", "connection", "down", "uuid", uuid],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15
        )
        if self.app:
            if res.returncode == 0:
                self.app.call_from_thread(self.app.notify_status, f"Disconnected from {label}.")
            else:
                err = res.stderr.strip().split("\n")[0][:50]
                self.app.call_from_thread(self.app.notify_status, f"Failed: {err}")
                self.app.call_from_thread(self.app.play_reset_sound)
        self.rescan_event.set()

    # =========================================================================
    #  DYNAMIC SCHEMA REBUILDER
    # =========================================================================

    def _rebuild_schema(self) -> None:
        """Rebuilds tabs 0 & 1 in-place. Updates labels on tabs 2 & 3.
        Always runs on Textual's event loop (dispatched via call_from_thread)."""
        if not self.app or not self.app.schema:
            return

        radio = self._run_cmd(["nmcli", "radio", "wifi"]).strip() == "enabled"
        active = self._get_active_wifi_connection()
        saved = self._get_saved_wifi()

        # Preserve expanded menu states using full UIDs (scope.key)
        expanded = set()
        for tab_idx in (0, 1):
            for item in self.app.schema.get(tab_idx, []):
                if item.is_parent and item.expanded:
                    uid = f"{item.scope}.{item.key}" if item.scope and item.scope != "DEFAULT" else item.key
                    expanded.add(uid)

        # ----- Tab 0: Networks -----
        t0 = []
        t0.append(self._make_item(
            label="⟳ Rescan Networks", key="rescan", scope="network",
            type_="bool", default=False, group="Actions",
            extended_help="Triggers a new wireless network scan in the background.",
            options=["trigger"]
        ))

        if not radio:
            t0.append(self._make_item(
                label="睊 Wi-Fi Radio is OFF — Enable in Status tab",
                key="radio_off_notice", scope="network", type_="action", default=":",
                group="Status"
            ))
        else:
            for net in self._cached_scans:
                ssid, signal, security, in_use = net["ssid"], net["signal"], net["security"], net["in_use"]
                match = [c for c in saved if c["name"] == ssid]
                is_saved = len(match) > 0
                bar = self._signal_bar(signal)

                if in_use:
                    icon, status_lbl = "●", "Active"
                elif is_saved:
                    icon, status_lbl = "◉", "Saved"
                else:
                    icon, status_lbl = "○", "New"

                label = f"{icon} {status_lbl:<6} {ssid:<24} {security:<10} {signal}% {bar}"
                pkey = f"net__{ssid}"
                # parent_ref must match _get_item_uid: "scope.key"
                parent_uid = f"network.{pkey}"

                t0.append(self._make_item(
                    label=label, key=pkey, scope="network", type_="menu", default=None,
                    is_parent=True, expanded=(parent_uid in expanded), group="Available Networks"
                ))

                # Child actions — parent_ref must be the parent's UID
                if in_use:
                    t0.append(self._make_item(
                        label="✕ Disconnect", key=f"dc__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                    ))
                    t0.append(self._make_item(
                        label="✕ Forget", key=f"fg__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                        confirm_message=f"Permanently delete saved profile for **{ssid}**?"
                    ))
                elif is_saved:
                    uuid = match[0]["uuid"]
                    t0.append(self._make_item(
                        label="▶ Connect", key=f"cn__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                    ))
                    t0.append(self._make_item(
                        label="✕ Forget", key=f"fg__{ssid}", scope="network",
                        type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                        confirm_message=f"Permanently delete saved profile for **{ssid}**?"
                    ))
                    t0.append(self._make_item(
                        label="Auto-connect", key=uuid, scope="saved",
                        type_="bool", default=match[0]["autoconnect"], parent_ref=parent_uid
                    ))
                else:
                    if security and security not in ("Open", "--", ""):
                        t0.append(self._make_item(
                            label="▶ Connect (Enter Password)", key=f"pw__{ssid}",
                            scope="network", type_="string", default="", parent_ref=parent_uid
                        ))
                    else:
                        t0.append(self._make_item(
                            label="▶ Connect (Open)", key=f"cn__{ssid}", scope="network",
                            type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                        ))

        self.app.schema[0] = t0

        # ----- Tab 1: Saved Profiles -----
        t1 = []
        for conn in saved:
            name, uuid, autocon = conn["name"], conn["uuid"], conn["autoconnect"]
            is_active = active and active["uuid"] == uuid
            indicator = "●" if is_active else "◉"
            pkey = f"prof__{uuid}"
            parent_uid = f"saved.{pkey}"

            t1.append(self._make_item(
                label=f"{indicator} {name}", key=pkey, scope="saved", type_="menu",
                default=None, is_parent=True, expanded=(parent_uid in expanded),
                group="Saved Connections"
            ))

            if is_active:
                t1.append(self._make_item(
                    label="✕ Disconnect", key=f"dc__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))
            else:
                t1.append(self._make_item(
                    label="▶ Connect", key=f"cn__{uuid}", scope="saved_action",
                    type_="bool", default=False, parent_ref=parent_uid, options=["trigger"]
                ))

            t1.append(self._make_item(
                label="Auto-connect", key=uuid, scope="saved",
                type_="bool", default=autocon, parent_ref=parent_uid
            ))
            t1.append(self._make_item(
                label="✕ Forget", key=f"fg__{uuid}", scope="saved_action",
                type_="bool", default=False, parent_ref=parent_uid, options=["trigger"],
                confirm_message=f"Permanently delete **{name}**?"
            ))

        self.app.schema[1] = t1

        # ----- Tab 2: Hotspot (update labels) -----
        if active and active.get("mode") == "ap":
            status_text = "Active"
            clients = self._get_hotspot_clients(active.get("device"))
            clients_text = f"{clients} connected"
        else:
            status_text = "Inactive"
            clients_text = "N/A"

        for item in self.app.schema.get(2, []):
            if item.key == "hotspot_status_info":
                item.label = f"Status: {status_text}"
            elif item.key == "hotspot_clients_info":
                item.label = f"Connected Clients: {clients_text}"
            elif item.key == "hotspot_ssid":
                item.value = self._hotspot_ssid
            elif item.key == "hotspot_password":
                item.value = self._hotspot_password

        # ----- Tab 3: Status (update labels) -----
        ssid_label = "None"
        ip_label = gateway_label = dns_label = "N/A"
        wifi_dev = ""

        if active and active.get("mode") != "ap":
            ssid_label = active["ssid"]
            wifi_dev = active["device"]
            info = self._get_device_ip_info(wifi_dev)
            ip_label, gateway_label, dns_label = info["ip"], info["gateway"], info["dns"]
        elif not active:
            wifi_dev = self._get_wifi_device()

        for item in self.app.schema.get(3, []):
            if item.key == "wifi_radio":
                item.value = radio
            elif item.key == "status_ssid":
                item.label = f"Connected WiFi: {ssid_label}"
            elif item.key == "status_ip":
                item.label = f"IP Address:     {ip_label}"
            elif item.key == "status_gateway":
                item.label = f"Gateway:        {gateway_label}"
            elif item.key == "status_dns":
                item.label = f"DNS Server:     {dns_label}"
            elif item.key == "status_device":
                item.label = f"WiFi Device:    {wifi_dev or 'N/A'}"

        # Rebuild key map and refresh UI
        self.app._rebuild_key_map()
        self.app._refresh_all_ui()

    # =========================================================================
    #  ITEM FACTORY
    # =========================================================================

    @staticmethod
    def _make_item(**kwargs) -> ConfigItem:
        """Creates a ConfigItem with exists_in_target and _initial_loaded pre-set."""
        item = ConfigItem(**kwargs)
        item.exists_in_target = True
        item.initial_value = item.value
        item._initial_loaded = True
        return item

    # =========================================================================
    #  NMCLI WRAPPERS
    # =========================================================================

    @staticmethod
    def _run_cmd(args: list[str], timeout: int = 5) -> str:
        try:
            res = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
            return res.stdout
        except (subprocess.TimeoutExpired, Exception):
            return ""

    def _get_wifi_device(self) -> str:
        for line in self._run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"]).splitlines():
            parts = _split_nmcli_line(line)
            if len(parts) >= 2 and parts[1] == "wifi":
                return parts[0]
        return ""

    def _get_active_wifi_connection(self) -> dict | None:
        """Returns active WiFi connection info or None."""
        for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show", "--active"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) >= 4 and parts[2] == "802-11-wireless":
                uuid = parts[1]
                mode_out = self._run_cmd(["nmcli", "-t", "-f", "802-11-wireless.mode", "connection", "show", uuid])
                mode = "ap" if "mode:ap" in mode_out.replace(" ", "") else "infra"
                return {"ssid": parts[0], "uuid": uuid, "device": parts[3], "mode": mode}
        return None

    def _get_saved_wifi(self) -> list[dict]:
        conns = []
        for line in self._run_cmd(["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT", "connection", "show"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) >= 4 and parts[2] == "802-11-wireless":
                conns.append({"name": parts[0], "uuid": parts[1], "autoconnect": parts[3] == "yes"})
        return conns

    def _get_scanned_wifi(self) -> list[dict]:
        """Reads the last scan results (no --rescan flag)."""
        scans = []
        seen: set[str] = set()
        for line in self._run_cmd(["nmcli", "-t", "-f", "IN-USE,SSID,SECURITY,SIGNAL", "device", "wifi", "list"]).splitlines():
            if not line:
                continue
            parts = _split_nmcli_line(line)
            if len(parts) < 4:
                continue
            in_use = parts[0].strip() == "*"
            ssid = parts[1]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            security = parts[2] if parts[2] else "Open"
            if security == "--":
                security = "Open"
            try:
                signal = int(parts[3])
            except ValueError:
                signal = 0
            scans.append({"in_use": in_use, "ssid": ssid, "security": security, "signal": signal})
        return scans

    def _get_device_ip_info(self, device: str) -> dict:
        info = {"ip": "N/A", "gateway": "N/A", "dns": "N/A"}
        for line in self._run_cmd(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", device]).splitlines():
            if not line:
                continue
            # Split on first colon only — values may contain colons
            idx = line.find(":")
            if idx < 0:
                continue
            key, val = line[:idx], line[idx + 1:]
            if "IP4.ADDRESS" in key:
                info["ip"] = val
            elif "IP4.GATEWAY" in key:
                info["gateway"] = val
            elif "IP4.DNS" in key:
                info["dns"] = val
        return info

    def _get_hotspot_clients(self, wifi_dev: str | None) -> int:
        if not wifi_dev:
            return 0
        try:
            # Try using sudo -n if credentials are cached, fallback to raw iw
            res = subprocess.run(
                ["sudo", "-n", "iw", "dev", wifi_dev, "station", "dump"],
                capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
            )
            if res.returncode != 0:
                res = subprocess.run(
                    ["iw", "dev", wifi_dev, "station", "dump"],
                    capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5
                )
            return len(re.findall(r"^Station", res.stdout, re.MULTILINE))
        except Exception:
            return 0

    @staticmethod
    def _is_uuid(s: str) -> bool:
        return bool(re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', s))

    @staticmethod
    def _signal_bar(signal: int) -> str:
        if signal >= 80: return "▂▄▆█"
        if signal >= 60: return "▂▄▆_"
        if signal >= 40: return "▂▄__"
        if signal >= 20: return "▂___"
        return "____"
