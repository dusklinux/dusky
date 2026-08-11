from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(rel):
    return (ROOT / rel).read_text()


def test_legacy_menubutton_popover_is_not_used_by_live_controls():
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


def test_popup_layer_window_owns_hover_lifecycle():
    s = read('components/PopupWindow.tsx')
    assert 'Gtk.EventControllerMotion' in s
    assert 'enterPanel(id)' in s
    assert 'leavePanel(id)' in s
    assert 'Astal.Exclusivity.IGNORE' in s
    assert 'Astal.Layer.OVERLAY' in s


def test_workspace_focus_uses_direct_bash_exec_not_shell_string():
    s = read('lib/dusky.ts')
    assert 'const workspaceScript' in s
    assert 'execAsync(["bash", workspaceScript, "workspace", String(id)])' in s


def test_workspace_preview_has_immediate_snapshot_and_single_app_index():
    s = read('lib/workspacePreviewState.ts')
    assert 'const apps = new AstalApps.Apps()' in s
    assert s.count('new AstalApps.Apps()') == 1
    assert 'snapshotClients(localId)' in s
    assert 'setPreviewClients(items)' in s
    assert 'createPoll<PreviewClient[]>([], 450' not in s
