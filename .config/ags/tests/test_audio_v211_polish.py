from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSX = (ROOT / "components" / "AudioControl.tsx").read_text()
CSS = (ROOT / "style.css").read_text()


def test_audio_uses_explicit_output_and_input_sections():
    assert 'label="OUTPUT"' in TSX
    assert 'label="INPUT"' in TSX
    assert TSX.count('class="audio-section-kicker"') == 2
    assert TSX.count('class="audio-control-line"') == 2


def test_device_names_are_context_not_picker_triggers():
    assert 'class="audio-device-label"' in TSX
    assert 'DevicePicker' not in TSX
    assert 'togglePicker' not in TSX
    assert 'setPicker' not in TSX
    assert 'endpoint.set_is_default' not in TSX
    assert 'audio-device-picker' not in TSX


def test_mute_icons_are_aligned_interactive_controls():
    assert 'speaker.set_mute(!speaker.mute)' in TSX
    assert 'microphone.set_mute(!microphone.mute)' in TSX
    assert 'class={createBinding(speaker, "mute")' in TSX
    assert 'class={createBinding(microphone, "mute")' in TSX
    assert '.audio-channel-icon:hover' in CSS
    assert '.audio-channel-icon:active' in CSS
    assert '.audio-channel-icon:focus-visible' in CSS
    assert '.audio-channel-icon.muted' in CSS


def test_slider_remains_thick_rounded_and_percent_aligned():
    assert 'scale.audio-thick-slider trough' in CSS
    assert 'min-height: 12px' in CSS
    assert 'border-radius: 999px' in CSS
    assert '.audio-control-line' in CSS
    assert '.audio-channel-percent' in CSS
    assert 'valign={Gtk.Align.CENTER}' in TSX


def test_audio_footer_remains_mixer_only():
    assert TSX.count('label="Mixer"') == 1
    assert 'Visualizer' not in TSX
    assert 'Output device' not in TSX
    assert 'Input device' not in TSX
