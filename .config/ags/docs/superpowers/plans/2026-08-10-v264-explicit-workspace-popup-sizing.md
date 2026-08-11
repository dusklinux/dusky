# Explicit Workspace Popup Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace v2.6.3's GTK natural-size reset with bounded, state-specific workspace popup sizes so multi-window previews stay compact while single-window previews shrink correctly and empty workspaces show no popup.

**Architecture:** Keep workspace content state and capture behavior unchanged. `workspacePreviewState.ts` will own a small pure size policy keyed by client count and schedule `set_default_size(width, height)` after each non-empty snapshot; zero-window workspaces remain suppressed by `Workspaces.tsx`. `WorkspacePreview.tsx` will explicitly keep `Gtk.Picture.canShrink=true`, and existing 24px icons / 42px rows remain unchanged.

**Tech Stack:** AGS v3/Gnim TSX, GTK4/Astal, TypeScript, pytest source-contract tests, Bash packaging.

## Global Constraints

- Preserve v2.6.3 workspace rail, magnetic shell, rail-to-preview ownership, capture helper, exact-window focus, pagination, and Waybar fallback isolation.
- Empty workspaces must never open the workspace popup.
- Single-window popup target: 308×230.
- 2–4 window popup target: 356×320.
- 5+ window popup target: 356×365.
- Never call `set_default_size(-1, -1)` for workspace preview resizing.
- Keep `Gtk.Picture` shrinkable and `Gtk.ContentFit.CONTAIN`.
- Keep multi-window icons at 24px and rows at 42px.

---

### Task 1: Replace natural sizing with explicit state sizing

**Files:**
- Modify: `lib/workspacePreviewState.ts`
- Test: `tests/test_v264_explicit_workspace_popup_size.py`

**Interfaces:**
- Produces: `workspacePopupSizeForCount(count: number): { width: number; height: number } | null`
- Produces: `requestWorkspacePopupSize(count: number): void`
- Consumes: the `WorkspacePreviewWindow.set_default_size(width, height)` callback already registered by `PopupWindows.tsx`.

- [ ] **Step 1: Write the failing test**

Create a source-contract test that requires the three approved size states, rejects `set_default_size(-1, -1)`, requires a deferred positive-size update after `openWorkspacePreview()`, and confirms zero clients map to no popup size.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_v264_explicit_workspace_popup_size.py`
Expected: FAIL because v2.6.3 still uses `requestWorkspaceNaturalSizeReset()` and `set_default_size(-1, -1)`.

- [ ] **Step 3: Implement the explicit size policy**

Use this policy in `workspacePreviewState.ts`:

```ts
export function workspacePopupSizeForCount(count: number) {
  if (count <= 0) return null
  if (count === 1) return { width: 308, height: 230 }
  if (count <= 4) return { width: 356, height: 320 }
  return { width: 356, height: 365 }
}
```

Schedule the positive `set_default_size(width, height)` on the GTK idle queue after snapshot state changes, cancel stale scheduled sizing work, and queue a resize afterward.

- [ ] **Step 4: Run focused test**

Run: `pytest -q tests/test_v264_explicit_workspace_popup_size.py`
Expected: PASS.

### Task 2: Make shrinkability explicit and preserve compact geometry

**Files:**
- Modify: `components/WorkspacePreview.tsx`
- Test: `tests/test_v264_explicit_workspace_popup_size.py`

**Interfaces:**
- Consumes: preview client state from `workspacePreviewState.ts`.
- Preserves: 276×138 single-window hero, 320×160 multi-window hero, 24px icons, 42px multi rows.

- [ ] **Step 1: Extend the failing contract**

Require `canShrink={true}` and `Gtk.ContentFit.CONTAIN`; require the existing compact icon/row geometry to remain present.

- [ ] **Step 2: Verify RED for explicit boolean form if needed**

Run the focused test and confirm it fails only if the property is not explicit.

- [ ] **Step 3: Make `canShrink` explicit without changing capture behavior**

Change the picture property to `canShrink={true}`. Do not change capture commands or content fit.

- [ ] **Step 4: Run focused test**

Expected: PASS.

### Task 3: Regression and package verification

**Files:**
- Modify only historical tests whose assertions explicitly require the retired v2.6.3 natural-size reset.
- Modify: `README.md`, `install.sh` version metadata.

**Interfaces:**
- Preserves all historical runtime contracts except the intentionally superseded natural-size reset.

- [ ] **Step 1: Run full pytest suite**

Run: `pytest -q`
Expected: only historical assertions for `set_default_size(-1, -1)` may fail.

- [ ] **Step 2: Update only superseded historical assertions**

Retain tests for empty suppression, single/multi state geometry, 24px icons, 42px rows, active-workspace suppression, and preview ownership.

- [ ] **Step 3: Run syntax/smoke guards**

Run the established TypeScript transpile, shell syntax, installer smoke, capture helper, GTK/Gnim compatibility, Waybar isolation, and runtime-diff checks.

- [ ] **Step 4: Package and verify extracted ZIP**

Create `/mnt/data/adaptive-glass-ags-v2.6.4.zip`, extract it into a fresh verification directory, rerun the full suite plus the focused v2.6.4 contract, and confirm metadata/version integrity.
