# Network Density Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the v2.0 Network Dashboard physically smaller and denser while increasing typography for readability.

**Architecture:** Preserve `NetworkControl.tsx` and `networkSession.ts` behavior completely. Implement the visual correction only in Network-specific CSS so live rates, session totals, quick actions, popup behavior, and integrations remain unchanged.

**Tech Stack:** AGS v3, Gnim JSX, GTK4 CSS, Matugen tokens.

## Global Constraints

- Do not change Network data/session logic.
- Do not change popup hover/click behavior.
- Do not modify Workspace, Calendar, Audio, Display, Power, or Media components.
- Network popup target width: 320 px minimum instead of 360 px.
- Increase important Network font sizes while reducing card and row heights.

---

### Task 1: Compact Network visual scale

**Files:**
- Modify: `style.css`
- Test: `tests/test_v202_network_density.py`

**Interfaces:**
- Consumes: existing `.network-*` class names from `NetworkControl.tsx`.
- Produces: compact Network layout with larger typography and unchanged behavior.

- [ ] **Step 1: Write a failing CSS-contract test** requiring the 320 px shell, reduced card/row heights, and larger text sizes.
- [ ] **Step 2: Run the focused test and verify RED against v2.0.1 styling.**
- [ ] **Step 3: Change only Network-specific CSS to satisfy the contract.**
- [ ] **Step 4: Run the focused test and complete historical regressions.**
- [ ] **Step 5: Verify TSX/component isolation and package the artifact.**
