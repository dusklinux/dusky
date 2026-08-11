# Adaptive Glass AGS v1.5 Design

## Purpose

v1.5 replaces the repeatedly unreliable Gtk.MenuButton/Gtk.Popover hover architecture and the schematic workspace activity card. The goal is fast, predictable shell interaction and a workspace navigator that displays a fresh real thumbnail of the selected window.

## Popup interaction architecture

Calendar, Network, Audio, Display, Power, and Workspace surfaces are dedicated non-exclusive Astal layer-shell windows. Bar controls are ordinary Gtk.Button widgets with pointer motion controllers. A single global popup controller owns `activePanel` and `pinnedPanel` state so only one surface is active at a time.

Hover has no opening timer. Leaving a trigger uses only an 80 ms bridge delay so the pointer can cross the gap into the panel. Entering the panel cancels that close. Leaving the panel closes it when unpinned. Clicking Calendar/Network/Audio/Display/Power toggles pinned state. Workspace buttons never pin: click always switches workspace.

## Workspace navigator

Hovering a workspace snapshots AstalHyprland clients immediately and opens the navigator before screenshot work completes. The target workspace follows Dusky's per-monitor bank convention. Clients are sorted by focus history so the most recently used window becomes the default selection.

The card contains one large preview stage and one compact window list. There is no duplicate schematic map. Hovering a list row changes selection immediately, clears the previous screenshot, and asynchronously captures one fresh thumbnail. Clicking a row closes the navigator, switches to the selected workspace through `multi_monitor_workspace.sh`, then focuses the exact Hyprland `address:0x...` window.

## Thumbnail capture

Option A is used: one fresh thumbnail on workspace-open or row-hover, not a continuously streaming preview. The helper maps the Astal/Hyprland window address to Hyprland's `stableId` using `hyprctl -j clients`, then asks modern grim to capture that foreign toplevel with `-T`. A legacy `grim -w` address fallback remains for older Hyprland-oriented grim builds.

The helper stores files only below `$XDG_RUNTIME_DIR` and never modifies a client's screen-share property. If capture is unavailable or blocked, the shell remains functional and shows a selected-app fallback while exact window navigation continues to work.

## Visual hierarchy

The workspace surface is compact and intentional: header and workspace badge, 16:9 dark preview stage, selected-window highlight, app icon, app name/title, and a subtle open indicator. Dedicated popup windows reuse the Matugen glass palette and stronger elevation rather than GTK's popover chrome.

## Scope boundaries

v1.5 does not add continuous thumbnail streaming, a compositor plugin, a full-screen outside-click catcher, CPU/RAM cards, tray redesign, or the future Dusky Waybar/AGS backend manager. Those remain later phases after live interaction and capture behavior are verified.
