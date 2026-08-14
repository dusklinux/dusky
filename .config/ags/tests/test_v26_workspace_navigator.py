from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_snapshot_keeps_all_clients_and_pages_eight_at_a_time():
    state = read("lib/workspacePreviewState.ts")
    assert "MAX_CLIENTS" not in state
    assert ".slice(0, MAX_CLIENTS)" not in state
    assert "export const PREVIEW_PAGE_SIZE = 8" in state
    assert "previewPage" in state
    assert "previewPageCount" in state
    assert "previewPageClients" in state
    assert "items.slice(start, start + PREVIEW_PAGE_SIZE)" in state
    assert "setPreviewPage(0)" in state
    assert "previousPreviewPage" in state
    assert "nextPreviewPage" in state


def test_active_workspace_hover_suppresses_preview_immediately():
    src = read("components/Workspaces.tsx")
    assert 'closePanel("workspace")' in src
    assert "closeWorkspacePreview()" in src
    assert "if (active() === id())" in src
    active_branch = re.search(r"if \(active\(\) === id\(\)\) \{([\s\S]*?)\n\s*\}", src)
    assert active_branch
    body = active_branch.group(1)
    assert "closeWorkspacePreview()" in body
    assert 'closePanel("workspace")' in body
    assert "openWorkspacePreview(id())" not in body
    assert 'hoverPanel("workspace")' not in body


def test_non_active_workspace_still_opens_preview_and_claims_ownership():
    src = read("components/Workspaces.tsx")
    assert "claimWorkspaceInteraction(id())" in src
    assert "openWorkspacePreview(id())" in src
    assert 'hoverPanel("workspace")' in src


def test_window_grid_is_two_rows_of_four_from_current_page():
    src = read("components/WorkspacePreview.tsx")
    assert "previewPageClients" in src
    assert src.count('class="workspace-window-grid-row"') == 2
    assert "items.slice(0, 4)" in src
    assert "items.slice(4, 8)" in src
    assert "previewClients((items) => items.slice(0, 4))" not in src
    assert "previewClients((items) => items.slice(4, 8))" not in src


def test_pager_only_appears_for_multiple_pages_and_has_bounded_controls():
    src = read("components/WorkspacePreview.tsx")
    assert 'class="workspace-window-pager"' in src
    assert 'class="workspace-window-page-indicator"' in src
    assert "previousPreviewPage" in src
    assert "nextPreviewPage" in src
    assert "previewPageCount((count) => count > 1)" in src
    assert "previewPage((page) => page > 0)" in src
    assert "previewPageCount" in src


def test_selected_window_identity_is_prominent_below_hero():
    src = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert 'class="workspace-preview-identity"' in src
    assert 'class="workspace-preview-identity-app"' in src
    assert 'class="workspace-preview-identity-title"' in src
    assert "selectedClient" in src
    app = css_block(css, ".workspace-preview-identity-app")
    title = css_block(css, ".workspace-preview-identity-title")
    assert "font-size: 12px" in app
    assert "font-weight:" in app
    assert "font-size: 10px" in title


def test_hero_picture_is_allocated_to_full_viewport_with_contain_fit():
    src = read("components/WorkspacePreview.tsx")
    assert "contentFit={Gtk.ContentFit.CONTAIN}" in src
    assert "items.length === 1 ? 276 : 320" in src
    assert "items.length === 1 ? 138 : 160" in src
    assert "hexpand={true}" in src
    assert "vexpand={true}" in src
    assert "halign={Gtk.Align.FILL}" in src
    assert "valign={Gtk.Align.FILL}" in src


def test_navigator_top_spacing_is_tighter_than_v257():
    src = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert 'workspace-navigator empty-state' in src
    assert 'workspace-navigator single-state' in src
    assert 'workspace-navigator multi-state' in src
    assert 'spacing={6}' in src
    nav = css_block(css, ".workspace-navigator")
    header = css_block(css, ".workspace-preview-header")
    assert "padding: 7px" in nav
    assert "min-height: 30px" in header
