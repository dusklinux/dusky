from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_media_state_derives_only_non_primary_currently_playing_sources():
    state = read('lib/mediaState.ts')
    assert 'additionalPlayingPlayers' in state
    assert 'player !== snapshot.primary' in state
    assert 'playbackOf(player) === "playing"' in state


def test_media_island_only_enables_popup_when_alternative_playing_sources_exist():
    media = read('components/MediaCard.tsx')
    assert '<With value={mediaSnapshot}>' in media
    assert 'additionalPlayingPlayers(snapshot)' in media
    assert 'const popupAvailable = alternatives.length > 0' in media
    assert 'popupAvailable && hoverPanel("media")' in media
    assert 'popupAvailable && leaveTrigger("media")' in media
    assert 'if (popupAvailable) togglePin("media")' in media


def test_media_popup_does_not_duplicate_primary_or_show_paused_sources():
    panel = read('components/MediaPanel.tsx')
    assert 'additionalPlayingPlayers(snapshot)' in panel
    assert 'label="OTHER MEDIA"' in panel
    assert 'NOW PLAYING' not in panel
    assert 'MediaPrimary' not in panel
    assert 'players.slice(1)' not in panel
    assert 'paused' not in panel.lower()


def test_media_popup_auto_closes_and_unpins_when_no_alternatives_remain():
    panel = read('components/MediaPanel.tsx')
    popup = read('lib/popupState.ts')
    assert 'closePanel("media")' in panel
    assert 'export function closePanel(id: PanelId)' in popup
    assert 'pinnedPanel.get() === id' in popup
    assert 'activePanel.get() === id' in popup


def test_popup_is_compact_source_switcher_not_rich_now_playing_dashboard():
    css = read('style.css')
    panel = read('components/MediaPanel.tsx')
    assert 'media-primary-card' not in panel
    assert 'media-primary-progress' not in panel
    assert '.media-source-row' in css
    assert '.media-popup-panel' in css


def test_targeted_media_close_does_not_touch_other_panel_timers_when_media_is_inactive():
    popup = read('lib/popupState.ts')
    function = popup.split('export function closePanel(id: PanelId)', 1)[1].split('export function closePanels()', 1)[0]
    assert 'if (pinnedPanel.get() !== id && activePanel.get() !== id) return' in function
    assert function.index('if (pinnedPanel.get() !== id && activePanel.get() !== id) return') < function.index('cancelClose()')
