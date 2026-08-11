# Adaptive Glass AGS v1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace the laggy Gtk.Popover interaction path with explicit AGS layer-shell popup windows and replace the schematic workspace card with a real-window thumbnail navigator that can focus an exact Hyprland client.

**Architecture:** A single reactive popup controller owns active/pinned state for Calendar, Network, Audio, Display, Power, and Workspace surfaces. Bar controls become ordinary Gtk.Button triggers; separate Astal.Window overlay surfaces render panels at fixed top-left/top-center/top-right positions and own pointer enter/leave directly. Workspace previews take a fresh toplevel screenshot on open/row-hover using grim (`-T`, with legacy `-w` fallback), cache only runtime files, and switch workspace then focus the selected window by exact Hyprland address on click.

**Tech Stack:** AGS v3 / Gnim JSX, GTK4, Astal GTK4 layer shell, AstalHyprland, AstalApps, grim 1.5+ foreign-toplevel capture when available, Dusky Hyprland Lua dispatcher scripts.

## Global Constraints

- Do not modify or stop Waybar from the package.
- Do not add a compositor plugin dependency for previews.
- Hover opens without an intentional opening delay; leave uses only a short bridge delay (<= 100 ms).
- Click pins non-workspace panels; workspace number clicks always switch workspace and never pin.
- One popup surface may be active at a time.
- Workspace preview must not duplicate a schematic map and app list.
- Hovering an app row changes the large real thumbnail; clicking a row switches to its workspace and focuses that exact client address.
- If grim/toplevel capture is unavailable, keep the shell alive and show a graceful preview fallback.

---

### Task 1: Explicit Popup Controller and Layer Windows

**Files:**
- Create: `lib/popupState.ts`
- Create: `components/PanelTrigger.tsx`
- Create: `components/PopupWindow.tsx`
- Create: `components/PopupWindows.tsx`
- Modify: `app.tsx`
- Modify: `components/ClockCard.tsx`
- Modify: `components/NetworkControl.tsx`
- Modify: `components/AudioControl.tsx`
- Modify: `components/DisplayControl.tsx`
- Modify: `components/PowerControl.tsx`
- Test: `tests/test_v15_contract.py`

**Interfaces:**
- Produces `PanelId`, `activePanel`, `pinnedPanel`, `hoverPanel(id)`, `leaveTrigger(id)`, `enterPanel(id)`, `leavePanel(id)`, `togglePin(id)`, and `closePanels()`.
- `PanelTrigger` consumes a `PanelId` and ordinary trigger child.
- `PopupWindow` consumes monitor, anchor/margins, panel id, and panel JSX.

- [x] Write RED tests asserting no control imports `HoverPopover`, triggers are plain buttons, layer popup windows use `Astal.Exclusivity.IGNORE`, and close delay is <=100ms.
- [x] Run the v1.5 contract tests and confirm RED.
- [x] Implement the popup state controller and popup window components.
- [x] Refactor controls into trigger exports plus panel-content exports.
- [x] Render popup windows alongside each monitor bar from `app.tsx`.
- [x] Run tests and confirm GREEN.

### Task 2: Real Workspace Thumbnail Navigator

**Files:**
- Create: `lib/workspacePreviewState.ts`
- Create: `scripts/capture_window_preview.sh`
- Replace: `components/WorkspacePreview.tsx`
- Modify: `components/Workspaces.tsx`
- Modify: `lib/dusky.ts`
- Modify: `style.css`
- Test: `tests/test_v15_contract.py`

**Interfaces:**
- `openWorkspacePreview(localId)` snapshots clients immediately and selects most-recently-focused client.
- `selectPreviewClient(address)` triggers a fresh capture only when selection changes.
- `activatePreviewClient(localId,address)` awaits workspace switch then exact address focus.
- `capture_window_preview.sh ADDRESS DEST` writes a PNG or exits nonzero without killing AGS.

- [x] Add RED tests asserting grim `-T` capture with `-w` fallback, no schematic `MiniWindow`, row hover selection, row click exact focus, and Lua-compatible address focus.
- [x] Run tests and confirm RED.
- [x] Implement runtime capture helper and state store.
- [x] Replace workspace preview with one large Gtk.Picture region plus interactive app rows.
- [x] Make workspace-number hover open the shared workspace popup immediately and click switch workspace directly.
- [x] Add `focusWindow(address)` using `hyprctl dispatch 'hl.dsp.focus({ window = "address:0x..." })'` after the workspace switch.
- [x] Run tests and confirm GREEN.

### Task 3: Polish and Regression Verification

**Files:**
- Modify: `style.css`
- Modify: `README.md`
- Test: all files under `tests/`

**Interfaces:**
- Workspace navigator presents a strong 16:9 preview, selected row accent, compact metadata list, loading/fallback state, and no duplicated mini-map.
- Existing Media, Matugen, brightnessctl, Dusky Network, calendar, audio, and power behaviors remain available.

- [x] Style dedicated popup windows and the workspace navigator toward the concept hierarchy.
- [x] Document the optional `grim` capture capability and live-test sequence.
- [x] Run all pytest contract tests.
- [x] Run install smoke test and Bash syntax checks.
- [x] Parse all TS/TSX files and scan for Waybar mutations.
- [x] Package ZIP and verify archive integrity.
