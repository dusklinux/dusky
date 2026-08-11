# Elastic Hover Ownership + Snapback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the hovered workspace's elastic expansion while the pointer moves into its preview, and add a horizontal overshoot→snapback→settle animation whenever elastic ownership transfers.

**Architecture:** Move workspace interaction ownership from component-local hover state into a small shared state module used by both `Workspaces.tsx` and `WorkspacePreview.tsx`. The rail and preview explicitly enter/leave that shared interaction zone. CSS keeps the existing active-dot/ring semantics, but `.expanded` receives a short horizontal keyframe animation that overshoots its resting width before settling.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalHyprland, existing Adaptive Glass popup state and CSS.

## Global Constraints

- Preserve v2.5.3 active dot + ring behavior.
- Preserve exact workspace switching and exact-window focus behavior.
- Hovering another workspace keeps the active workspace compact but still marked.
- Moving from a workspace pill into its preview must keep that hovered workspace expanded.
- Leaving both the workspace rail and preview restores expansion to the actual active workspace.
- The snap effect is horizontal width/padding only; no vertical translate or bounce.
- The rail's steady-state total width must stay stable during ownership transfer.
- Do not modify Media, Network, Audio, Display, Power, Calendar, capture logic, or Waybar.

---

### Task 1: Shared workspace interaction ownership

**Files:**
- Create: `lib/workspaceInteractionState.ts`
- Modify: `components/Workspaces.tsx`
- Modify: `components/WorkspacePreview.tsx`
- Modify: `lib/workspacePreviewState.ts`
- Test: `tests/test_v254_workspace_hover_ownership.py`

**Interfaces:**
- Produces: `workspaceInteractionId`, `claimWorkspaceInteraction(id)`, `enterWorkspaceRail()`, `leaveWorkspaceRail()`, `enterWorkspacePreview()`, `leaveWorkspacePreview()`, `clearWorkspaceInteraction()`.
- Consumes: the focused workspace from AstalHyprland remains local to `Workspaces.tsx`; popup visibility remains controlled by `popupState.ts`.

- [ ] **Step 1: Write the failing ownership contract** requiring shared state, rail/preview enter-leave hooks, no local `hoveredWorkspace` state, and explicit clear after exact-window activation.
- [ ] **Step 2: Run the focused test and confirm RED.**
- [ ] **Step 3: Implement shared interaction ownership with a short grace timer so rail→preview pointer travel does not release ownership.**
- [ ] **Step 4: Wire both surfaces to the shared state and clear it after exact-window activation.**
- [ ] **Step 5: Run the focused test and confirm GREEN.**

### Task 2: Horizontal magnetic snap animation

**Files:**
- Modify: `style.css`
- Test: `tests/test_v254_workspace_hover_ownership.py`

**Interfaces:**
- Consumes: `.workspace-button.expanded`, `.workspace-button.hovered`, `.workspace-button.active` classes from `Workspaces.tsx`.
- Produces: `@keyframes workspace-elastic-snap` and an expanded-state animation with a larger midpoint width/padding than the resting expanded state.

- [ ] **Step 1: Extend the focused contract to require horizontal overshoot, snapback, final 7px resting padding, lighting peak, and no `translateY`.**
- [ ] **Step 2: Run focused test and confirm RED.**
- [ ] **Step 3: Add the keyframe animation and restrained peak illumination.**
- [ ] **Step 4: Run focused test and full suite; update only historical tests that explicitly encode superseded v2.5.3 interaction details.**
- [ ] **Step 5: Run TS/TSX syntax, installer, capture, GTK compatibility, runtime isolation, ZIP extraction, and packaged-artifact verification.**
