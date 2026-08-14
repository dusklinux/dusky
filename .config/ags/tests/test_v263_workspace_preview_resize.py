from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_popup_has_explicit_size_reset_hook_after_preview_change():
    state = read("lib/workspacePreviewState.ts")
    popup = read("components/PopupWindow.tsx")
    popup_windows = read("components/PopupWindows.tsx")
    assert "workspacePopupSizeForCount" in state
    assert "set_default_size(size.width, size.height)" in state
    assert "set_default_size(-1, -1)" not in state
    assert "GLib.idle_add" in state
    assert "setWorkspacePreviewWindow" in state
    assert "windowRef?:" in popup
    assert "setWorkspacePreviewWindow" in popup_windows


def test_open_preview_returns_count_and_empty_workspace_never_opens_panel():
    state = read("lib/workspacePreviewState.ts")
    workspaces = read("components/Workspaces.tsx")
    assert "return items.length" in state
    assert "const windowCount = openWorkspacePreview(id())" in workspaces
    assert "if (windowCount === 0)" in workspaces
    empty_branch = workspaces.split("if (windowCount === 0)", 1)[1].split("hoverPanel(\"workspace\")", 1)[0]
    assert 'closePanel("workspace")' in empty_branch


def test_empty_workspace_has_no_preview_ui_at_all():
    src = read("components/WorkspacePreview.tsx")
    assert 'label="No preview available"' not in src
    assert 'class="workspace-empty compact"' not in src


def test_multi_window_icons_are_smaller_without_changing_4x2_paging():
    src = read("components/WorkspacePreview.tsx")
    state = read("lib/workspacePreviewState.ts")
    assert "PREVIEW_PAGE_SIZE = 8" in state
    assert "items.slice(0, 4)" in src
    assert "items.slice(4, 8)" in src
    assert "<ClientIcon client={client} size={24} />" in src


def test_v263_final_tile_geometry_is_about_42px_with_24px_icons():
    css = read("style.css")
    row = css_block(css, ".workspace-window-grid-row")
    tile = css_block(css, ".workspace-window-grid-row .workspace-window-tile")
    shell = css_block(css, ".workspace-window-tile-icon-shell")
    icon = css_block(css, ".workspace-window-tile .workspace-window-icon")
    assert "min-height: 42px" in row
    assert "min-height: 42px" in tile
    assert "min-width: 32px" in shell and "min-height: 32px" in shell
    assert "min-width: 24px" in icon
    assert "margin: 4px" in icon
