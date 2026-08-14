from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def read(rel):
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_workspace_preview_is_compact_320px_hero():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert 'items.length === 1 ? 276 : 320' in tsx
    assert 'items.length === 1 ? 138 : 160' in tsx
    assert 'min-width: 340px' in css_block(css, ".workspace-navigator")
    assert 'min-width: 320px' in css_block(css, ".workspace-preview-hero")
    assert 'min-height: 160px' in css_block(css, ".workspace-preview-hero-viewport")
    tile = css_block(css, ".workspace-window-tile")
    assert 'min-height: 52px' in tile


def test_workspace_popup_is_readably_opaque():
    css = read("style.css")
    block = css_block(css, ".popup-workspace")
    assert 'alpha(@surface_container_high, 0.94)' in block
    assert 'alpha(@surface, 0.90)' in block
    tile = css_block(css, ".workspace-window-tile")
    assert 'alpha(@surface_container_high, 0.42)' in tile


def test_workspace_hover_preview_is_independent_from_persistent_active_indicator():
    tsx = read("components/Workspaces.tsx")
    css = read("style.css")
    assert 'createComputed' in tsx
    assert 'workspaceInteractionId' in tsx
    assert 'claimWorkspaceInteraction(id())' in tsx
    assert 'openWorkspacePreview(id())' in tsx
    assert 'hoverPanel("workspace")' in tsx
    assert 'workspace-pacman' in tsx
    assert 'workspace-active-dot' not in tsx
    assert '.workspace-button.active' in css
    assert '.workspace-button.hovered' in css


def test_workspace_grid_uses_tooltips_instead_of_persistent_helper_copy():
    tsx = read("components/WorkspacePreview.tsx")
    assert 'Hover · Click to focus' not in tsx
    assert 'Hover to preview · Click to focus' not in tsx
    assert 'workspace-preview-identity-title' in tsx
    assert 'tooltipText=' not in tsx
