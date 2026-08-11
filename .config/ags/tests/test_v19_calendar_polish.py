from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def test_calendar_uses_minimal_date_header_and_related_actions_only():
    tsx = read("components/ClockCard.tsx")
    assert 'calendar-date-label' in tsx
    assert 'format("%A, %B %d, %Y")' in tsx
    assert 'label="Today"' in tsx
    assert 'label="Clocks"' in tsx
    assert 'openClocks()' in tsx
    assert 'openTerminalCalendar' not in tsx
    assert 'Peaclock' not in tsx
    assert 'Terminal' not in tsx


def test_today_action_resets_native_gtk_calendar_to_current_date():
    tsx = read("components/ClockCard.tsx")
    assert 'let calendar: Gtk.Calendar' in tsx
    assert '$={(self) => calendar = self}' in tsx
    assert 'calendar.select_day(GLib.DateTime.new_now_local())' in tsx


def test_calendar_keeps_native_month_navigation_but_is_compact_and_dark():
    tsx = read("components/ClockCard.tsx")
    css = read("style.css")
    assert '<Gtk.Calendar' in tsx
    assert 'showHeading' in tsx or 'show_heading' in tsx
    assert 'showDayNames' in tsx or 'show_day_names' in tsx
    panel = css_block(css, ".calendar-panel")
    assert 'min-width: 250px' in panel
    assert 'max-width:' not in panel
    assert 'padding: 12px' in panel
    popup = css_block(css, ".popup-calendar")
    assert '0.98' in popup or '0.97' in popup
    assert '.calendar-widget header' in css
    assert '.calendar-widget .today' in css
    assert '.calendar-widget .other-month' in css


def test_calendar_footer_is_minimal_two_action_layout():
    css = read("style.css")
    assert '.calendar-footer' in css
    action = css_block(css, ".calendar-footer-action")
    assert 'border-radius' in action
    assert 'min-height' in action


def test_workspace_preview_header_uses_centered_non_font_grid_visual():
    tsx = read("components/WorkspacePreview.tsx")
    css = read("style.css")
    assert 'label="󰕰"' not in tsx
    assert 'workspace-preview-grid-glyph' in tsx
    assert tsx.count('class="workspace-preview-grid-cell"') == 4
    glyph = css_block(css, ".workspace-preview-grid-glyph")
    assert 'margin: 0' in glyph
