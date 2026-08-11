from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_uses_elastic_segment_transfer_instead_of_selection_plate():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")

    assert '<box class="workspace-switcher">' in tsx
    assert 'workspaceInteractionId' in tsx
    assert 'workspace-selection-plate' not in tsx
    assert 'const expanded = interactionId === id || (interactionId === null && active() === id)' in tsx
    assert '<box class="workspace-deck" spacing={3}>' in tsx

    expanded = css_block(css, '.workspace-button.expanded')
    button = css_block(css, '.workspace-button')
    assert 'padding: 0 7px' in expanded
    assert 'transition: 190ms ease' in button


def test_workspace_active_and_hover_states_are_distinct():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")
    active = css_block(css, '.workspace-button.active')
    hovered = css_block(css, '.workspace-button.hovered')

    assert 'workspace-active-dot' in tsx
    assert 'border-color: alpha(@primary, 0.58)' in active
    assert '0 0 0 1px alpha(@primary, 0.14)' in active
    assert 'background-image:' in hovered
    assert 'alpha(@secondary_container, 0.46)' in hovered

def test_workspace_preview_is_softer_lighter_and_keeps_hero_dimensions():
    css = read("style.css")
    tsx = read("components/WorkspacePreview.tsx")

    popup = css_block(css, '.popup-workspace')
    hero = css_block(css, '.workspace-preview-hero')
    assert 'alpha(@outline_variant, 0.16)' in popup
    assert '0 12px 28px alpha(#000000, 0.28)' in popup
    assert 'alpha(@surface_container_high, 0.94)' in popup
    assert 'alpha(@outline_variant, 0.14)' in hero
    assert '0 5px 14px alpha(#000000, 0.17)' in hero
    assert 'items.length === 1 ? 276 : 320' in tsx
    assert 'items.length === 1 ? 138 : 160' in tsx


def test_workspace_window_grid_icons_and_heading_are_more_readable():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")

    assert '<ClientIcon client={client} size={24} />' in tsx
    tile = css_block(css, '.workspace-window-tile')
    shell = css_block(css, '.workspace-window-tile-icon-shell')
    heading = css_block(css, '.workspace-window-list-title')
    assert 'min-width: 56px' in tile
    assert 'min-height: 52px' in tile
    assert 'min-width: 32px' in shell
    assert 'min-height: 32px' in shell
    assert 'font-size: 10px' in heading
    assert 'workspace-preview-identity-title' in tsx
    assert 'tooltipText=' not in tsx
    assert 'selectPreviewClient(client.address)' in tsx
    assert 'activatePreviewClient(previewWorkspaceLocalId.get(), client.address)' in tsx


def test_clock_has_stronger_hierarchy_without_changing_calendar_trigger():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")

    assert 'panel="calendar"' in tsx
    assert 'ClockReelDigit' in tsx
    assert 'class="clock-reel"' in tsx
    assert 'class="clock-reel-separator"' in tsx
    assert 'class="clock-accent-dot"' not in tsx
    card = css_block(css, '.clock-card')
    digit = css_block(css, '.clock-reel-digit')
    face = css_block(css, '.clock-reel-digit-face')
    meridiem = css_block(css, '.clock-meridiem')
    dot = css_block(css, '.clock-accent-dot')
    assert 'min-width: 0' in card
    assert 'padding: 0 9px' in card
    assert 'border-radius: 10px' in card
    assert 'animation: clock-v27-reel-in' in digit
    assert 'font-size: 13px' in face
    assert 'font-size: 9px' in meridiem
    assert 'alpha(@on_surface_variant, 0.68)' in meridiem
    assert 'min-width: 0' in dot
    assert 'min-height: 0' in dot
    assert 'opacity: 0' in dot
    assert 'box-shadow: none' in dot


def test_all_top_bar_pills_share_one_geometry_language():
    css = read("style.css")
    assert 'v2.5.1 — unified top-bar geometry' in css
    unified = css_block(css, '.launcher-card,\n.weather-card,\n.notification-card,\n.clock-card,\n.control-leader,\n.battery-card,\n.media-card')
    assert 'min-height: 28px' in unified
    assert 'border-radius: 10px' in unified
    assert 'border-width: 1px' in unified
    assert 'border-style: solid' in unified
    assert '0 3px 8px alpha(#000000, 0.14)' in unified

    deck = css_block(css, '.workspace-deck')
    assert 'min-height: 28px' in deck
    assert 'border-radius: 10px' in deck


def test_media_visual_footprint_is_normalized_without_touching_mpris_logic():
    tsx = read("components/MediaCard.tsx")
    assert 'pixelSize={24}' in tsx
    assert 'pixelSize={16}' in tsx
    assert 'additionalPlayingPlayers(snapshot)' in tsx
    assert 'popupAvailable && hoverPanel("media")' in tsx
    assert 'player.play_pause()' in tsx
