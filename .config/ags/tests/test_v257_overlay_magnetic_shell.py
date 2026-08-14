from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_workspace_button_uses_overlay_with_non_sizing_magnetic_shell():
    src = read("components/Workspaces.tsx")
    assert "<overlay" in src
    assert 'class={snapShellClass}' in src
    assert '$type="overlay"' in src
    assert 'canTarget={false}' in src
    assert 'workspace-magnetic-shell' in src
    assert 'workspace-snap-surface' not in src

    shell_tag = re.search(
        r'<box\s+\$type="overlay"[\s\S]*?class=\{snapShellClass\}[\s\S]*?/>',
        src,
    )
    assert shell_tag, "expected a self-contained overlay magnetic shell"
    assert 'hexpand={true}' not in shell_tag.group(0)
    assert 'hexpand' not in shell_tag.group(0)


def test_main_workspace_content_remains_the_sizing_child():
    src = read("components/Workspaces.tsx")
    overlay = re.search(r'<overlay[^>]*>([\s\S]*?)</overlay>', src)
    assert overlay
    body = overlay.group(1)
    content_pos = body.find('class="workspace-button-content"')
    shell_pos = body.find('$type="overlay"')
    assert content_pos >= 0 and shell_pos >= 0
    assert content_pos < shell_pos, "main content must be the Gtk.Overlay sizing child"


def test_workspace_resting_geometry_stays_compact_and_stable():
    css = read("style.css")
    button = re.findall(r'(?m)^\.workspace-button\s*\{([^}]*)\}', css, re.S)
    expanded = re.findall(r'(?m)^\.workspace-button\.expanded\s*\{([^}]*)\}', css, re.S)
    assert button and expanded
    assert 'min-width: 22px' in button[-1]
    assert 'padding: 0 2px' in button[-1]
    assert 'min-width' not in button[-1].split('transition:', 1)[1]
    assert 'padding: 0 2px' in expanded[-1]
    assert 'animation:' not in expanded[-1]


def test_magnetic_shell_owns_delayed_horizontal_pulse_only():
    css = read("style.css")
    assert '.workspace-magnetic-shell' in css
    assert '.workspace-magnetic-shell.snapping' in css
    assert 'transform: scaleX(1.20)' in css
    assert 'transform: scaleX(0.96)' in css
    snap = re.search(r'\.workspace-magnetic-shell\.snapping\s*\{([^}]*)\}', css, re.S)
    assert snap
    assert 'animation:' in snap.group(1)
    keyframes_start = css.find('@keyframes workspace-overlay-magnetic-snap')
    assert keyframes_start >= 0
    keyframes = css[keyframes_start:keyframes_start + 1300]
    assert 'translateY' not in keyframes
    assert 'scaleY' not in keyframes


def test_no_expanding_snap_surface_regression():
    src = read("components/Workspaces.tsx")
    assert 'class={snapSurfaceClass}' not in src
    assert 'hexpand={true}' not in src
