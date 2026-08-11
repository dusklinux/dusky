# Adaptive Glass Network Dashboard v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prototype Network popup with the approved compact Network Dashboard showing one connection identity, signal quality, live transfer rates, session data usage with a Since timestamp, and compact Wi-Fi/Bluetooth actions.

**Architecture:** Keep the existing AGS popup-window and hover/click state architecture unchanged. Add one focused `networkSession.ts` data module that samples `/proc/net/route` plus `/sys/class/net/<iface>/statistics/{rx,tx}_bytes`, persists cumulative state under `GLib.get_user_runtime_dir()`, and exports one reactive session sample. Rebuild `NetworkControl.tsx` as a presentation component over AstalNetwork/AstalBluetooth plus that session sample, with dedicated Network Dashboard CSS rather than generic panel rows.

**Tech Stack:** AGS v3, Gnim TSX, GTK4, AstalNetwork, AstalBluetooth, GLib, Linux `/proc` and `/sys` network counters, Matugen CSS variables.

## Global Constraints

- Preserve the existing dedicated popup-window hover/click architecture and Network bar trigger behavior.
- Show the active SSID exactly once in the popup.
- Do not show nearby networks, saved networks, hotspot controls, graphs, IP/MAC addresses, daily/monthly counters, or a reboot/reset message.
- Session usage must continue accumulating if the default network interface changes during the same user runtime session.
- Persist session state only under the user runtime directory; do not create long-term history in the home directory.
- Use GTK symbolic icons where practical; do not add decorative icon bubbles.
- `Manage` must continue opening the existing Dusky Network Manager.
- `Devices` must continue opening the Bluetooth manager.
- Do not modify Waybar.

---

### Task 1: Session Network Meter

**Files:**
- Create: `lib/networkSession.ts`
- Create: `tests/test_v20_network_dashboard.py`

**Interfaces:**
- Produces `networkSession`, a reactive accessor containing `{ interfaceName, rxRate, txRate, rxTotal, txTotal, sinceEpoch }`.
- Produces pure formatters `formatBytes(bytes)`, `formatRate(bytesPerSecond)`, and `formatSince(epochSeconds)` for the UI.

- [ ] **Step 1: Write the failing contract tests**

Require the new module to read `/proc/net/route`, `/sys/class/net`, persist under `GLib.get_user_runtime_dir()`, handle interface switches without resetting cumulative totals, and expose formatting helpers.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
python3 tests/test_v20_network_dashboard.py
```

Expected: FAIL because `lib/networkSession.ts` does not exist.

- [ ] **Step 3: Implement `networkSession.ts`**

Implement a stateful sampler with these rules:

```text
active interface = default route from /proc/net/route
rx/tx counters  = /sys/class/net/<iface>/statistics/{rx,tx}_bytes
rate            = positive counter delta / elapsed seconds
session total   = accumulated positive deltas across interfaces
state file      = $XDG_RUNTIME_DIR/dusky-adaptive-glass-network-session.json
since timestamp = persisted session start time
```

On first state creation, use the current active interface counters as the initial session usage and estimate the start timestamp from `/proc/uptime`. On interface change or counter rollback, reset only the per-interface baseline while preserving accumulated totals.

- [ ] **Step 4: Run the focused test and confirm it passes**

```bash
python3 tests/test_v20_network_dashboard.py
```

Expected: PASS.

---

### Task 2: Network Dashboard Component

**Files:**
- Modify: `components/NetworkControl.tsx`
- Modify: `tests/test_v20_network_dashboard.py`

**Interfaces:**
- Consumes `networkSession`, `formatBytes`, `formatRate`, and `formatSince` from Task 1.
- Continues to call `runNetworkManager()` and `runBluetoothManager()` from `lib/dusky.ts`.

- [ ] **Step 1: Extend the failing tests for the approved information hierarchy**

Require these labels/classes and prohibit duplicated SSID rendering:

```text
connection header
LIVE TRAFFIC
download metric
upload metric
SESSION DATA
total usage
Since timestamp
Wi-Fi / Manage action
Bluetooth / Devices action
```

Require proper GTK `<image iconName=...>` elements for connection, transfer, Wi-Fi, Bluetooth, and chevron affordances.

- [ ] **Step 2: Run the focused test and confirm it fails**

Expected: FAIL against the prototype component.

- [ ] **Step 3: Rebuild `NetworkPanel()`**

Use this visual order:

```text
[ Wi-Fi icon ] Charles arch
                Connected · Signal 74%

LIVE TRAFFIC
[↓ 1.8 MB/s Download] [↑ 240 KB/s Upload]

SESSION DATA
1.46 GB used
↓ 1.31 GB       ↑ 150 MB
Since 2:14 PM

Wi-Fi                         Manage ›
Bluetooth · On                Devices ›
```

SSID appears only in the header. The lower Wi-Fi action must not repeat it.

Bluetooth status should be `Off`, `On`, `1 connected`, or `<N> connected`. Use the existing Astal device list for connected devices and a lightweight `bluetoothctl show` poll for powered state.

- [ ] **Step 4: Run the focused test and confirm it passes**

Expected: PASS.

---

### Task 3: Network Dashboard Visual Polish

**Files:**
- Modify: `style.css`
- Modify: `tests/test_v20_network_dashboard.py`

**Interfaces:**
- Styles only the Network popup through dedicated `.network-dashboard-*` selectors and `.popup-network`.

- [ ] **Step 1: Add failing visual-contract assertions**

Require a compact 360–380px dashboard, darker network-specific glass, clear section kickers, two-column metrics, one dominant total-usage value, compact action rows, and no icon-shell/bubble classes.

- [ ] **Step 2: Run focused tests and confirm failure**

Expected: FAIL because the new CSS does not exist.

- [ ] **Step 3: Implement the styles**

Target:

```text
width: ~370px
outer padding: 14px
section gap: 12–14px
header icon: 26–30px, no background bubble
live metrics: equal columns with subtle divider
session card: darker inset surface, total value strongest
quick actions: ~42–46px rows, icon + label + trailing action/chevron
```

Use Matugen primary/secondary colors subtly for signal/download/upload accents. Avoid green/red semantics for traffic direction.

- [ ] **Step 4: Run focused tests and confirm pass**

Expected: PASS.

---

### Task 4: Regression and Packaging

**Files:**
- Modify: `README.md`
- Package: `/mnt/data/adaptive-glass-ags-v2.0.zip`

- [ ] **Step 1: Run all Python contract/regression tests**

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: 0 failures.

- [ ] **Step 2: Run shell smoke tests**

```bash
bash -n install.sh
bash tests/test_install_smoke.sh
bash tests/test_capture_helper.sh
```

Expected: all exit 0.

- [ ] **Step 3: Parse every TS/TSX file with the existing parser check**

Expected: 0 syntax failures.

- [ ] **Step 4: Verify isolation**

Confirm no Waybar file is modified and `popupState.ts`, workspace capture/focus logic, ClockCard behavior, Audio, Display, Power, and Media remain unchanged except imports required by the Network component.

- [ ] **Step 5: Package and verify ZIP integrity**

```bash
cd /mnt/data
zip -qr adaptive-glass-ags-v2.0.zip adaptive-glass-ags-v2.0
unzip -t adaptive-glass-ags-v2.0.zip
```

Expected: no ZIP errors.
