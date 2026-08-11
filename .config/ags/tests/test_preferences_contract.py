from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_motion_state_contract_and_defaults():
    path = ROOT / "lib/motionState.ts"
    assert path.exists(), "motionState.ts must exist"
    text = path.read_text()
    assert "AdaptiveMotionStyle" in text
    assert 'DEFAULT_MOTION_STYLE: AdaptiveMotionStyle = "soft-magnetic"' in text
    assert "adaptive-glass-motion" in text
    assert "motionStyle" in text
    assert "motionClass" in text
    assert "getWorkspaceMotionTiming" in text
    assert '"soft-magnetic"' in text
    assert '"precise-futuristic"' in text
    assert "monitor_directory" in text
    assert "let motionMonitor: Gio.FileMonitor | null = null" in text
    assert "motionMonitor = dir.monitor_directory" in text


def test_feature_state_contract_and_defaults():
    path = ROOT / "lib/featureState.ts"
    assert path.exists(), "featureState.ts must exist"
    text = path.read_text()
    for token in [
        '"workspace-preview"',
        '"media-island"',
        '"weather"',
        '"notifications"',
    ]:
        assert token in text
    assert "DEFAULT_FEATURE_ENABLED = true" in text
    assert "workspacePreviewEnabled" in text
    assert "mediaIslandEnabled" in text
    assert "weatherEnabled" in text
    assert "notificationsEnabled" in text
    assert "monitor_directory" in text
    assert "let featureMonitor: Gio.FileMonitor | null = null" in text
    assert "featureMonitor = dir.monitor_directory" in text


def test_bar_and_popups_receive_motion_classes():
    bar = read("components/Bar.tsx")
    popup = read("components/PopupWindow.tsx")
    assert "motionClass" in bar
    assert "motionClass" in popup
    assert "createComputed" in bar
    assert "createComputed" in popup
    assert "motion-soft-magnetic" not in bar
    assert "motion-precise-futuristic" not in popup


def test_workspace_preview_can_be_disabled():
    popup = read("components/PopupWindow.tsx")
    popups = read("components/PopupWindows.tsx")
    workspaces = read("components/Workspaces.tsx")
    state = read("lib/workspacePreviewState.ts")

    assert "enabled?:" in popup
    assert "activePanel() === id" in popup
    assert "workspacePreviewEnabled" in popups
    assert 'id="workspace"' in popups
    assert "enabled={workspacePreviewEnabled}" in popups
    assert "workspacePreviewEnabled" in workspaces
    assert "workspacePreviewEnabled" in state


def test_optional_entrypoints_use_feature_visibility():
    left = read("components/LeftCluster.tsx")
    right = read("components/RightCluster.tsx")
    optional = read("components/OptionalFeature.tsx")
    assert "<With value={enabled}>" in optional
    assert "render: () => JSX.Element" in optional
    assert "return enabled ? render() : <box visible={false} />" in optional
    assert "weatherEnabled" in left
    assert "notificationsEnabled" in left
    assert "mediaIslandEnabled" in right
    assert "OptionalFeature" in left
    assert "OptionalFeature" in right
    assert "render={() => <Weather />}" in left
    assert "render={() => <Notification />}" in left
    assert "render={() => <MediaCard />}" in right
    assert "visible={weatherEnabled}" not in left
    assert "visible={notificationsEnabled}" not in left
    assert "visible={mediaIslandEnabled}" not in right


def test_motion_css_is_scoped_and_non_sizing():
    css = read("style.css")
    assert "Adaptive Glass preferences: motion styles" in css
    assert ".motion-soft-magnetic .workspace-button" in css
    assert ".motion-precise-futuristic .workspace-button" in css
    assert ".motion-soft-magnetic .workspace-magnetic-shell.snapping" in css
    assert ".motion-precise-futuristic .workspace-magnetic-shell.snapping" in css
    assert "560ms" in css
    assert "320ms" in css

    motion_blocks = re.findall(
        r"\.motion-(?:soft-magnetic|precise-futuristic)[^{]*\{([^}]*)\}",
        css,
        re.S,
    )
    assert motion_blocks
    sizing_props = ("min-width", "min-height", "width:", "height:", "padding:", "margin:")
    assert not any(prop in block for block in motion_blocks for prop in sizing_props)


def test_control_center_exposes_adaptive_glass_preferences():
    config = (REPO / "user_scripts/dusky_system/control_center/dusky_config.toml").read_text()
    rows = (REPO / "user_scripts/dusky_system/control_center/lib/rows.py").read_text()

    assert 'properties.get("default"' in rows
    assert 'title = "Status Bar"' in config
    assert 'title = "Status Bar (Waybar)"' not in config
    assert 'title = "Adaptive Glass"' in config
    assert 'title = "Adaptive Glass Motion"' in config
    assert 'key = "ags/adaptive-glass-motion"' in config
    assert 'default = "soft-magnetic"' in config
    assert 'options = ["Soft Magnetic", "Precise Futuristic"]' in config
    assert '"soft-magnetic" = "Soft Magnetic"' in config
    assert '"precise-futuristic" = "Precise Futuristic"' in config

    for title, key in [
        ("Workspace Preview", "ags/features/workspace-preview"),
        ("Media Island", "ags/features/media-island"),
        ("Weather", "ags/features/weather"),
        ("Notifications", "ags/features/notifications"),
    ]:
        assert f'title = "{title}"' in config
        assert f'key = "{key}"' in config
        assert re.search(
            rf'title = "{re.escape(title)}"[\s\S]*?key = "{re.escape(key)}"[\s\S]*?default = true',
            config,
        )
