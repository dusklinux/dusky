# Compact Dual-Row Audio Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the prototype Audio popup with a compact two-row output/input control using click-to-mute icons, thick rounded sliders, clickable device names with inline device selection, and a single Mixer action.

**Architecture:** Keep AstalWp as the only audio backend. Bind to `AstalWp.Audio` default speaker/microphone and endpoint lists, use endpoint `mute`, `volume`, `description`, `volumeIcon`, and `is-default`, and keep the device picker inline so the popup only grows while a device is being selected. Remove the visualizer entirely.

**Tech Stack:** AGS v3, Gnim JSX, GTK4, AstalWp/WirePlumber, existing Dusky `runAudioMixer()` integration.

## Global Constraints

- Keep the popup compact: target roughly 280–300 px minimum width.
- No duplicated output/microphone mute icons.
- Speaker icon toggles speaker mute; microphone icon toggles microphone mute.
- Device name itself opens the corresponding inline device selector.
- Sliders use a thick rounded Android-like track and thumb.
- Keep only `Mixer ›` as the footer action.
- Do not change Network, Workspace, Calendar, Media, Display, Power, popup state, or Waybar.

---

### Task 1: Audio component contract

**Files:**
- Modify: `components/AudioControl.tsx`
- Test: `tests/test_audio_v21_contract.py`

**Interfaces:**
- Consumes: `AstalWp.get_default().audio`, `Endpoint.set_mute()`, `Endpoint.set_volume()`, `Endpoint.set_is_default()`.
- Produces: `AudioPanel()` with output/input rows, inline pickers, and Mixer action.

- [ ] Write a failing structural test for no Visualizer, speaker/mic mute buttons, clickable device names, input slider, device pickers, and one Mixer action.
- [ ] Run it and confirm it fails against v2.0.3 behavior.
- [ ] Implement the minimal reactive component.
- [ ] Run the focused test and make it pass.

### Task 2: Compact Android-style audio CSS

**Files:**
- Modify: `style.css`
- Test: `tests/test_audio_v21_contract.py`

**Interfaces:**
- Consumes: classes emitted by `AudioControl.tsx`.
- Produces: compact panel geometry, thick rounded scales, clickable text-like device-name buttons, compact inline picker rows.

- [ ] Extend the failing test with width/slider/style assertions.
- [ ] Run and confirm failure.
- [ ] Add only Audio-specific CSS.
- [ ] Run focused and historical suites.

### Task 3: Packaging and isolation verification

**Files:**
- Modify: `README.md`
- Package: `/mnt/data/adaptive-glass-ags-v2.1.zip`

- [ ] Verify the historical regression suite.
- [ ] Verify Bash syntax, installer smoke, TS/TSX syntax, GTK compatibility, and ZIP integrity.
- [ ] Confirm runtime diffs are limited to Audio component/CSS plus tests/docs.
