from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def read(rel):
    return (ROOT / rel).read_text()


def test_popup_state_has_preview_pin_state_machine():
    s = read('lib/popupState.ts')
    assert 'CLOSE_DELAY_MS =' in s
    assert 'activePanel' in s
    assert 'pinnedPanel' in s
    assert 'GLib.timeout_add' in s
    assert 'leaveTrigger' in s
    assert 'togglePin' in s
    assert 'closePanels' in s

def test_workspace_uses_hover_preview_but_click_switches():
    s = read('components/Workspaces.tsx')
    assert '<button' in s
    assert 'openWorkspacePreview(id())' in s
    assert 'hoverPanel("workspace")' in s
    assert 'onClicked=' in s
    assert 'focusWorkspace(id())' in s
    assert 'HoverPopover' not in s


def test_workspace_preview_uses_native_clients_and_apps():
    state = read('lib/workspacePreviewState.ts')
    view = read('components/WorkspacePreview.tsx')
    assert 'gi://AstalHyprland' in state
    assert 'gi://AstalApps' in state
    assert 'get_clients' in state
    assert 'exact_query' in state
    assert 'fuzzy_query' in state
    assert 'Gtk.Picture' in view
    assert 'workspace-window-list' in view
    assert 'workspace-preview-map' not in view
    assert 'workspace-mini-window' not in view
    assert 'Preview unavailable' in view
    assert 'focusedMonitor' in state
    assert '* 10 + localId' in state

def test_workspace_preview_styles_and_active_glow_exist():
    s = read('style.css')
    for selector in ['.workspace-navigator', '.workspace-preview-stage', '.workspace-preview-picture', '.workspace-window-row']:
        assert selector in s
    active = re.search(r'\.workspace-button\.active\s*\{(.*?)\}', s, re.S)
    assert active
    assert 'box-shadow' in active.group(1)
    assert '@primary' in active.group(1) or '@tertiary' in active.group(1)


def test_bar_remains_transparent_and_popover_hierarchy_is_polished():
    s = read('style.css')
    bar = re.search(r'\.bar-shell\s*\{(.*?)\}', s, re.S)
    assert bar and 'background: transparent' in bar.group(1)
    assert '.panel-kicker' in s and '.panel-title' in s
    assert 'border-top' in s or 'box-shadow' in s


def test_network_terminal_has_fallbacks():
    s = read('lib/dusky.ts')
    assert 'command -v foot' in s
    assert 'command -v kitty' in s
    assert 'command -v wezterm' in s
    assert 'runNetworkManager' in s
