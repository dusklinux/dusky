from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_pages_eight_windows_at_a_time():
    state = read("lib/workspacePreviewState.ts")
    src = read("components/WorkspacePreview.tsx")
    assert "export const PREVIEW_PAGE_SIZE = 8" in state
    assert "items.slice(0, 4)" in src
    assert "items.slice(4, 8)" in src
    assert "items.slice(0, 6)" not in src
    assert "items.slice(6, 12)" not in src


def test_compact_hero_is_320_by_160_and_still_contains_full_capture():
    src = read("components/WorkspacePreview.tsx")
    assert "items.length === 1 ? 276 : 320" in src
    assert "items.length === 1 ? 138 : 160" in src
    assert "contentFit={Gtk.ContentFit.CONTAIN}" in src
    assert "halign={Gtk.Align.FILL}" in src
    assert "valign={Gtk.Align.FILL}" in src


def test_navigator_is_narrower_and_compact():
    css = read("style.css")
    nav = css_block(css, ".workspace-navigator")
    content = css_block(css, ".workspace-preview-content")
    hero = css_block(css, ".workspace-preview-hero-viewport")
    assert "min-width: 340px" in nav
    assert "min-width: 340px" in content
    assert "min-width: 320px" in hero
    assert "min-height: 160px" in hero
    assert "min-width: 390px" not in nav
    assert "min-width: 390px" not in content


def test_grid_stays_two_rows_but_only_four_columns_per_row():
    src = read("components/WorkspacePreview.tsx")
    assert src.count('class="workspace-window-grid-row"') == 2
    assert "items.length > 4" in src
    assert "items.length > 6" not in src
