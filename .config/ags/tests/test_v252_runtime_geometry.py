from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def css_block(css: str, selector: str) -> str:
    matches = re.findall(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert matches, f"missing CSS block for {selector}"
    return matches[-1]


def css_block_for_selector(css: str, selector: str) -> str:
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    matches = re.findall(r"(?m)^([^@][^{]+)\{(.*?)\}", without_comments, re.S)
    blocks = [
        block for selector_list, block in matches
        if selector in [part.strip() for part in selector_list.split(',')]
    ]
    assert blocks, f"missing CSS block for {selector}"
    return blocks[-1]


def test_monitor_window_tree_has_one_application_this_boundary():
    app = read('app.tsx')
    popups = read('components/PopupWindows.tsx')
    bar = read('components/Bar.tsx')
    popup_window = read('components/PopupWindow.tsx')

    # For owns one monitor component; that component owns the single app This.
    assert '<PopupWindows gdkmonitor={monitor} />' in app
    assert '<This this={app}>' not in app
    assert popups.count('<This this={app}>') == 1
    assert '<Bar gdkmonitor={gdkmonitor} />' in popups

    # Windows are registered through the one This boundary, not twice via application=.
    assert 'application={app}' not in bar
    assert 'application={app}' not in popup_window


def test_workspace_rail_no_longer_depends_on_gtk_overlay_selection_plate():
    workspaces = read('components/Workspaces.tsx')
    assert 'workspace-selection-plate' not in workspaces
    assert 'selectionPlateClass' not in workspaces
    assert 'class={selectionPlateClass}' not in workspaces
    assert '<box class="workspace-deck" spacing={3}>' in workspaces


def test_final_hover_feedback_does_not_move_geometry():
    css = read('style.css')
    marker = '/* v2.5.2 — stable hover geometry + compact clock */'
    assert marker in css
    tail = css.split(marker, 1)[1]
    assert 'transform: none;' in tail
    for selector in [
        '.launcher-card:hover',
        '.weather-card:hover',
        '.notification-card:hover',
        '.clock-card:hover',
        '.control-leader:hover',
        '.battery-card:hover',
        '.workspace-button:hover:not(.active)',
        '.workspace-window-tile:hover',
    ]:
        assert selector in tail
        assert 'translateY(' not in css_block_for_selector(tail, selector)


def test_clock_is_content_sized_without_trailing_fixed_space():
    css = read('style.css')
    marker = '/* v2.5.2 — stable hover geometry + compact clock */'
    tail = css.split(marker, 1)[1]
    clock = css_block(tail, '.clock-card')
    content = css_block(tail, '.clock-card-content')
    assert 'min-width: 90px;' in clock
    assert 'padding: 4px 9px 1px 9px;' in clock
    assert 'border-radius: 0 0 7px 7px;' in clock
    assert 'spacing' not in content  # spacing is JSX-owned; CSS must not fake width
