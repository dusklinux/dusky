from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def test_workspace_preview_keeps_real_hero_preview_capability():
    s = read('components/WorkspacePreview.tsx')
    assert 'workspace-preview-hero' in s
    assert 'workspace-preview-hero-viewport' in s
    assert 'Gtk.Picture' in s
    assert 'previewPath' in s


def test_workspace_tiles_keep_selected_window_and_focus_affordances():
    s = read('components/WorkspacePreview.tsx')
    assert 'workspace-window-tile selected' in s
    assert 'workspace-preview-identity-app' in s
    assert 'workspace-preview-identity-title' in s
    assert 'tooltipText=' not in s
    assert 'selectedAddress((address)' in s
    assert 'activatePreviewClient' in s


def test_workspace_preview_keeps_gnim_fragment_safety_and_option_a_capture_contract():
    s = read('components/WorkspacePreview.tsx')
    state = read('lib/workspacePreviewState.ts')
    assert '<With' not in s
    assert s.count('<For each={previewPageClients((items) => items.slice') == 2
    assert 'selectPreviewClient(client.address)' in s
    assert 'if (selectedAddress.get() === address) return' in state
    assert 'chooseClient(client, true)' in state


def test_workspace_preview_css_keeps_cinematic_hero_and_polished_rows():
    css = read('style.css')
    assert '.workspace-preview-hero {' in css
    assert '.workspace-preview-hero-viewport {' in css
    assert '.workspace-window-tile.selected {' in css
    assert '.workspace-window-grid {' in css
    assert 'transition: 120ms ease' in css or 'transition: 140ms ease' in css
