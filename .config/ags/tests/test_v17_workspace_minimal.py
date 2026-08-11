from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def test_workspace_header_is_minimal_and_not_redundant():
    s = read('components/WorkspacePreview.tsx')
    assert 'workspace-preview-header-icon' in s
    assert 'workspace-preview-header-title' in s
    assert 'workspace-preview-header-subtitle' in s
    assert 'workspace-preview-close' in s
    assert 'Choose a window' not in s
    assert 'workspace-preview-badge' not in s
    assert 'workspace-preview-count-chip' not in s
    assert 'workspace-preview-mode-chip' not in s


def test_workspace_hero_is_dominant_without_heavy_duplicate_metadata():
    s = read('components/WorkspacePreview.tsx')
    assert 'workspace-preview-hero' in s
    assert 'workspace-preview-hero-viewport' in s
    assert 'workspace-preview-hero-meta' not in s
    assert 'workspace-preview-hero-footer' not in s
    assert 'workspace-preview-live-chip' not in s
    assert 'SNAPSHOT' not in s
    assert 'REFRESHING' not in s


def test_window_tiles_are_compact_icon_only_and_unumbered():
    s = read('components/WorkspacePreview.tsx')
    assert 'workspace-window-tile selected' in s
    assert 'workspace-window-grid' in s
    assert 'workspace-preview-identity-app' in s
    assert 'tooltipText=' not in s
    assert 'workspace-window-index' not in s
    assert 'workspace-window-selected-dot' not in s
    assert 'workspace-window-copy' not in s


def test_working_navigation_and_capture_contract_is_preserved():
    s = read('components/WorkspacePreview.tsx')
    state = read('lib/workspacePreviewState.ts')
    assert '<With' not in s
    assert s.count('<For each={previewPageClients((items) => items.slice') == 2
    assert 'selectPreviewClient(client.address)' in s
    assert 'activatePreviewClient(previewWorkspaceLocalId.get(), client.address)' in s
    assert 'if (selectedAddress.get() === address) return' in state
    assert 'chooseClient(client, true)' in state


def test_v17_css_has_minimal_reference_direction():
    css = read('style.css')
    assert 'v1.7 — minimal workspace navigator' in css
    assert '.workspace-preview-header-icon {' in css
    assert '.workspace-preview-header-title {' in css
    assert '.workspace-preview-close {' in css
    assert '.workspace-preview-hero-viewport {' in css
    assert '.workspace-window-row.selected {' in css
    assert 'min-width: 460px' in css
    assert 'min-height: 259px' in css
