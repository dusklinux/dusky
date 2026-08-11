from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TSX = (ROOT / "components" / "AudioControl.tsx").read_text()
CSS = (ROOT / "style.css").read_text()


def test_audio_removes_visualizer_and_duplicate_actions():
    assert "Visualizer" not in TSX
    assert "runCava" not in TSX
    assert TSX.count('label="Mixer"') == 1


def test_audio_has_output_and_input_reactive_rows():
    assert 'createBinding(audio, "defaultSpeaker")' in TSX
    assert 'createBinding(audio, "defaultMicrophone")' in TSX
    assert 'class="audio-channel-row output-channel"' in TSX
    assert 'class="audio-channel-row input-channel"' in TSX
    assert 'speaker.set_mute(!speaker.mute)' in TSX
    assert 'microphone.set_mute(!microphone.mute)' in TSX
    assert 'microphone.set_volume(value)' in TSX


def test_audio_device_context_is_visible_without_duplicate_footer_actions():
    assert 'class="audio-device-label"' in TSX
    assert 'label="OUTPUT"' in TSX
    assert 'label="INPUT"' in TSX
    assert 'DevicePicker' not in TSX
    assert 'togglePicker' not in TSX


def test_audio_uses_compact_thick_slider_visual_contract():
    assert ".audio-panel" in CSS
    assert "min-width: 276px" in CSS
    assert "scale.audio-thick-slider trough" in CSS
    assert "min-height: 12px" in CSS
    assert "border-radius: 999px" in CSS
    assert "scale.audio-thick-slider slider" in CSS
    assert "min-width: 18px" in CSS
    assert ".audio-device-label" in CSS
    assert ".audio-control-line" in CSS
    assert "background: transparent" in CSS


def test_audio_footer_is_only_mixer():
    assert 'class="audio-mixer-action"' in TSX
    assert "Output device" not in TSX
    assert "Input device" not in TSX
