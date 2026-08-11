from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POWER = (ROOT / "components" / "PowerControl.tsx").read_text()
DUSKY = (ROOT / "lib" / "dusky.ts").read_text()
CSS = (ROOT / "style.css").read_text()


def test_profile_status_is_not_duplicated_and_uses_one_compact_selector():
    assert "power-profile-current" not in POWER
    assert "power-profile-option" not in POWER
    assert 'class="power-profile-segments"' in POWER
    assert 'power-profile-segment' in POWER
    assert "set_active_profile" in POWER


def test_caffeine_uses_native_gtk_switch():
    assert "<Gtk.Switch" in POWER
    assert 'class="power-caffeine-switch"' in POWER
    assert "toggleCaffeine" in POWER
    assert "caffeineState" in POWER


def test_session_actions_are_a_two_by_three_power_deck():
    assert 'class="power-command-deck"' in POWER
    assert POWER.count('class="power-command-row"') == 2
    for label in ["Lock", "Sleep", "Logout", "Soft reboot", "Reboot", "Power off"]:
        assert f'label="{label}"' in POWER
    assert "power-session-row" not in POWER


def test_dusky_session_backend_maps_graceful_actions():
    assert 'dusky_session.sh" logout' in DUSKY
    assert 'dusky_session.sh" soft-reboot' in DUSKY
    assert 'dusky_session.sh" reboot' in DUSKY
    assert 'dusky_session.sh" poweroff' in DUSKY
    assert "export function softRebootSession" in DUSKY


def test_soft_reboot_reboot_and_poweroff_use_inline_confirmation():
    assert 'type ConfirmAction = "soft-reboot" | "restart" | "shutdown" | null' in POWER
    assert 'setConfirmAction("soft-reboot")' in POWER
    assert 'setConfirmAction("restart")' in POWER
    assert 'setConfirmAction("shutdown")' in POWER
    assert "softRebootSession()" in POWER


def test_power_deck_has_richer_rice_styles_in_both_themes():
    for selector in [
        ".power-profile-segments",
        ".power-profile-segment",
        ".power-caffeine-switch",
        ".power-command-deck",
        ".power-command-tile",
        ".power-command-tile:hover",
        ".power-command-tile:active",
        ".theme-light .power-command-tile",
        ".theme-light .power-profile-segments",
    ]:
        assert selector in CSS
