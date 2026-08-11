from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MEDIA = (ROOT / 'components' / 'MediaCard.tsx').read_text()
POPUP = (ROOT / 'components' / 'PopupWindows.tsx').read_text()
STATE = (ROOT / 'lib' / 'popupState.ts').read_text()
CSS = (ROOT / 'style.css').read_text()


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text() if p.exists() else ''


def test_media_priority_state_tracks_newest_playing_transition_and_fallback():
    state = read('lib/mediaState.ts')
    assert 'lastPlaybackState' in state
    assert 'lastPlayedRank' in state
    assert 'playback === "playing" && previous !== "playing"' in state
    assert 'selectPrimaryPlayer' in state
    assert 'playing.sort' in state or 'sortByRecency(playing)' in state
    assert 'paused' in state
    assert 'stopped' in state


def test_bar_renders_exactly_one_primary_media_island_not_for_each_player():
    assert '<For each={players}>' not in MEDIA
    assert '<With value={mediaSnapshot}>' in MEDIA
    assert 'snapshot.primary' in MEDIA
    assert MEDIA.count('media-card ${popupAvailable') == 1
    assert 'hoverPanel("media")' in MEDIA
    assert 'togglePin("media")' in MEDIA


def test_media_island_is_content_driven_and_capped_not_fixed_wide():
    assert 'width={30}' in MEDIA or 'width={28}' in MEDIA
    assert 'min-width: 168px' not in CSS
    copy = re.search(r'\.media-copy\s*\{(.*?)\}', CSS, re.S)
    assert copy
    assert 'min-width: 58px' in copy.group(1) or 'min-width: 56px' in copy.group(1)
    assert 'maxWidthChars' in MEDIA


def test_media_popup_is_registered_in_shared_layer_shell_controller():
    panel = read('components/MediaPanel.tsx')
    assert '| "media"' in STATE
    assert 'id="media"' in POPUP
    assert '<MediaPanel' in POPUP
    assert 'NOW PLAYING' not in panel
    assert 'OTHER MEDIA' in panel
    assert 'additionalPlayingPlayers(snapshot)' in panel


def test_popup_secondary_rows_have_source_specific_controls_without_primary_duplication():
    panel = read('components/MediaPanel.tsx')
    assert 'MediaPrimary' not in panel
    assert 'MediaSourceRow' in panel
    assert 'player.previous()' in panel
    assert 'player.play_pause()' in panel
    assert 'player.next()' in panel
    assert 'media-primary-progress' not in panel
    assert 'media-source-row' in panel


def test_media_popup_and_island_have_dark_and_frosted_mist_rice():
    for selector in [
        '.media-card',
        '.media-main-hit',
        '.media-popup-panel',
        '.media-source-row',
        '.media-source-row:hover',
        '.theme-light .media-card',
        '.theme-light .media-popup-panel',
        '.theme-light .media-source-row',
    ]:
        assert selector in CSS
