# Engine: `network`

- **Class:** `NetworkManagerEngine` — `engines/network_manager.py`
- **Engine types:** `network`
- **Default target:** `~/.cache/dusky_tui/wifi_cache.json` (scan cache only — all real work goes through `nmcli`)

## Target format

A JSON array of scan records written by the engine after
`nmcli device wifi list --rescan yes`, e.g. `[{"in_use": false, "ssid": "MyNet",
"security": "WPA2", "signal": 72}]`. Read once at engine startup to populate
tab 0 instantly; the engine overwrites it on every rescan. It is never read
for writes — target presence only affects `[Missing]` rendering.

## Scope / key mapping

`write_value` dispatches: key checks `rescan` and `wifi_radio` happen FIRST
(any scope works), then scope dispatch, then fallback `(True, "OK", "")`.
`load_state` publishes keys as `"scope/key"` (e.g. `status/wifi_radio`,
`saved/<uuid>`); use the exact scopes below.

| scope | key | type | semantics |
|---|---|---|---|
| `network` | `rescan` | bool trigger | sets rescan event (same as `status_action/rescan`) |
| `network` | `hidden_menu` | menu parent | expandable folder (`type_="menu"`, `is_parent=True`) for connecting to hidden networks |
| `network` | `hidden_ssid` | string | in-memory hidden network SSID draft |
| `network` | `hidden_password` | string | in-memory hidden network password draft (masked in logs) |
| `network` | `hidden_security` | cycle | security selector (`"Auto"`, `"WPA/WPA2/WPA3"`, `"WPA3 (SAE)"`, `"Open"`, `"WEP"`) |
| `network` | `connect_hidden` | bool trigger | initiates connection to hidden network via targeted scan and profile fallback |
| `network` | `qr_hidden` | bool trigger | shares hidden network credentials via interactive QR code with `H:true` |
| `network` | `net__<ssid>` | menu parent | engine-created per-SSID folder (`type_="menu"`, `is_parent=True`) — never write it |
| `network` | `cn__<ssid>` | bool trigger | connect: saved profile by name, else open network |
| `network` | `rc__<ssid>` | bool trigger | reconnect: disconnects and brings connection back up |
| `network` | `qr_net__<ssid>` | bool trigger | shares network credentials via an interactive QR code |
| `network` | `pw__<ssid>` | string | connect with password; empty value → `(False, "Password cannot be empty.", "")` |
| `network` | `dc__<ssid>` | bool trigger | disconnect; `(False, "Not connected to this network.", "")` if not active |
| `network` | `fg__<ssid>` | bool trigger | forget saved profile by name; `(False, "Connection not found.", "")` if unsaved |
| `saved` | `<uuid>` | bool | autoconnect toggle — key MUST match strict UUID regex, else falls through to OK |
| `saved_action` | `cn__<uuid>` / `dc__<uuid>` / `fg__<uuid>` | bool trigger | connect/disconnect/delete saved profile by UUID |
| `saved_action` | `rc__<uuid>` | bool trigger | reconnect saved profile by UUID |
| `saved_action` | `qr_prof__<uuid>` | bool trigger | shares saved profile via interactive QR code |
| `status` | `wifi_radio` | bool | `nmcli radio wifi on/off`; `"true"` → on |
| `status_action` | `reconnect` | bool trigger | disconnects and reconnects active connection |
| `status_action` | `qr_active` | bool trigger | shares active Wi-Fi profile via interactive QR code |
| `status_action` | `disconnect` | bool trigger | disconnect active connection |
| `status_action` | `restart_nm` | bool trigger | `sudo -n systemctl restart NetworkManager` → may return `AUTH_REQUIRED` |
| `status_action` | `rescan` | bool trigger | force rescan |
| `clipboard` | `status_type`, `status_ssid`, `status_ip`, `status_gateway`, `status_detail`, `status_device`, `throughput_down`, `throughput_up`, `throughput_rx_total`, `throughput_tx_total`, `ping_router`, `ping_internet`, `ping_packet_loss`, `speedtest_down_result`, `speedtest_up_result` | bool `options=["copy"]` | copies the item's LABEL text after the first `:` via `wl-copy` (fallback `xclip`); `(False, "Nothing to copy.", "")` if label is `N/A`/`None`/`--` |
| `speedtest_action` | `speedtest_full` / `speedtest_down` / `speedtest_up` | bool trigger | full / download-only / upload-only test |
| `speedtest_info` | `speedtest_status` | action | read-only label row (write falls through to OK) |
| `hotspot` | `hotspot_ssid` | string | in-memory SSID (default `"MyHotspot"`) |
| `hotspot` | `hotspot_password` | string | in-memory password; non-empty must be ≥ 8 chars else `(False, "Password must be at least 8 characters.", "")` |
| `hotspot` | `start_hotspot_24` / `start_hotspot_5` | bool trigger | `nmcli device wifi hotspot ... band bg\|a` (+ `password` if set) |
| `hotspot` | `stop_hotspot` | bool trigger | `nmcli device disconnect <wifi-dev>` |
| `hotspot` | `qr_hotspot` | bool trigger | shares hotspot credentials via interactive QR code |
| `hotspot` | `hotspot_status_info`, `hotspot_clients_info` | action | read-only label rows |
| (tab 3, optional) | `dns_current` | action | read-only DNS provider label; **no DNS write scope exists** |

