# Workspace Preview Resize States Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build v2.6.3 so workspace previews always resize to the natural size of the newly hovered workspace, empty workspaces show no popup, and multi-window icon rows are shorter and less visually dominant.

**Architecture:** Keep the existing single workspace popup window and the v2.6.2 navigator state model. Add a narrow popup-window registration hook so `workspacePreviewState` can request `Gtk.Window.set_default_size(-1, -1)` on the next GTK idle after a workspace snapshot changes. `openWorkspacePreview()` will return the visible client count, allowing `Workspaces.tsx` to suppress the workspace panel entirely for zero-window workspaces. Multi-window tile geometry will shrink without changing the 4×2 pagination contract.

**Tech Stack:** AGS v3/Gnim TSX, GTK4/Astal, AstalHyprland, GLib idle sources, pytest source-contract tests, shell installer/capture smoke tests.

## Global Constraints

- Preserve the v2.5.7 elastic/magnetic workspace rail behavior.
- Preserve v2.6 active-workspace preview suppression.
- Preserve v2.6 pagination: all clients retained, 8 per page, 4×2.
- Preserve rail-to-preview interaction ownership for non-empty non-active workspaces.
- Empty workspaces must not display any popup content.
- Single-window previews must shrink back after a multi-window preview without restarting AGS.
- Multi-window icon rows should target about 40–42px height with approximately 24px app icons.
- Do not reintroduce the large native window tooltip.
- Do not modify Waybar configuration.

---

### Task 1: Regression contract for stale popup size and empty suppression

**Files:**
- Create: `tests/test_v263_workspace_preview_resize.py`
- Test: `tests/test_v263_workspace_preview_resize.py`

**Interfaces:**
- Consumes: existing `openWorkspacePreview`, workspace popup component, v2.6.2 navigator selectors.
- Produces: a regression contract requiring a popup natural-size reset hook, zero-window suppression, and compact tile geometry.

- [ ] **Step 1: Write the failing tests**

Create tests asserting all of the following exact contracts:

```python
assert "set_default_size(-1, -1)" in workspace_state_or_popup_source
assert "GLib.idle_add" in workspace_state_source
assert "return items.length" in workspace_state_source
assert "const windowCount = openWorkspacePreview(id)" in workspaces_source
assert "if (windowCount === 0)" in workspaces_source
assert 'label="No preview available"' not in preview_source
assert 'class="workspace-empty compact"' not in preview_source
assert "<ClientIcon client={client} size={24} />" in preview_source
```

Also assert final CSS has `min-height: 42px` for `.workspace-window-grid-row` and `.workspace-window-grid-row .workspace-window-tile` and an approximately 24px icon rule.

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```bash
pytest -q tests/test_v263_workspace_preview_resize.py
```

Expected: FAIL because v2.6.2 has no natural-size reset, still renders an empty-state card, and uses 32px icons/52px rows.

### Task 2: Natural-size reset and zero-window suppression

**Files:**
- Modify: `components/PopupWindow.tsx`
- Modify: `components/PopupWindows.tsx`
- Modify: `components/Workspaces.tsx`
- Modify: `lib/workspacePreviewState.ts`
- Modify: `components/WorkspacePreview.tsx`

**Interfaces:**
- Produces: `setWorkspacePreviewWindow(window: Gtk.Window | null): void` and synchronous `openWorkspacePreview(localId: number): number`.
- Consumes: the existing Astal workspace popup and workspace hover entry path.

- [ ] **Step 1: Add an optional top-level window ref hook to `PopupWindow`**

Extend `PopupWindowProps` with:

```ts
windowRef?: (window: Astal.Window | null) => void
```

Call it when `$` receives the Astal window and with `null` during cleanup.

- [ ] **Step 2: Register the workspace popup window**

Pass the workspace-only `windowRef` from `PopupWindows.tsx` to `setWorkspacePreviewWindow`.

- [ ] **Step 3: Schedule natural-size resets after workspace snapshot changes**

In `workspacePreviewState.ts`, hold the registered window and schedule one GTK idle source:

```ts
workspacePreviewWindow?.set_default_size(-1, -1)
```

Cancel a pending idle before scheduling another. Call the scheduler after `setPreviewClients`, page reset, and client selection have been updated in `openWorkspacePreview`.

- [ ] **Step 4: Return the workspace client count**

Make `openWorkspacePreview(localId)` return `items.length` synchronously.

- [ ] **Step 5: Suppress empty workspace popups in the rail hover path**

In `Workspaces.tsx`:

```ts
const windowCount = openWorkspacePreview(id)
if (windowCount === 0) {
  closePanel("workspace")
  return
}
hoverPanel("workspace")
```

Keep `claimWorkspaceInteraction(id)` so the elastic rail still responds.

- [ ] **Step 6: Remove empty-state UI from `WorkspacePreview.tsx`**

Delete the `workspace-empty compact` content block. The navigator can keep an empty class internally, but it must never be presented because the popup stays closed for zero clients.

- [ ] **Step 7: Run the focused test**

Run:

```bash
pytest -q tests/test_v263_workspace_preview_resize.py
```

Expected: remaining failures only for compact icon geometry until Task 3.

### Task 3: Compact multi-window icon geometry

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Modify: `style.css`
- Test: `tests/test_v263_workspace_preview_resize.py`

**Interfaces:**
- Preserves: 8 items per page, 4 items per row, homogeneous full-width rows.
- Produces: 24px app icons in approximately 42px rows.

- [ ] **Step 1: Reduce the rendered icon request**

Change:

```tsx
<ClientIcon client={client} size={32} />
```

to:

```tsx
<ClientIcon client={client} size={24} />
```

- [ ] **Step 2: Add authoritative v2.6.3 CSS**

Append final rules using:

```css
.workspace-window-grid-row { min-height: 42px; }
.workspace-window-grid-row .workspace-window-tile { min-height: 42px; }
.workspace-window-tile-icon-shell { min-width: 32px; min-height: 32px; }
.workspace-window-tile .workspace-window-icon { min-width: 24px; margin: 4px; }
```

Keep the accepted border, selected, hover, and full-width homogeneous row behavior unchanged.

- [ ] **Step 3: Run focused GREEN**

Run:

```bash
pytest -q tests/test_v263_workspace_preview_resize.py
```

Expected: PASS.

### Task 4: Historical regression reconciliation and package verification

**Files:**
- Modify only historical tests whose assertions explicitly require the removed empty card or old 32px/52px tile geometry.
- Modify: `README.md`
- Modify: `install.sh`

**Interfaces:**
- Preserves every unrelated subsystem contract.
- Produces the v2.6.3 package metadata and downloadable ZIP.

- [ ] **Step 1: Run the complete suite**

```bash
pytest -q
```

Update only assertions that encode the superseded empty-state card or old icon geometry, then rerun until zero failures.

- [ ] **Step 2: Update metadata**

Set package/installer references to `v2.6.3` and document the stale-size reset, empty suppression, and smaller tile geometry.

- [ ] **Step 3: Run syntax and helper checks**

Run TypeScript/TSX parse checks, `bash -n` on shell scripts, installer smoke, capture helper, GTK/Gnim guard tests, and Waybar isolation checks.

- [ ] **Step 4: Package and verify the actual ZIP**

Create `/mnt/data/adaptive-glass-ags-v2.6.3.zip`, extract it into a fresh verification directory, rerun the full pytest suite and focused v2.6.3 contract from the extracted package, and verify installer/capture helper smoke tests.
