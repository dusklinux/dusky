from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_workspace_interaction_state_has_delayed_transient_snap_state():
    state = read('lib/workspaceInteractionState.ts')
    assert 'SNAP_DELAY_MS = 190' in state
    assert 'SNAP_PULSE_MS = 510' in state
    assert 'workspaceSnapId' in state
    assert 'setWorkspaceSnapId' in state
    assert 'snapDelayTimer' in state
    assert 'snapClearTimer' in state
    assert 'GLib.timeout_add' in state
    assert 'setWorkspaceSnapId(id)' in state
    assert 'setWorkspaceSnapId(null)' in state


def test_workspaces_has_inner_snap_surface_bound_to_transient_snap_state():
    src = read('components/Workspaces.tsx')
    assert 'workspaceSnapId' in src
    assert 'workspace-magnetic-shell' in src
    assert 'snapping' in src
    assert 'workspaceSnapId() === id' in src


def test_resting_elastic_layout_is_unchanged_and_outer_expansion_does_not_animate_snap():
    css = read('style.css')
    expanded = re.findall(r'\.workspace-button\.expanded\s*\{([^}]*)\}', css, re.S)
    assert expanded
    final = expanded[-1]
    assert 'padding: 0 5px' in final
    assert 'animation:' not in final


def test_inner_snap_surface_owns_delayed_centered_pulse():
    css = read('style.css')
    assert '@keyframes workspace-overlay-magnetic-snap' in css
    assert 'transform: scaleX(1.20)' in css
    assert 'transform: scaleX(0.96)' in css
    assert '.workspace-magnetic-shell.snapping' in css
    snap = re.search(r'\.workspace-magnetic-shell\.snapping\s*\{([^}]*)\}', css, re.S)
    assert snap
    assert '510ms' in snap.group(1)
    assert 'workspace-overlay-magnetic-snap' in snap.group(1)
    assert 'translateY' not in css[css.find('@keyframes workspace-overlay-magnetic-snap'):css.find('@keyframes workspace-overlay-magnetic-snap')+1200]


def test_v255_direct_expanded_animation_is_removed():
    css = read('style.css')
    assert 'animation: workspace-elastic-snap 420ms' not in css
