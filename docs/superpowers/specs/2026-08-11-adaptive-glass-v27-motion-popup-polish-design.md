# Adaptive Glass v2.7 Motion And Popup Polish Design

Date: 2026-08-11

## Goal

Make Adaptive Glass feel smoother and more premium in the places the user touches most: the workspace pill, time pill, calendar popup, and shared popup entrances.

This pass should feel animated and expensive without making the bar noisy. Motion must respond to user intent, settle quickly, and preserve the compact Dusky top-bar footprint.

## User-Facing Behavior

Workspace pill:

- Hover transfer should feel smooth and magnetic.
- The recoil should be visibly stronger than the current snap, with a clear overshoot, compression, and settle.
- The recoil must not resize the measured workspace rail or push other pills around.
- Soft Magnetic should feel plush and elastic.
- Precise Futuristic should feel quicker, sharper, and less bouncy.

Time pill:

- Remove the accent dot from the time pill.
- Replace the single time label with individual digit slots.
- When a digit changes, the digit should roll vertically like a restrained casino reel.
- The time pill should remain compact and stable while digits animate.
- AM/PM remains readable, but should not glow or dominate the time.

Calendar popup:

- Keep the native GTK calendar and existing Today/Clocks actions.
- Polish the card into a denser frosted panel with better header hierarchy, softer dividers, and cleaner footer actions.
- The popup should feel connected to the time pill rather than appearing as a heavy floating sheet.

Shared popups:

- All popup frames should use a consistent reveal language: subtle opacity, slight vertical lift, and a tiny scale settle.
- Popup surfaces should have calmer borders and more refined shadow depth.
- The reveal timing should respect the selected motion style.

## Architecture

Keep the implementation in the existing AGS app:

- `ClockCard.tsx` owns the time pill and calendar popup content.
- `PopupWindow.tsx` owns shared popup window classes and visibility.
- `workspaceInteractionState.ts` owns workspace recoil timing.
- `Workspaces.tsx` keeps the measured rail layout and overlay magnetic shell.
- `style.css` carries the final visual overrides and motion keyframes.

Do not introduce a new animation library. GTK CSS animation and existing AGS state are sufficient for this pass.

## Workspace Motion

The workspace rail already uses `workspace-magnetic-shell` as an overlay child. This is the correct place to intensify recoil because it paints over the button without changing layout.

Add a stronger v2.7 keyframe:

- starts at rest
- expands horizontally beyond current peak
- briefly compresses below rest
- settles to transparent rest

Only animate paint-time properties:

- `transform`
- `opacity`
- `box-shadow`
- `background-image`
- `border-color`

Do not animate:

- `padding`
- `margin`
- `min-width`
- `width`
- `height`

Reduce the soft-magnetic snap delay enough that the recoil feels attached to hover. Keep precise-futuristic faster and less elastic.

## Time Reel

Split the time string into slots:

- digit slot for numbers
- separator slot for `:`
- meridiem label for `AM` or `PM`

Each digit slot receives a class that changes when the displayed character changes. CSS uses that class change to replay a vertical roll animation.

The reel should be restrained:

- no looping
- no huge bounce
- no glowing dot
- no layout shift when changing from one digit to another

The time pill remains the `calendar` trigger.

## Calendar Popup

Use the existing `CalendarPanel` structure but polish the visual hierarchy:

- compact date header with a quieter uppercase/kicker feel
- native calendar body with softer border and subtle inset depth
- clearer today and selected-day states
- footer actions that read like command chips, not large buttons

The calendar still uses GTK month navigation and the Today button still calls `calendar.select_day(GLib.DateTime.new_now_local())`.

## Shared Popup Reveal

Popup windows already become visible based on `activePanel()`. Add a reactive frame class so CSS can distinguish the visible/open state if needed.

The final frame should use a shared class like:

- `popup-window-frame popup-calendar popup-open`

CSS should provide a single reveal animation that is reused across calendar, workspace, media, network, audio, display, and power popups.

Motion modes can override duration/easing:

- Soft Magnetic: slower, softer settle
- Precise Futuristic: shorter, crisper settle

## Testing

Add focused contract tests for:

- stronger workspace recoil keyframes with a higher overshoot and visible compression
- no workspace recoil sizing properties
- reduced soft-magnetic snap delay compared with the prior `205ms`
- time pill has reel slots and no `clock-accent-dot`
- time pill keeps `panel="calendar"`
- clock reel CSS has vertical keyframes and fixed slot geometry
- calendar popup keeps native GTK calendar and Today/Clocks behavior
- calendar popup has v2.7 surface/header/footer polish tokens
- popup frame receives an open-state class and shared reveal CSS
- motion classes override popup reveal timings

Existing tests must continue to pass.

## Live Smoke

After implementation:

- install the branch into `/home/hangoma/.config/ags`
- switch to Adaptive Glass with Dusky's `bar_switch.sh`
- capture a top-bar screenshot
- open/hover popup surfaces if practical
- confirm `ags request -i dusky-adaptive-glass state` still works
- confirm the AGS log stays quiet

## Non-Goals

- Do not redesign the whole bar.
- Do not add a new Control Center setting in this pass.
- Do not remove Waybar fallback.
- Do not change workspace count or workspace focus behavior.
- Do not replace GTK Calendar with a custom calendar.
- Do not add continuous or idle animations.
- Do not use a large marketing-style visual treatment.
