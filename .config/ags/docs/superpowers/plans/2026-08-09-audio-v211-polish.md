# Audio v2.1.1 Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the compact Audio popup into explicit OUTPUT and INPUT sections with passive device labels, aligned mute-icon/slider/percentage control rows, and clear hover/press affordance.

**Architecture:** Keep AstalWp as the only audio backend and preserve the existing mute, volume, microphone activity, and Mixer behavior. Remove the inline device picker state from the popup so the current endpoint names are contextual labels rather than selector triggers. Constrain runtime changes to `components/AudioControl.tsx` and Audio-specific CSS in `style.css`.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalWp/WirePlumber, Python contract tests.

## Global Constraints

- No Visualizer.
- OUTPUT and INPUT must be visible section labels.
- Speaker and microphone icons remain the mute/unmute controls.
- Device names are passive labels, not inline picker triggers.
- Icon, slider, and percentage must align on one horizontal control axis in each section.
- Sliders remain thick and fully rounded.
- Icon hover/press/muted states must visibly communicate clickability.
- Mixer remains the only footer action.
- Do not change Network, Workspace, Calendar, Media, Display, Power, or popup-state logic.

---

### Task 1: Lock the v2.1.1 Audio contract

**Files:**
- Create: `tests/test_audio_v211_polish.py`

**Interfaces:**
- Consumes: `components/AudioControl.tsx`, Audio CSS in `style.css`.
- Produces: regression requirements for section labels, passive endpoint labels, aligned control rows, and button interaction styling.

- [ ] Write a failing Python test that requires `OUTPUT`, `INPUT`, `audio-control-line`, no `DevicePicker`, no `togglePicker`, hover/active icon CSS, and preserved thick slider geometry.
- [ ] Run the focused test and confirm RED against v2.1.

### Task 2: Simplify Audio component structure

**Files:**
- Modify: `components/AudioControl.tsx`

**Interfaces:**
- Consumes: AstalWp default speaker/microphone bindings.
- Produces: output/input sections with passive device labels and control rows.

- [ ] Remove picker state and endpoint picker UI.
- [ ] Render explicit `OUTPUT` and `INPUT` labels.
- [ ] Keep endpoint descriptions as passive labels.
- [ ] Put mute icon, slider, and percentage inside one `audio-control-line` per section.
- [ ] Preserve output mute, microphone mute, volume changes, microphone activity indicator, and Mixer action.

### Task 3: Polish compact visual alignment and affordance

**Files:**
- Modify: `style.css`

**Interfaces:**
- Consumes: new Audio class names from Task 2.
- Produces: aligned controls and subtle clickable-state feedback.

- [ ] Style section kickers and device labels with stronger hierarchy.
- [ ] Center mute icon and slider vertically on a single axis.
- [ ] Keep thick rounded Android-style slider track and thumb.
- [ ] Add subtle hover, active, focus-visible, and muted treatments to icon buttons without adding a permanent bubble.
- [ ] Keep popup compact and Mixer visually secondary.

### Task 4: Verify regression and package

**Files:**
- Update only superseded v2.1 assertions in `tests/test_audio_v21_contract.py`.
- Update: `README.md`.

**Interfaces:**
- Produces: installable `adaptive-glass-ags-v2.1.1.zip`.

- [ ] Run focused v2.1.1 contract.
- [ ] Run full Python regression suite.
- [ ] Run installer smoke, capture helper, Bash syntax, GTK compatibility scan, and TSX transpile/syntax verification.
- [ ] Confirm non-Audio runtime files are unchanged from v2.1.
- [ ] Build and test ZIP integrity.
