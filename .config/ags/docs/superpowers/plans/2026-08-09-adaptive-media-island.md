# Adaptive Media Island Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace duplicate MPRIS cards with one adaptive bar island owned by the newest actively playing source, plus a hover/pinnable popup for all media sources and controls.

**Architecture:** A dedicated media-state helper tracks playback transitions per MPRIS bus name and records recency. The bar renders only the selected primary player; the media popup renders primary first, then other playing sources, then paused sources. The popup uses the existing layer-shell popup controller so hover, grace-close, and pin behavior match Workspace and other Adaptive Glass surfaces.

**Tech Stack:** AGS v3, GTK4/Gnim JSX, AstalMpris, AstalApps, existing PopupWindow/popupState architecture.

## Global Constraints

- Exactly one media island on the bar.
- Newest source transitioning to PLAYING becomes primary.
- When primary stops/pauses, fall back to another currently playing source before paused sources.
- Hovering the island opens a media popup; pointer transfer keeps it open; clicking pins/unpins it.
- Popup order: primary, other playing, paused; unavailable/stopped stale sources are not shown.
- Primary popup section has richer artwork/metadata/full transport controls; secondary rows have source-specific controls.
- Bar island width is content-driven: compact for short metadata, capped for long metadata, with marquee/ellipsis only past the cap.
- Dark and Frosted Mist styling are both supported.

---

### Task 1: Media source priority state

**Files:**
- Create: `lib/mediaState.ts`
- Test: `tests/test_v24_media_island.py`

**Interfaces:**
- Consumes: MPRIS player objects with `busName`, `playbackStatus`, `available`, title/identity metadata.
- Produces: deterministic recency ordering and primary-source selection helpers.

- [ ] **Step 1: Write the failing source-priority tests.**
- [ ] **Step 2: Run focused tests and confirm RED.**
- [ ] **Step 3: Implement transition tracking and primary fallback logic.**
- [ ] **Step 4: Run focused tests and confirm GREEN.**

### Task 2: Single adaptive bar island

**Files:**
- Modify: `components/MediaCard.tsx`
- Modify: `style.css`
- Modify: `lib/popupState.ts`
- Test: `tests/test_v24_media_island.py`

**Interfaces:**
- Consumes: primary-source selector from Task 1.
- Produces: exactly one `media-card`, content-driven metadata copy, `PanelTrigger panel="media"`.

- [ ] **Step 1: Extend the failing contract for one island and dynamic sizing.**
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Implement one primary MediaCard with content-driven width and capped marquee.**
- [ ] **Step 4: Confirm GREEN.**

### Task 3: Hover/pinnable multi-source popup

**Files:**
- Create: `components/MediaPanel.tsx`
- Modify: `components/PopupWindows.tsx`
- Modify: `style.css`
- Test: `tests/test_v24_media_island.py`

**Interfaces:**
- Consumes: media priority/order state and existing PopupWindow interaction controller.
- Produces: popup `id="media"`, primary rich controls, source-specific secondary rows.

- [ ] **Step 1: Extend contract for popup ordering and per-player controls.**
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Implement MediaPanel and register media popup.**
- [ ] **Step 4: Run focused/full tests and verify package.**
