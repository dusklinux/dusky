from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def test_workspace_preview_avoids_gnim_nested_with_fragments():
    s = read('components/WorkspacePreview.tsx')
    # Gnim v3 rejects a <With> rendered from another <With> callback with
    # "nesting Fragments are not yet supported". Keep this component fragment-free.
    assert '<With' not in s
    assert 'from "ags"' in s
    assert 'For' in s


def test_workspace_preview_keeps_real_picture_and_window_tiles_after_fragment_fix():
    s = read('components/WorkspacePreview.tsx')
    assert '<Gtk.Picture' in s
    assert s.count('<For each={previewPageClients((items) => items.slice') == 2
    assert 'workspace-window-grid' in s
    assert 'selectPreviewClient' in s
    assert 'activatePreviewClient' in s
    assert 'previewPath((path)' in s
