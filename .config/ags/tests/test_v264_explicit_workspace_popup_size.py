from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATE = (ROOT / 'lib/workspacePreviewState.ts').read_text()
PREVIEW = (ROOT / 'components/WorkspacePreview.tsx').read_text()
CSS = (ROOT / 'style.css').read_text()
WORKSPACES = (ROOT / 'components/Workspaces.tsx').read_text()


def test_workspace_popup_uses_explicit_count_based_sizes_not_natural_reset():
    assert 'workspacePopupSizeForCount' in STATE
    assert 'if (count <= 0) return null' in STATE
    assert 'if (count === 1) return { width: 308, height: 230 }' in STATE
    assert 'if (count <= 4) return { width: 356, height: 320 }' in STATE
    assert 'return { width: 356, height: 365 }' in STATE
    assert 'set_default_size(-1, -1)' not in STATE


def test_non_empty_open_schedules_explicit_popup_resize():
    assert 'requestWorkspacePopupSize(items.length)' in STATE
    assert re.search(r'workspacePreviewWindow\?\.set_default_size\(size\.width, size\.height\)', STATE)
    assert 'GLib.idle_add' in STATE
    assert 'queue_resize' in STATE


def test_picture_is_explicitly_shrinkable_and_contained():
    assert 'canShrink={true}' in PREVIEW
    assert 'contentFit={Gtk.ContentFit.CONTAIN}' in PREVIEW


def test_v263_compact_tile_geometry_is_preserved():
    marker = 'v2.6.3 — natural popup resize + tighter multi-window tiles'
    block = CSS.split(marker, 1)[1]
    assert 'min-height: 42px' in block
    assert 'min-width: 24px' in block
    assert 'min-width: 32px' in block


def test_empty_workspace_still_closes_without_opening_popup():
    assert 'const windowCount = openWorkspacePreview(id)' in WORKSPACES
    assert 'if (windowCount === 0)' in WORKSPACES
    zero_branch = WORKSPACES.split('if (windowCount === 0)', 1)[1].split('}', 1)[0]
    assert 'closePanel("workspace")' in zero_branch
    assert 'hoverPanel("workspace")' not in zero_branch
