import asyncio
import errno
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from dusky_keylogger import keycodes as kc
from dusky_keylogger import daemon as dm
from dusky_keylogger import listener as lm
from dusky_keylogger.listener import KeyEventClassifier, KeyPress
from dusky_keylogger.storage import EventWriter, KeyStore, row_from_press


def press(code=kc.KEY_A, ts_us=None):
    return KeyPress(code, kc.key_name(code), kc.char_for(code, False, False),
                    kc.classify_key(code), "Synthetic", ts_us or time.time_ns() // 1000)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("condition did not become true before deadline")


def test_classifier_modifiers_and_repeat():
    c = KeyEventClassifier()
    def feed(code, value=1, device="one"):
        return c.handle(device, device, code, value, 1234567)
    assert feed(kc.KEY_A).char == "a"
    assert feed(kc.KEY_A, 2) is None
    assert feed(kc.KEY_A, 0) is None
    assert feed(kc.KEY_LEFTSHIFT).kind == kc.KIND_MODIFIER
    assert feed(kc.KEY_A).char == "A"
    feed(kc.KEY_RIGHTSHIFT)
    feed(kc.KEY_LEFTSHIFT, 0)
    assert feed(kc.KEY_A).char == "A"
    feed(kc.KEY_RIGHTSHIFT, 0)
    feed(kc.KEY_CAPSLOCK)
    assert feed(kc.KEY_A).char == "A"
    feed(kc.KEY_LEFTSHIFT)
    assert feed(kc.KEY_A).char == "a"
    feed(kc.KEY_LEFTCTRL)
    assert feed(kc.KEY_C) is None
    assert feed(kc.KEY_DELETE) is None
    assert feed(kc.KEY_BACKSPACE).kind == kc.KIND_BACKSPACE
    assert feed(kc.KEY_A, device="two").char == "a"
    assert feed(0x110) is None
    feed(kc.KEY_LEFTCTRL, 0)
    c.sync_from_kernel("one", [], False, False)
    assert feed(kc.KEY_KP1).kind == kc.KIND_NAVIGATION
    assert feed(kc.KEY_KPPLUS).char == "+"
    c.handle_led("one", kc.LED_NUML, 1)
    assert feed(kc.KEY_KP1).char == "1"


@pytest.mark.parametrize("code", range(1, 249))
def test_known_codes_agree_with_evdev(code):
    from evdev import ecodes
    name = kc.KEY_NAMES.get(code)
    if name:
        assert getattr(ecodes, name) == code
    for shift in (False, True):
        for caps in (False, True):
            value = kc.char_for(code, shift, caps)
            assert value is None or isinstance(value, str)


@pytest.mark.parametrize("value", [None, [], {}, 123, "", "   "])
def test_malformed_config_paths_use_defaults(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"data_dir": value, "transcript_dir": value}), encoding="utf-8")
    config = dm.load_config(path)
    assert dm.get_data_dir(config) == tmp_path / ".config/dusky/settings/keylogger/data"
    assert dm.get_transcript_dir(config) == Path("/tmp")


@pytest.mark.parametrize("interval", [None, "bad", "inf", "nan", -10, 100])
def test_flush_interval_validation(tmp_path, interval):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"flush_interval": interval, "persistent_enabled": "false"}), encoding="utf-8")
    cfg = dm.load_config(path)
    assert 0.05 <= cfg["flush_interval"] <= 5
    assert cfg["persistent_enabled"] is True


def test_config_migration_and_precedence(tmp_path, monkeypatch):
    old = tmp_path / ".config/dusky-keylogger/config.json"
    old.parent.mkdir(parents=True)
    old.write_text('{"data_dir":"relative-data","flush_interval":1}', encoding="utf-8")
    assert dm.load_config()["data_dir"] == str(tmp_path / "relative-data")
    new = tmp_path / ".config/dusky/settings/keylogger/config.json"
    assert new.exists() and old.exists()
    monkeypatch.setenv("DUSKY_KEYLOGGER_DATA_DIR", "$HOME/env-data")
    assert dm.default_data_dir() == tmp_path / "env-data"
    assert new.stat().st_mode & 0o777 == 0o600


def test_store_roundtrip_and_boundaries(store):
    now = datetime.now().replace(second=0, microsecond=0)
    lo = now - timedelta(minutes=1)
    rows = [row_from_press(press(ts_us=int(t.timestamp()*1_000_000)+123))
            for t in (lo, now, now+timedelta(minutes=1))]
    assert store.insert_many(rows) == 3
    assert store.total() == 3
    assert store.count_between(lo, now) == 1
    assert store.count_ranges([(lo, now), (now, now+timedelta(minutes=1))]) == [1, 1]
    assert list(store.iter_between(lo, now)) == rows[:1]
    assert store.active_minutes(lo, now+timedelta(minutes=2)) == 3
    assert store.recent(3) == rows[::-1]
    assert store.path.stat().st_mode & 0o777 == 0o600
    with store._connect() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM events")


