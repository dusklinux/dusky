# Adaptive Glass v2.6 Workspace Navigator Design

## Goal
Turn the workspace popup into a scalable navigator for non-active workspaces while preserving the accepted v2.5.7 workspace rail.

## Preview visibility
- Hovering the active workspace never opens the workspace preview.
- If a non-active workspace preview is open and the pointer moves onto the active workspace, close the workspace preview immediately.
- The active workspace keeps its rail animation, dot, ring, elastic state, and magnetic shell behavior.
- Hovering a non-active workspace opens its preview and rail-to-preview ownership remains intact.

## Window inventory and pagination
- Remove the eight-client truncation. Snapshot every visible mapped client on the target workspace.
- Display 12 clients per page in a 6-column by 2-row icon grid.
- Reset to page 1 whenever a different workspace preview opens.
- Show previous/next page controls and a `current / total` indicator only when more than one page exists.
- Previous/next controls stop at the first/last page.
- Hovering a tile selects that exact client and refreshes the hero preview; clicking focuses that exact client on the target workspace.

## Hero preview
- Preserve the complete captured window and its aspect ratio.
- Allocate the hero picture to the full 390 x 219 viewport so narrow or tiny captured windows are scaled up as far as the viewport permits without cropping.
- Keep the capture fresh-on-selection model rather than continuous streaming.

## Selected-window identity
- Add a compact identity strip directly beneath the hero.
- Show the selected app name prominently and its window title beneath it, with a small app icon.
- Keep native tile tooltips as secondary detail rather than the primary identification mechanism.

## Density
- Reduce navigator header-to-hero whitespace.
- Reduce outer navigator spacing while keeping the 390 px hero width.
- Keep the 6 x 2 tile grid compact enough to fit without widening the popup excessively.

## Scope
- No changes to workspace rail geometry, active-dot semantics, magnetic shell, media, network, audio, display, power, calendar, or Waybar fallback.
