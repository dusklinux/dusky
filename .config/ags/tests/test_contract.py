from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def read(rel):
    return (ROOT / rel).read_text()

def test_app_uses_ags_v3_gtk4_and_monitors():
    s=read('app.tsx')
    assert 'ags/gtk4/app' in s
    assert 'app.start({' in s
    assert 'createBinding(app, "monitors")' in s
    assert '<For each={monitors}>' in s

def test_bar_is_exclusive_top_layer_and_three_zone():
    s=read('components/Bar.tsx')
    assert 'Astal.Exclusivity.EXCLUSIVE' in s
    assert 'TOP | LEFT | RIGHT' in s
    for name in ['LeftCluster', 'ClockCard', 'RightCluster']:
        assert f'<{name}' in s

def test_workspaces_use_dusky_lua_compatible_dispatcher():
    s=read('components/Workspaces.tsx')
    assert 'gi://AstalHyprland' in s
    assert 'focusWorkspace' in s
    assert 'dispatch("workspace"' not in s
    d=read('lib/dusky.ts')
    assert 'multi_monitor_workspace.sh' in d
    assert 'workspaceScript' in d
    assert 'WORKSPACE_VISIBLE_SLOTS = [1, 2, 3, 4, 5] as const' in s
    assert 'function workspaceIdForSlot' in s
    assert 'active() > 5 ? active() : slot' in s
    assert 'Array.from({ length: 10 }' not in s

def test_clock_has_real_gtk_calendar_panel():
    s=read('components/ClockCard.tsx')
    assert '<Gtk.Calendar' in s
    assert '<PanelTrigger' in s
    assert 'panel="calendar"' in s
    assert 'createPoll' in s

def test_popup_controller_opens_on_pointer_enter_without_gtk_popover():
    trigger=read('components/PanelTrigger.tsx')
    popup=read('components/PopupWindow.tsx')
    assert 'Gtk.EventControllerMotion' in trigger
    assert 'hoverPanel(panel)' in trigger
    assert '<popover' not in popup
    assert 'Astal.Layer.OVERLAY' in popup

def test_media_card_uses_mpris_and_fixed_transport_controls():
    s=read('components/MediaCard.tsx')
    assert 'gi://AstalMpris' in s
    assert 'coverArt' in s
    assert 'player.previous()' in s
    assert 'player.play_pause()' in s
    assert 'player.next()' in s
    assert 'title' in s and 'artist' in s

def test_network_panel_uses_native_astal_network_and_bluetooth():
    s=read('components/NetworkControl.tsx')
    assert 'gi://AstalNetwork' in s
    assert 'gi://AstalBluetooth' in s
    assert 'ssid' in s
    assert 'strength' in s

def test_audio_panel_uses_wireplumber_slider():
    s=read('components/AudioControl.tsx')
    assert 'gi://AstalWp' in s
    assert '<slider' in s
    assert 'set_volume' in s

def test_display_panel_uses_installed_brightnessctl_without_optional_astal_brightness():
    s=read('components/DisplayControl.tsx')
    assert 'gi://AstalBrightness' not in s
    assert 'brightnessctl' in s
    assert 'createPoll' in s
    assert 'execAsync' in s
    assert '<slider' in s
    assert 'runWallpaper' in s
    assert 'runTheme' in read('lib/themeState.ts')

def test_app_is_directly_runnable_with_ags_shebang():
    s=read('app.tsx')
    assert s.startswith('#!/usr/bin/env -S ags run\n')
    install=read('install.sh')
    assert 'chmod +x "$DEST/app.tsx"' in install

def test_power_panel_exposes_safe_session_actions():
    s=read('components/PowerControl.tsx')
    for label in ['Lock', 'Sleep', 'Logout', 'Soft reboot', 'Reboot', 'Power off']:
        assert f'>{label}<' in s or f'label="{label}"' in s
    assert 'Caffeine' in s
    assert 'power-confirm' in s

def test_matugen_palette_is_loaded_before_shell_css():
    s=read('app.tsx')
    assert 'waybar-colors.css' in s
    assert 'fallback.css' in s
    assert 'style.css' in s

def test_css_matches_concept_visual_language():
    s=read('style.css')
    for selector in ['.bar-shell', '.workspace-deck', '.clock-card', '.media-card', '.popup-window-frame', '.control-leader']:
        assert selector in s
    assert 'linear-gradient' in s
    assert 'border-radius' in s

def test_waybar_is_not_killed_or_modified():
    all_text='\n'.join(p.read_text(errors='ignore') for p in ROOT.rglob('*') if p.is_file() and 'tests' not in p.parts and '.pytest_cache' not in p.parts)
    assert 'pkill waybar' not in all_text
    assert '~/.config/waybar/config.jsonc' not in all_text

def test_install_script_targets_home_config_ags():
    s=read('install.sh')
    assert '$HOME/.config/ags' in s
    assert 'ags run "$HOME/.config/ags/app.tsx"' in s


def test_media_sources_are_managed_by_the_adaptive_media_state_layer():
    card=read('components/MediaCard.tsx')
    state=read('lib/mediaState.ts')
    assert '<For each={players}>' not in card
    assert '<With value={mediaSnapshot}>' in card
    assert 'snapshot.primary' in card
    assert 'createBinding(mpris, "players")' in state
    assert 'playerIsAvailable' in state

def test_audio_mixer_has_dependency_safe_dusky_fallback():
    s=read('lib/dusky.ts')
    assert 'command -v pavucontrol' in s
    assert 'command -v pwvucontrol' in s
    assert 'dusky_quickpanal.py' in s

def test_bar_shell_is_visually_transparent_not_full_width_card():
    s=read('style.css')
    block=re.search(r'\.bar-shell\s*\{(.*?)\}', s, re.S)
    assert block, 'missing .bar-shell block'
    body=block.group(1)
    assert 'background: transparent' in body
    assert 'border: none' in body
    assert 'box-shadow: none' in body


def test_media_uses_single_primary_source_instead_of_duplicate_player_cards():
    card=read('components/MediaCard.tsx')
    state=read('lib/mediaState.ts')
    assert '<With value={mediaSnapshot}>' in card
    assert 'snapshot.primary' in card
    assert '<For each={players}>' not in card
    assert 'selectPrimaryPlayer' in state
    assert 'lastPlayedRank' in state
