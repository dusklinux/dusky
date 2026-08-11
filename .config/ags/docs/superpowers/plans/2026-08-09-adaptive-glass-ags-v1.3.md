# Adaptive Glass AGS v1.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add correct hover/pin popup behavior, workspace activity previews, and a concept-directed visual polish pass to Adaptive Glass AGS.

**Architecture:** `HoverPopover.tsx` becomes the single owner of preview/pinned popup state so Clock/Network/Audio/Display/Power stay consistent. `WorkspacePreview.tsx` reads AstalHyprland clients and resolves AstalApps icons, while `Workspaces.tsx` keeps click switching delegated to Dusky's existing monitor-banked dispatcher. CSS changes remain presentation-only and the AGS window stays transparent.

**Tech Stack:** AGS v3, GTK4, Gnim JSX, AstalHyprland, AstalApps, Matugen CSS, Dusky shell helpers.

## Global Constraints
- Waybar must remain untouched.
- Hover preview closes 220 ms after the pointer leaves both trigger and panel.
- Click pins Clock/Network/Audio/Display/Power; click again or outside closes.
- Workspace click switches workspace and never pins its preview.
- Workspace preview is schematic and native-data-driven, not screenshot-based.
- No additional runtime packages.

---

### Task 1: Shared popup state machine

**Files:**
- Modify: `components/HoverPopover.tsx`
- Test: `tests/test_v13_contract.py`

**Interfaces:**
- Consumes: `trigger: JSX.Element`, `panel: JSX.Element`, optional `onActivate(): unknown`, optional `pinOnClick: boolean`.
- Produces: one exclusive popup with delayed hover-close and click pinning.

- [ ] **Step 1: Write failing contract tests**

Add tests asserting `HoverPopover.tsx` contains `CLOSE_DELAY_MS = 220`, trigger and popover `Gtk.EventControllerMotion` enter/leave handlers, `Gtk.GestureClick`, `pinned`, a delayed `GLib.timeout_add`, and module-level `activePopover` exclusivity. Add a test asserting workspace usage passes `pinOnClick={false}` and `onActivate`.

- [ ] **Step 2: Run RED test**

Run:
```bash
python -m pytest tests/test_v13_contract.py -q
```
Expected: failures because v1.2 only opens on hover and has no leave/pin state.

- [ ] **Step 3: Implement the state machine**

Use this public prop shape:
```ts
type HoverPopoverProps = {
  class?: string
  trigger: JSX.Element
  panel: JSX.Element
  pinOnClick?: boolean
  onActivate?: () => unknown
}
```

Use module state:
```ts
const CLOSE_DELAY_MS = 220
let activePopover: Gtk.Popover | null = null
```

Within the component track `pinned`, `triggerHovered`, `panelHovered`, and one GLib timeout source. `openPopover()` must close the prior `activePopover` before opening this one. Trigger/panel leave handlers call `scheduleClose()`. A `Gtk.GestureClick` toggles pin state for normal controls; when `pinOnClick === false`, it executes `onActivate` and closes after GTK's default menu-button activation using `GLib.idle_add`.

- [ ] **Step 4: Run GREEN test**

Run:
```bash
python -m pytest tests/test_v13_contract.py -q
```
Expected: popup-state tests pass.

---

### Task 2: Workspace activity preview

**Files:**
- Create: `components/WorkspacePreview.tsx`
- Modify: `components/Workspaces.tsx`
- Test: `tests/test_v13_contract.py`

**Interfaces:**
- `WorkspacePreview({ localId }: { localId: number }): JSX.Element`
- Uses focused-monitor bank mapping `targetWorkspaceId(localId): number`.
- Snapshot entries contain `address`, `className`, `title`, `iconName`, `width`, and `height`.

- [ ] **Step 1: Write failing workspace-preview tests**

