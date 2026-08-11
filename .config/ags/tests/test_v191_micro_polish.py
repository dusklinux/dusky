from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_calendar_footer_keeps_compact_actions_with_invisible_icon_shells():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")

    assert tsx.count('class="calendar-footer-icon-shell"') == 2
    assert 'class="calendar-footer-icon today-icon"' in tsx
    assert 'class="calendar-footer-icon clocks-icon"' in tsx
    assert 'iconName="x-office-calendar-symbolic"' in tsx
    assert 'iconName="preferences-system-time-symbolic"' in tsx

    action = css_block(css, ".calendar-footer-action")
    assert 'min-height: 28px' in action
    assert 'border-radius: 10px' in action
    assert 'background-image:' in action

    shell = css_block(css, ".calendar-footer-icon-shell")
    assert 'min-width: 18px' in shell
    assert 'min-height: 18px' in shell
    assert 'border: none' in shell
    assert 'background: transparent' in shell


def test_workspace_header_grid_is_css_drawn_and_has_no_symbolic_icon_bearing():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")

    assert 'iconName="view-grid-symbolic"' not in tsx
    assert 'workspace-preview-grid-glyph' in tsx
    assert tsx.count('class="workspace-preview-grid-cell"') == 4
    assert 'workspace-preview-header-icon-glyph' not in tsx

    glyph = css_block(css, ".workspace-preview-grid-glyph")
    assert 'min-width: 16px' in glyph
    assert 'min-height: 16px' in glyph
    assert 'margin: 0' in glyph

    cell = css_block(css, ".workspace-preview-grid-cell")
    assert 'min-width: 6px' in cell
    assert 'min-height: 6px' in cell
    assert 'border-radius: 2px' in cell