Per-SSID (`cn__/pw__/dc__/fg__/net__<ssid>`) and per-UUID (`saved/<uuid>`,
`saved_action/*__<uuid>`) items are GENERATED by the engine from live scans —
a schema must not pre-create them.

## Types & value handling

- `item_type` is ignored; triggers fire only when `new_value == "true"`.
- `load_state` republishes every trigger key as `"false"` so Apply buttons
  reset; it also publishes `saved/<uuid>` autoconnect, `hotspot/*`, and
  `hotspot/hotspot_status_info` (`"Active"` only while an AP-mode connection
  exists).
- State bridging: every non-`action`/`menu` schema item is republished as
  `"<scope>/<key>"` (bare key if scope empty) with `item.serialize(item.value)`
  so the UI never marks them `[Missing]`.
- No `__DELETE__`/`nil`/color/picker/cycle handling; unmatched key+scope
  returns `(True, "OK", "")` — unknown writes never fail.

## Quirks

- Tabs 0 and 1 are rebuilt from scratch by `_rebuild_schema` every 1.5 s
  background tick; engine-created items get `exists_in_target=True` (never
  `[Missing]`). Expanded parent folders are preserved across rebuilds.
- Label/value updates are positional: the engine expects tab 0 Networks,
  tab 1 Saved, tab 2 Status, tab 3 DNS, tab 4 Speed Test, tab 5 Hotspot, with
  each block guarded by `len(schema) > N`. The 5-tab reference schema
  (`TABS = ["Networks","Saved","Status","Speed Test","Hotspot"]`, no DNS tab)
  silently skips speed/hotspot label updates — include a DNS tab at index 3
  if you want live speed-test and hotspot rows.
- Background loop (1.5 s): polls radio/active status, throughput (rx/tx),
  pings (router + 1.1.1.1, window 24 / avg 5), DNS provider; auto-rescans
  every 25 s when the radio is enabled, and rescans on `rescan_event`.
- Speed tests with the app suspend the TUI and launch `engines/rich_speedtest.py`
  `<mode>` — a Rich-based helper invoked as a subprocess, NOT an engine — then
  read `~/.cache/dusky_tui/speedtest_last.json`. Headless: uses
  `dusky-network-speedtest` (PATH) or a native Cloudflare
  speed test. A running test rejects new starts.
- Reference schema sets `ENABLE_USER_PRESETS = False` and
  `CUSTOM_VIEWS = {2: {"view": render_network_dashboard_view, "interval": 1.0}}`.

## Example items

```python
ConfigItem(label="Wi-Fi Radio Switch", key="wifi_radio", scope="status",
           type_="bool", default=True, group="Hardware Control"),
ConfigItem(label="⟳ Rescan Networks", key="rescan", scope="network", type_="bool",
           default=False, options=["trigger"], group="Actions"),
ConfigItem(label="Hotspot Password", key="hotspot_password", scope="hotspot",
           type_="string", default="", group="Hotspot Configuration"),
ConfigItem(label="Download Speed: --", key="speedtest_down_result", scope="clipboard",
           type_="bool", default=False, options=["copy"], group="Test Status & Results"),
```