Assert the new component imports `gi://AstalHyprland`, `gi://AstalApps`, uses `hyprland.clients`, resolves icons with `exact_query`/`fuzzy_query`, contains `workspace-preview-map`, `workspace-preview-list`, and an `Empty workspace` state. Assert `Workspaces.tsx` renders `<WorkspacePreview localId={id}` inside `HoverPopover` and keeps `focusWorkspace(id)`.

- [ ] **Step 2: Run RED test**

Run:
```bash
python -m pytest tests/test_v13_contract.py -q
```
Expected: failures because the preview component does not exist.

- [ ] **Step 3: Implement bank mapping and snapshots**

Compute focused monitor bank without shell polling:
```ts
const monitors = Array.from(hyprland.monitors).sort((a, b) => a.x - b.x || a.y - b.y)
const focused = hyprland.focusedMonitor
const index = Math.max(0, monitors.findIndex((monitor) => monitor.id === focused?.id))
const targetId = index * 10 + localId
```

Use `createPoll([], 450, () => ...)` to snapshot only clients whose `client.workspace?.id === targetId`, excluding hidden/unmapped clients. Resolve desktop icons from `AstalApps.Apps()` first by exact class and then fuzzy class. Limit the map/list to six visible entries.

Render the map with two schematic rows of up to two mini-window cards each; derive compact `widthRequest` from the source client aspect ratio so wider windows look wider. Render the list with app icon, class, and ellipsized title. Show `Empty workspace` if the snapshot is empty.

- [ ] **Step 4: Wire each workspace button through HoverPopover**

Replace the plain workspace button with:
```tsx
<HoverPopover
  class={active((current) => current === id ? "workspace-button active" : "workspace-button")}
  trigger={<label label={`${id}`} />}
  panel={<WorkspacePreview localId={id} />}
  pinOnClick={false}
  onActivate={() => focus(id)}
/>
```

Keep active-state polling and `focusWorkspace(id)`.

- [ ] **Step 5: Run GREEN test**

Run:
```bash
python -m pytest tests/test_v13_contract.py -q
```
Expected: workspace-preview tests pass.

---

### Task 3: Concept polish and command safety

**Files:**
- Modify: `style.css`
- Modify: `lib/dusky.ts`
- Modify: `README.md`
- Test: `tests/test_v13_contract.py`

**Interfaces:**
- `runTerminal(command: string, appId?: string): Promise<void>` chooses `foot`, then `kitty`, then `wezterm`, otherwise notifies failure.
- Existing `runNetworkManager()` and terminal-backed actions reuse it where appropriate.

- [ ] **Step 1: Write failing polish/safety tests**

Assert CSS contains `.workspace-preview`, `.workspace-preview-map`, `.workspace-mini-window`, `.workspace-app-row`, `.workspace-button.active` with an accent shadow, `.panel-kicker`/`.panel-title` hierarchy, and `.bar-shell` remains transparent. Assert `lib/dusky.ts` checks for `foot` and `kitty` before launching the Dusky network TUI.

- [ ] **Step 2: Run RED test**

Run:
```bash
python -m pytest tests/test_v13_contract.py -q
```
Expected: preview-style and terminal-fallback tests fail.

- [ ] **Step 3: Implement concept polish**

Keep the full shell transparent. Increase bar top margin modestly, make workspace active state brighter with primary/tertiary glow, make the clock slightly more elevated than utility leaders, preserve the asymmetric media gradient, and give popup bodies a darker glass center with a colored top edge. Add dedicated preview-map/list surfaces and icon/title typography so the preview reads like a miniature workspace, not a tooltip.

- [ ] **Step 4: Implement terminal fallback**

Change `runNetworkManager()` to shell-select an installed terminal in this order: `foot`, `kitty`, `wezterm`; if none exists, notify instead of throwing. Keep the same Dusky Python TUI command.

- [ ] **Step 5: Verify complete package**

Run:
```bash
python -m pytest tests -q
bash -n install.sh
bash tests/test_install_smoke.sh
```
Then parse all TSX files with the locally available parser if present, and create `adaptive-glass-ags-v1.3.zip` excluding test caches.
