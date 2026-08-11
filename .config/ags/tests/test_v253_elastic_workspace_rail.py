from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def tail_after(css: str, marker: str) -> str:
    assert marker in css
    return css.split(marker, 1)[1]


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_rail_uses_shared_interaction_state_not_popup_state_for_elasticity():
    tsx = read("components/Workspaces.tsx")
    assert 'workspaceInteractionId' in tsx
    assert 'claimWorkspaceInteraction(id)' in tsx
    assert 'createState<number | null>(null)' not in tsx
    assert 'activePanel() === "workspace" ? previewWorkspaceLocalId() : active()' not in tsx
    assert 'workspace-selection-plate' not in tsx


def test_exactly_one_segment_owns_expanded_state():
    tsx = read("components/Workspaces.tsx")
    assert 'const expanded = interactionId === id || (interactionId === null && active() === id)' in tsx
    assert 'if (expanded) classes.push("expanded")' in tsx
    assert 'if (workspaceInteractionId() === id) classes.push("hovered")' in tsx
    assert 'if (active() === id) classes.push("active")' in tsx


def test_active_workspace_replaces_number_with_centered_dot_and_keeps_ring_class():
    tsx = read("components/Workspaces.tsx")
    assert 'class="workspace-active-dot"' in tsx
    assert 'class="workspace-number"' in tsx
    assert 'visible={isActive}' in tsx
    assert 'visible={isInactive}' in tsx
    assert 'class="workspace-button-content"' in tsx
    assert 'halign={Gtk.Align.CENTER}' in tsx
    assert 'valign={Gtk.Align.CENTER}' in tsx


def test_hover_preview_and_click_focus_behavior_are_preserved():
    tsx = read("components/Workspaces.tsx")
    assert 'openWorkspacePreview(id)' in tsx
    assert 'hoverPanel("workspace")' in tsx
    assert 'leaveTrigger("workspace")' in tsx
    assert 'void focusWorkspace(id)' in tsx


def test_elastic_css_has_separated_segments_stable_transfer_and_no_vertical_bounce():
    css = read("style.css")
    marker = '/* v2.5.3 — elastic workspace rail */'
    tail = tail_after(css, marker)

    deck = css_block(tail, '.workspace-deck')
    button = css_block(tail, '.workspace-button')
    expanded = css_block(tail, '.workspace-button.expanded')

    assert 'spacing' not in deck  # spacing is owned by JSX
    assert 'padding: 0 3px' in deck
    assert 'min-width: 27px' in button
    assert 'padding: 0' in button
    assert 'transition: 190ms ease' in button
    assert 'padding: 0 7px' in expanded
    assert 'translateY(' not in tail
    assert 'workspace-selection-plate' not in tail


def test_active_ring_dot_and_hover_sheen_are_visually_distinct():
    css = read("style.css")
    marker = '/* v2.5.3 — elastic workspace rail */'
    tail = tail_after(css, marker)

    active = css_block(tail, '.workspace-button.active')
    hovered = css_block(tail, '.workspace-button.hovered')
    dot = css_block(tail, '.workspace-active-dot')

    assert 'border-color: alpha(@primary, 0.58)' in active
    assert '0 0 0 1px alpha(@primary, 0.14)' in active
    assert 'background-image:' in hovered
    assert 'alpha(@secondary_container, 0.46)' in hovered
    assert 'min-width: 6px' in dot
    assert 'min-height: 6px' in dot
    assert 'border-radius: 999px' in dot
    assert '0 0 7px alpha(@primary, 0.52)' in dot


def test_active_compact_state_keeps_indicator_when_hover_moves_elsewhere():
    css = read("style.css")
    marker = '/* v2.5.3 — elastic workspace rail */'
    tail = tail_after(css, marker)
    compact = css_block(tail, '.workspace-button.active:not(.expanded)')
    assert 'border-color: alpha(@primary, 0.58)' in compact
    assert 'background: alpha(@primary_container, 0.14)' in compact


def test_frosted_mist_has_matching_elastic_active_and_hover_states():
    css = read("style.css")
    marker = '/* v2.5.3 — elastic workspace rail */'
    tail = tail_after(css, marker)
    assert '.theme-light .workspace-button.active' in tail
    assert '.theme-light .workspace-button.hovered' in tail
    assert '.theme-light .workspace-active-dot' in tail
