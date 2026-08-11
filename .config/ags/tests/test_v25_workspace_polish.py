from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_strip_uses_restrained_capsule_motion_not_primary_bloom():
    css = read("style.css")
    assert "v2.5 — workspace strip + navigator polish" in css

    deck = css_block(css, ".workspace-deck")
    assert "border: 1px solid alpha(@outline_variant, 0.18)" in deck
    assert "0 0 10px alpha(@primary" not in deck

    button = css_block(css, ".workspace-button")
    assert "transition: 230ms cubic-bezier(0.34, 1.56, 0.64, 1)" in button
    assert "font-size: 10px" in button

    hover = css_block(css, ".workspace-button.hovered")
    assert "transform: none" in hover
    assert "translateY(" not in hover
    assert "background-image:" in hover
    assert "0 0 10px alpha(@secondary, 0.10)" in hover

    active = css_block(css, ".workspace-button.active")
    assert "transform: none" in active
    assert "border-color: alpha(@primary, 0.58)" in active
    assert "0 0 0 1px alpha(@primary, 0.14)" in active
    assert "alpha(@tertiary" not in active


def test_preview_owned_workspace_remains_quiet_and_non_glowy():
    css = read("style.css")
    previewing = css_block(css, ".workspace-button.previewing")
    both = css_block(css, ".workspace-button.active.previewing")
    for block in (previewing, both):
        assert "0 0 14px" not in block
        assert "0 0 17px" not in block
        assert "alpha(@tertiary" not in block
    assert "background: transparent" in previewing
    assert "background: transparent" in both
    assert "box-shadow: none" in previewing
    assert "box-shadow: none" in both


def test_workspace_header_is_single_line_compact_identity():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")

    assert 'class="workspace-preview-header-copy" spacing={8}' in tsx
    assert 'class="workspace-preview-header-copy" orientation={Gtk.Orientation.VERTICAL}' not in tsx
    assert '"1 window"' in tsx
    assert '`${items.length} windows`' in tsx

    header = css_block(css, ".workspace-preview-header")
    assert "min-height: 30px" in header

    title = css_block(css, ".workspace-preview-header-title")
    assert "font-size: 12px" in title

    subtitle = css_block(css, ".workspace-preview-header-subtitle")
    assert "font-size: 9px" in subtitle
    assert "border-radius: 999px" in subtitle

    close = css_block(css, ".workspace-preview-close")
    assert "min-width: 28px" in close
    assert "min-height: 28px" in close


def test_workspace_preview_and_selected_row_have_restrained_depth():
    css = read("style.css")
    popup = css_block(css, ".popup-workspace")
    hero = css_block(css, ".workspace-preview-hero")
    selected = css_block(css, ".workspace-window-row.selected")

    assert "alpha(@primary_container" not in popup
    assert "0 12px 28px alpha(#000000, 0.28)" in popup
    assert "0 5px 14px alpha(#000000, 0.17)" in hero
    assert "0 0 0 1px alpha(@primary" not in hero
    assert "0 3px 8px alpha(#000000, 0.10)" in selected
    assert "0 5px 14px alpha(@primary" not in selected


def test_workspace_window_list_typography_is_more_readable():
    css = read("style.css")
    name = css_block(css, ".workspace-window-name")
    title = css_block(css, ".workspace-window-title")
    heading = css_block(css, ".workspace-window-list-title")
    help_text = css_block(css, ".workspace-window-list-help")
    focus = css_block(css, ".workspace-window-focus-copy")

    assert "font-size: 11px" in name
    assert "font-size: 9px" in title
    assert "font-size: 10px" in heading
    assert "font-size: 9px" in help_text
    assert "font-size: 8px" in focus


def test_workspace_functionality_and_preview_dimensions_are_preserved():
    workspaces = read("components/Workspaces.tsx")
    preview = read("components/WorkspacePreview.tsx")
    assert "void focusWorkspace(id)" in workspaces
    assert "openWorkspacePreview(id)" in workspaces
    assert "hoverPanel(\"workspace\")" in workspaces
    assert "selectPreviewClient(client.address)" in preview
    assert "activatePreviewClient(previewWorkspaceLocalId.get(), client.address)" in preview
    assert "items.length === 1 ? 276 : 320" in preview
    assert "items.length === 1 ? 138 : 160" in preview


def test_frosted_mist_workspace_states_get_matching_restrained_overrides():
    css = read("style.css")
    assert ".theme-light .workspace-button.active" in css
    assert ".theme-light .workspace-button.previewing" in css
    assert ".theme-light .popup-workspace" in css
    light_popup = css_block(css, ".theme-light .popup-workspace")
    assert "alpha(#314653, 0.12)" in light_popup


def test_open_windows_are_icon_only_grid_tiles_with_hover_descriptions():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")

    assert "function WindowTile" in tsx
    assert "function WindowRow" not in tsx
    assert 'class="workspace-window-grid"' in tsx
    assert tsx.count('class="workspace-window-grid-row"') == 2
    assert 'items.slice(0, 4)' in tsx
    assert 'items.slice(4, 8)' in tsx
    assert 'class="workspace-window-tile' in tsx
    assert 'workspace-preview-identity-title' in tsx
    assert 'tooltipText=' not in tsx
    assert 'selectPreviewClient(client.address)' in tsx
    assert 'activatePreviewClient(previewWorkspaceLocalId.get(), client.address)' in tsx
    assert 'class="workspace-window-copy"' not in tsx
    assert 'class="workspace-window-name"' not in tsx
    assert 'class="workspace-window-title"' not in tsx
    tile = css_block(css, ".workspace-window-tile")
    assert "min-width: 56px" in tile
    assert "min-height: 52px" in tile
    assert "transition: 150ms ease" in tile


def test_window_grid_replaces_vertical_list_to_reserve_space():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert 'label="WINDOWS"' in tsx
    assert 'label="OPEN WINDOWS"' not in tsx
    assert 'Hover · Click to focus' not in tsx
    grid = css_block(css, ".workspace-window-grid")
    assert "padding: 0" in grid
    row = css_block(css, ".workspace-window-grid-row")
    assert "min-height: 42px" in row