def test_v1_migration_preserves_id_and_text(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE events (id INTEGER PRIMARY KEY, ts_ms INTEGER, date TEXT,
          hour INTEGER, minute INTEGER, weekday INTEGER, key_name TEXT,
          keycode INTEGER, char TEXT, kind TEXT, device TEXT);
        INSERT INTO events VALUES (42, 1234567, '1970-01-01', 0, 20, 4,
          'KEY_A', 30, 'a', 'printable', 'Synthetic');
        PRAGMA user_version=1;
    """)
    conn.close()
    store = KeyStore(path)
    store.init_db()
    assert store.max_id() == 42
    row = store.recent(1)[0]
    assert row.ts_us == 1234567000 and row.char == "a"
    assert row.minute_of_day == 20
    assert store.insert_many([row]) == 1
    assert store.max_id() == 43


def test_newer_schema_is_not_overwritten(tmp_path):
    store = KeyStore(tmp_path / "future.db")
    conn = sqlite3.connect(store.path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(sqlite3.OperationalError, match="unsupported schema"):
        store.init_db()
    conn = sqlite3.connect(store.path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


def test_collector_exclusion(store):
    with store.collector_lock():
        with pytest.raises(RuntimeError, match="already owns"):
            with store.collector_lock():
                pass
    with store.collector_lock():
        pass


def test_writer_retries_idle_without_duplicate_rows(store, monkeypatch):
    original = store.open_writer
    def open_writer():
        conn = original()
        conn.execute("PRAGMA busy_timeout=20")
        return conn
    monkeypatch.setattr(store, "open_writer", open_writer)
    blocker = original()
    blocker.execute("BEGIN IMMEDIATE")
    writer = EventWriter(store, queue_size=2)
    writer.start()
    rows = [row_from_press(press()) for _ in range(7)]
    try:
        assert writer.submit(rows)
        wait_for(lambda: writer.last_error is not None)
        blocker.rollback()
        wait_for(lambda: writer.written == 7)
    finally:
        blocker.close()
        assert writer.close()
    assert writer.last_error is None
    assert writer.take_pending() == []
    assert store.total() == 7


def test_timed_out_writer_retains_collector_lock(store):
    blocker = store.open_writer()
    blocker.execute("BEGIN IMMEDIATE")
    writer = EventWriter(store)
    try:
        with store.collector_lock() as fd:
            writer.start(ownership_fd=fd)
            assert writer.submit([row_from_press(press())])
            wait_for(lambda: writer._queue.empty())
            assert not writer.close(timeout=0)
        with pytest.raises(RuntimeError, match="already owns"):
            with store.collector_lock():
                pass
        with pytest.raises(RuntimeError, match="still owns"):
            writer.take_pending()
    finally:
        blocker.rollback()
        blocker.close()
        assert writer.close()
    assert store.total() == 1
    with store.collector_lock():
        pass


def test_daemon_backpressure_retries_on_timer(tmp_path):
    d = dm.Daemon(tmp_path, config=dict(dm.DEFAULT_CONFIG))
    class RejectingWriter:
        calls = 0
        def submit(self, rows):
            self.calls += 1
            return False
    d._writer = RejectingWriter()
    for _ in range(1024):
        d._handle_press(press())
    assert d._writer.calls == 1
    assert len(d._buffer) == 1024
    d._kick_flush()
    assert d._writer.calls == 2


def test_backpressure_hard_bound_order_and_recovery(tmp_path, caplog):
    d = dm.Daemon(tmp_path, config=dict(dm.DEFAULT_CONFIG, flush_interval=5))
    class RecoveringWriter:
        accept = False
        calls = 0
        accepted = []
        def submit(self, rows):
            self.calls += 1
            if self.accept:
                self.accepted = rows
            return self.accept
    d._writer = RecoveringWriter()
    start = time.time_ns() // 1000
    for index in range(120_000):
        d._handle_press(press(ts_us=start + index))
    assert len(d._buffer) == dm.MAX_PENDING_ROWS
    assert d._discarded == 100_000
    assert d._writer.calls == 1
    assert d._buffer[0].ts_us == start + 100_000
    assert d._buffer[-1].ts_us == start + 119_999
    d._writer.accept = True
    d._kick_flush()
    assert not d._buffer and d._discarded == 0
    assert len(d._writer.accepted) == dm.MAX_PENDING_ROWS
    assert "discarded 100000 oldest events" in caplog.text
    assert [r.ts_us for r in d._writer.accepted] == list(range(start + 100_000, start + 120_000))


def test_continuously_readable_device_yields_to_other_tasks():
    listener = lm.KeyListener()
    event = SimpleNamespace(type=kc.EV_KEY, code=kc.KEY_A, value=1, sec=1, usec=0)
    class Device:
        reads = 0
        def read(self):
            self.reads += 1
            assert self.reads <= lm.MAX_READ_BATCHES
            return iter([event])
    device = Device()
    listener._devices["test"] = lm._LiveDevice(device, "Synthetic", "test")
    listener.on_key = lambda _: None
    listener._on_readable("test")
    assert device.reads == lm.MAX_READ_BATCHES


@pytest.mark.parametrize("fail_task", [False, True])
def test_daemon_task_supervision_and_shutdown(tmp_path, monkeypatch, fail_task):
    class SyntheticListener:
        stopped = False
        async def start(self):
            self.on_key(press())
        async def stop(self):
            self.stopped = True
    monkeypatch.setattr(dm, "KeyListener", SyntheticListener)
    monkeypatch.setattr(dm, "_setup_logging", lambda *_: None)
    d = dm.Daemon(tmp_path, config=dict(dm.DEFAULT_CONFIG))
    async def failed_flush():
        raise RuntimeError("synthetic background failure")
    async def run():
        if fail_task:
            d._flush_loop = failed_flush
            with pytest.raises(ExceptionGroup, match="TaskGroup"):
                await asyncio.wait_for(d.run(), 2)
        else:
            asyncio.get_running_loop().call_later(0.05, d.stop_sync)
            await asyncio.wait_for(d.run(), 2)
    asyncio.run(run())
    assert d._listener.stopped
    assert not d._writer.is_alive
    assert d._store.total() == 1
    with d._store.collector_lock():
        pass


def test_listener_drains_batches_and_resyncs(monkeypatch):
    listener = lm.KeyListener()
    def event(type_, code, value):
        return SimpleNamespace(type=type_, code=code, value=value, sec=1, usec=23)
    batches = iter([
        [event(kc.EV_KEY, kc.KEY_A, 1)],
        [event(kc.EV_SYN, kc.SYN_DROPPED, 0), event(kc.EV_KEY, kc.KEY_B, 1)],
        [event(kc.EV_SYN, kc.SYN_REPORT, 0), event(kc.EV_KEY, kc.KEY_C, 1)],
    ])
    class Device:
        def read(self):
            try:
                return iter(next(batches))
            except StopIteration:
                raise BlockingIOError(errno.EAGAIN, "empty")
    live = lm._LiveDevice(Device(), "Synthetic", "test")
    listener._devices["test"] = live
    hydration = []
    monkeypatch.setattr(listener, "_hydrate", lambda x: hydration.append(x))
    presses = []
    listener.on_key = presses.append
    listener._on_readable("test")
    assert [p.keycode for p in presses] == [kc.KEY_A, kc.KEY_C]
    assert presses[0].ts_us == 1000023
    assert hydration == [live] and listener.dropped_overruns == 1


def test_inotify_real_create_and_delete(tmp_path):
    fd = lm._open_inotify(str(tmp_path))
    try:
        path = tmp_path / "event-test"
        path.touch()
        path.unlink()
        parsed = lm._parse_inotify(os.read(fd, 65536))
        assert any(mask & lm._IN_CREATE and name == path.name for mask, name in parsed)
        assert any(mask & lm._IN_DELETE and name == path.name for mask, name in parsed)
    finally:
        os.close(fd)


def test_watch_retry_after_missing_directory(tmp_path, monkeypatch):
    directory = tmp_path / "input"
    real_open = lm._open_inotify
    monkeypatch.setattr(lm, "_open_inotify", lambda _: real_open(str(directory)))
    monkeypatch.setattr(lm, "_list_device_paths", lambda: [])
    listener = lm.KeyListener()
    async def run():
        await listener.start()
        assert listener._inotify_fd is None
        directory.mkdir()
        listener._rescan()
        assert listener._inotify_fd is not None
        directory.rmdir()
        await asyncio.sleep(0.02)
        assert listener._inotify_fd is None
        directory.mkdir()
        listener._rescan()
        assert listener._inotify_fd is not None
        await listener.stop()
    asyncio.run(run())


def test_repeated_dst_minutes_count_separately(store, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        # The 01:30 minute occurs twice at the November DST transition.
        first = datetime(2026, 11, 1, 1, 30, fold=0)
        second = datetime(2026, 11, 1, 1, 30, fold=1)
        rows = [row_from_press(press(ts_us=int(t.timestamp()*1_000_000)))
                for t in (first, second)]
        assert rows[0].minute_of_day == rows[1].minute_of_day
        store.insert_many(rows)
        assert store.active_minutes(datetime(2026, 11, 1), datetime(2026, 11, 2)) == 2
    finally:
        monkeypatch.undo()
        time.tzset()
