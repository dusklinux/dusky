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


def test_workspace_hover_ownership_is_shared_between_rail_and_preview():
    state = read("lib/workspaceInteractionState.ts") if (ROOT / "lib/workspaceInteractionState.ts").exists() else ""
    rail = read("components/Workspaces.tsx")
    preview = read("components/WorkspacePreview.tsx")

    assert 'workspaceInteractionId' in state
    assert 'claimWorkspaceInteraction' in state
    assert 'enterWorkspaceRail' in state
    assert 'leaveWorkspaceRail' in state
    assert 'enterWorkspacePreview' in state
    assert 'leaveWorkspacePreview' in state
    assert 'clearWorkspaceInteraction' in state
    assert 'createState<number | null>(null)' not in rail
    assert 'workspaceInteractionId() === id' in rail
    assert 'claimWorkspaceInteraction(id)' in rail
    assert 'leaveWorkspaceRail()' in rail
    assert 'enterWorkspacePreview()' in preview
    assert 'leaveWorkspacePreview()' in preview


def test_rail_to_preview_handoff_uses_shared_grace_zone_not_immediate_reset():
    state = read("lib/workspaceInteractionState.ts") if (ROOT / "lib/workspaceInteractionState.ts").exists() else ""
    rail = read("components/Workspaces.tsx")
    assert 'INTERACTION_RELEASE_DELAY_MS' in state
    assert 'railInside' in state
    assert 'previewInside' in state
    assert 'scheduleRelease()' in state
    assert 'if (railInside || previewInside) return' in state
    assert 'setHoveredWorkspace(null)' not in rail


def test_exact_window_activation_clears_workspace_interaction_after_focus_transfer():
    state = read("lib/workspacePreviewState.ts")
    assert 'clearWorkspaceInteraction' in state
    assert 'await focusWorkspace(localId)' in state
    assert 'await focusWindow(address)' in state
    assert 'clearWorkspaceInteraction()' in state


def test_elastic_snap_overshoots_horizontally_then_settles_without_vertical_bounce():
    css = read("style.css")
    assert '@keyframes workspace-overlay-magnetic-snap {' in css
    keyframes = css.split('@keyframes workspace-overlay-magnetic-snap {', 1)[1].split('.workspace-button {', 1)[0]
    expanded = css_block(css, '.workspace-button.expanded')
    snap_surface = css_block(css, '.workspace-magnetic-shell.snapping')

    assert 'transform: scaleX(1.20)' in keyframes
    assert 'transform: scaleX(0.96)' in keyframes
    assert 'transform: scaleX(1.00)' in keyframes
    assert 'padding:' not in keyframes
    assert 'box-shadow:' in keyframes
    assert 'animation: workspace-overlay-magnetic-snap 510ms' in snap_surface
    assert 'padding: 0 7px' in expanded
    assert 'translateY(' not in keyframes


def test_snap_lighting_peaks_then_returns_to_resting_hover_treatment():
    css = read("style.css")
    hovered = css_block(css, '.workspace-button.hovered')
    active = css_block(css, '.workspace-button.active')
    dot = css_block(css, '.workspace-active-dot')

    assert 'background-image:' in hovered
    assert 'alpha(@secondary_container, 0.46)' in hovered
    assert 'border-color: alpha(@primary, 0.58)' in active
    assert '0 0 7px alpha(@primary, 0.52)' in dot


def test_v254_keeps_preview_and_other_component_backends_unchanged():
    rail = read("components/Workspaces.tsx")
    preview_state = read("lib/workspacePreviewState.ts")
    assert 'openWorkspacePreview(id)' in rail
    assert 'void focusWorkspace(id)' in rail
    assert 'capture_window_preview.sh' in preview_state
    assert 'focusWindow(address)' in preview_state
