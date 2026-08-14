from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_optional_feature_keeps_stable_slot_when_toggled():
    optional = read("components/OptionalFeature.tsx")
    slot = '<box class="optional-feature-slot" visible={enabled}>'
    child_gate = "<With value={enabled}>"

    assert slot in optional
    assert child_gate in optional
    assert optional.index(slot) < optional.index(child_gate)
    assert "return enabled ? render() : <box visible={false} />" in optional


def test_media_island_keeps_canonical_position_before_status_controls():
    right = read("components/RightCluster.tsx")
    controls = [
        "render={() => <MediaCard />}",
        "<NetworkControl />",
        "<AudioControl />",
        "<DisplayControl />",
        "<Battery />",
        "<PowerControl />",
    ]

    positions = [right.index(control) for control in controls]
    assert positions == sorted(positions)
