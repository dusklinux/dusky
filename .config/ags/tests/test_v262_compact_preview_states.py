from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_navigator_has_empty_single_and_multi_state_classes():
    src = read("components/WorkspacePreview.tsx")
    assert "workspace-navigator empty-state" in src
    assert "workspace-navigator single-state" in src
    assert "workspace-navigator multi-state" in src
    assert "workspace-preview-content single-window" in src
    assert "workspace-preview-content multi-window" in src


def test_empty_workspace_preview_ui_is_suppressed_entirely():
    src = read("components/WorkspacePreview.tsx")
    workspaces = read("components/Workspaces.tsx")
    assert 'class="workspace-empty compact"' not in src
    assert 'label="No preview available"' not in src
    assert "const windowCount = openWorkspacePreview(id)" in workspaces
    assert "if (windowCount === 0)" in workspaces
    assert 'closePanel("workspace")' in workspaces


def test_single_window_state_is_smaller_and_hides_windows_grid():
    src = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert "items.length > 1" in src
    assert "items.length === 1 ? 276 : 320" in src
    assert "items.length === 1 ? 138 : 160" in src
    single_nav = css_block(css, ".workspace-navigator.single-state")
    single_hero = css_block(css, ".workspace-preview-content.single-window .workspace-preview-hero-viewport")
    assert "min-width: 292px" in single_nav
    assert "min-width: 276px" in single_hero
    assert "min-height: 138px" in single_hero


def test_multi_window_rows_fill_width_evenly_without_native_tooltip():
    src = read("components/WorkspacePreview.tsx")
    assert "tooltipText=" not in src
    assert src.count('class="workspace-window-grid-row"') == 2
    assert src.count("homogeneous={true}") >= 2
    assert src.count("hexpand={true}") >= 3  # picture plus both rows
    assert "halign={Gtk.Align.FILL}" in src


def test_v262_css_preserves_readable_icons_while_filling_cells():
    css = read("style.css")
    tile = css_block(css, ".workspace-window-grid-row .workspace-window-tile")
    row = css_block(css, ".workspace-window-grid-row")
    icon = css_block(css, ".workspace-window-tile .workspace-window-icon")
    assert "min-width: 0" in tile
    assert "min-height: 42px" in tile
    assert "min-width: 24px" in icon
    assert "min-height: 42px" in row
