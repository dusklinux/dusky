from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def test_controls_use_plain_panel_triggers_not_hover_popovers():
    for rel in [
        'components/ClockCard.tsx',
        'components/NetworkControl.tsx',
        'components/AudioControl.tsx',
        'components/DisplayControl.tsx',
        'components/PowerControl.tsx',
    ]:
        s = read(rel)
        assert 'HoverPopover' not in s
        assert 'PanelTrigger' in s


def test_popup_state_has_fast_preview_pin_state_machine():
    s = read('lib/popupState.ts')
    assert 'createState' in s
    assert 'activePanel' in s
    assert 'pinnedPanel' in s
    assert 'hoverPanel' in s
    assert 'togglePin' in s
    assert 'leaveTrigger' in s
    assert 'enterPanel' in s
    assert 'leavePanel' in s
    match = re.search(r'CLOSE_DELAY_MS\s*=\s*(\d+)', s)
    assert match and int(match.group(1)) <= 100


def test_popup_windows_are_dedicated_nonexclusive_layer_surfaces():
    s = read('components/PopupWindow.tsx')
    assert '<window' in s
    assert 'Astal.Exclusivity.IGNORE' in s
    assert 'Astal.Layer.OVERLAY' in s
    assert 'Gtk.EventControllerMotion' in s
    assert 'visible=' in s
    assert '<popover' not in s
    assert '<menubutton' not in s


def test_app_renders_popup_windows_alongside_each_bar():
    s = read('app.tsx')
    assert 'PopupWindows' in s
    popups = read('components/PopupWindows.tsx')
    assert '<PopupWindows gdkmonitor={monitor} />' in s
    assert '<Bar gdkmonitor={gdkmonitor} />' in popups
    assert '<PopupWindows gdkmonitor={monitor}' in s


def test_workspace_number_click_is_plain_button_and_direct_switch():
    s = read('components/Workspaces.tsx')
    assert '<button' in s
    assert 'onClicked=' in s
    assert 'focusWorkspace(id)' in s
    assert 'HoverPopover' not in s
    assert 'openWorkspacePreview(id)' in s


def test_workspace_navigator_has_real_picture_not_schematic_map():
    s = read('components/WorkspacePreview.tsx')
    assert 'Gtk.Picture' in s
    assert 'workspace-preview-picture' in s
    assert 'workspace-window-tile' in s
    assert 'workspace-window-grid' in s
    assert 'MiniWindow' not in s
    assert 'workspace-preview-map' not in s
    assert 'workspace-mini-window' not in s


def test_workspace_rows_change_preview_on_hover_and_activate_exact_client_on_click():
    s = read('components/WorkspacePreview.tsx')
    assert 'Gtk.EventControllerMotion' in s
    assert 'selectPreviewClient' in s
    assert 'activatePreviewClient' in s
    assert 'onClicked=' in s


def test_capture_helper_prefers_grim_foreign_toplevel_and_has_legacy_fallback():
    s = read('scripts/capture_window_preview.sh')
    assert 'grim' in s
    assert 'hyprctl -j clients' in s
    assert 'stableId' in s
    assert '-T' in s
    assert '-w' in s
    assert 'mktemp' in s
    assert 'exit 1' in s


def test_workspace_state_captures_selected_client_and_uses_runtime_cache():
    s = read('lib/workspacePreviewState.ts')
    assert 'capture_window_preview.sh' in s
    assert 'XDG_RUNTIME_DIR' in s or 'get_user_runtime_dir' in s
    assert 'selectPreviewClient' in s
    assert 'openWorkspacePreview' in s
    assert 'capturing' in s


def test_exact_window_activation_switches_workspace_then_focuses_address():
    s = read('lib/workspacePreviewState.ts')
    assert 'await focusWorkspace(localId)' in s
    assert 'await focusWindow(address)' in s
    d = read('lib/dusky.ts')
    assert 'export async function focusWindow' in d
    assert 'hl.dsp.focus({ window =' in d
    assert 'address:0x' in d


def test_workspace_styles_have_large_preview_and_selected_row():
    s = read('style.css')
    for selector in [
        '.workspace-navigator',
        '.workspace-preview-stage',
        '.workspace-preview-picture',
        '.workspace-window-tile',
        '.workspace-window-tile.selected',
    ]:
        assert selector in s
