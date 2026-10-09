"""Keep every audit test away from the user's history and configuration."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (
        "DUSKY_KEYLOGGER_CONFIG", "DUSKY_KEYLOGGER_DATA_DIR",
        "DUSKY_TRANSCRIPT_DIR", "DUSKY_TRANSCRIPT_FORMAT",
        "WATCHDOG_USEC", "NOTIFY_SOCKET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def store(tmp_path):
    from dusky_keylogger.storage import KeyStore
    store = KeyStore(tmp_path / "data" / "keys.db")
    store.init_db()
    return store
