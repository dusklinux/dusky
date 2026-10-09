"""SQLite persistence for keystroke events.

Schema v2 stores the kernel event timestamp at microsecond resolution
plus precomputed local calendar parts so daily / hourly / minute
aggregations are indexed range scans. WAL + a dedicated writer thread
keep the asyncio evdev loop off the disk path.

On-disk format is SQLite STRICT. application_id = 0x44534B59 ('DSKY').
"""

from __future__ import annotations

import fcntl
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
APPLICATION_ID = 0x44534B59  # 'DSKY'

# WAL is persistent; connection tuning stays separate from schema metadata.
_WRITER_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -8192",
    "PRAGMA mmap_size = 134217728",
    "PRAGMA wal_autocheckpoint = 1000",
    "PRAGMA foreign_keys = ON",
    "PRAGMA analysis_limit = 400",
    "PRAGMA trusted_schema = OFF",
)

# Readers must never touch journal_mode -- setting it is a write that
# contends with the daemon for the WAL write lock, and it raises
# "attempt to write a readonly database" on a mode=ro connection when
# the file is not already in WAL. WAL is persistent in the file header;
# readers only need query_only + busy_timeout + cache tuning.
_READER_PRAGMAS = (
    "PRAGMA query_only = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA cache_size = -4096",
    "PRAGMA mmap_size = 134217728",
    "PRAGMA trusted_schema = OFF",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_us         INTEGER NOT NULL,
    ts_ms         INTEGER NOT NULL,
    local_date    TEXT    NOT NULL,
    hour          INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    minute        INTEGER NOT NULL CHECK (minute BETWEEN 0 AND 59),
    minute_of_day INTEGER NOT NULL CHECK (minute_of_day BETWEEN 0 AND 1439),
    weekday       INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    key_name      TEXT    NOT NULL,
    keycode       INTEGER NOT NULL,
    char          TEXT,
    kind          TEXT    NOT NULL,
    device        TEXT    NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts   ON events(kind, ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_key_ts    ON events(key_name, ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_date_mod  ON events(local_date, minute_of_day);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
"""

_INSERT_SQL = (
    "INSERT INTO events (ts_us, ts_ms, local_date, hour, minute, "
    "minute_of_day, weekday, key_name, keycode, char, kind, device) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class PressLike(Protocol):
    key_name: str
    keycode: int
    char: str | None
    kind: str
    device: str
    ts_us: int


@dataclass(frozen=True, slots=True)
class EventRow:
    """A single persisted key press."""

    ts_us: int
    ts_ms: int
    local_date: str
    hour: int
    minute: int
    minute_of_day: int
    weekday: int
    key_name: str
    keycode: int
    char: str | None
    kind: str
    device: str


def _local_parts(ts_ms: int) -> tuple[str, int, int, int]:
    """Split epoch-ms into local (YYYY-MM-DD, hour, minute, ISO weekday)."""
    t = time.localtime(ts_ms / 1000.0)
    iso_wd = t.tm_wday + 1  # Monday=1 .. Sunday=7
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}",
        t.tm_hour,
        t.tm_min,
        iso_wd,
    )


def row_from_press(press: PressLike, ts_ms: int | None = None) -> EventRow:
    """Build an EventRow from a listener.KeyPress.

    Prefer the kernel-stamped press.ts_us. ts_ms is accepted as
    an override for the seed CLI (synthetic wall-clock times).
    """
    if ts_ms is None:
        ts_us = press.ts_us
        ts_ms = ts_us // 1000
    else:
        ts_us = ts_ms * 1000
    date_s, hour, minute, weekday = _local_parts(ts_ms)
    return EventRow(
        ts_us=ts_us,
        ts_ms=ts_ms,
        local_date=date_s,
        hour=hour,
        minute=minute,
        minute_of_day=hour * 60 + minute,
        weekday=weekday,
        key_name=press.key_name,
        keycode=press.keycode,
        char=press.char,
        kind=str(press.kind),
        device=press.device,
    )


def _row_params(r: EventRow) -> tuple:
    return (
        r.ts_us,
        r.ts_ms,
        r.local_date,
        r.hour,
        r.minute,
        r.minute_of_day,
        r.weekday,
        r.key_name,
        r.keycode,
        r.char,
        r.kind,
        r.device,
    )


def _configure(conn: sqlite3.Connection, *, read_only: bool = False) -> None:
    conn.row_factory = sqlite3.Row
    pragmas = _READER_PRAGMAS if read_only else _WRITER_PRAGMAS
    for pragma in pragmas:
        conn.execute(pragma)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _execute_schema(conn: sqlite3.Connection) -> None:
    # executescript() commits a pending transaction in sqlite3's default mode.
    # These static DDL statements must stay inside the caller's transaction.
    for statement in _SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Atomically rebuild v1, preserving row IDs and rebuilding its indexes."""
    table_sql = _SCHEMA.split("CREATE INDEX", 1)[0]
    conn.execute(table_sql.replace("events (", "events_v2 (", 1).strip().rstrip(";"))
    conn.execute(
        """
        INSERT INTO events_v2 (
            id, ts_us, ts_ms, local_date, hour, minute, minute_of_day,
            weekday, key_name, keycode, char, kind, device
        )
        SELECT id, ts_ms * 1000, ts_ms, date, hour, minute, hour * 60 + minute,
               weekday, key_name, keycode, char, kind, device
        FROM events
        """
    )
    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE events_v2 RENAME TO events")


class KeyStore:
    """SQLite-backed event store.

    Writers should hold one long-lived connection (see EventWriter).
    Readers open a short-lived connection per call; WAL lets them run
    concurrently with the daemon writer without blocking evdev.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    @property
    def path(self) -> Path:
        return self._db_path

    @contextmanager
    def collector_lock(self) -> Iterator[int]:
        """One collector per database; purge uses the same ownership lock."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
        # Keep the lock file in place: unlinking it allows a second inode/owner.
        fd = os.open(
            self._db_path.with_name(self._db_path.name + '.lock'),
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
            0o600,
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"Collector already owns {self._db_path}; stop it first") from exc
            yield fd
        finally:
            os.close(fd)

    def init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
        # If DB already exists, ensure its permissions are tight before opening.
        if self._db_path.exists():
            try:
                os.chmod(self._db_path, 0o600)
            except OSError:
                pass
        with self._connect(writable=True) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise sqlite3.OperationalError(f"unsupported schema version: {version}")
                tables = {
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "events" in tables:
                    cols = _table_columns(conn, "events")
                    if "date" in cols and "local_date" not in cols:
                        _migrate_v1_to_v2(conn)
                # date_mod already indexes the local_date prefix.
                conn.execute("DROP INDEX IF EXISTS idx_events_date")
                _execute_schema(conn)
                conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) "
                    "VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
        # The keystroke DB contains typed text (including passphrases):
        # lock it down so no other local user can read it.
        try:
            os.chmod(self._db_path, 0o600)
            # Also tighten wal/shm if they exist
            for suffix in ("-wal", "-shm"):
                p = Path(str(self._db_path) + suffix)
                if p.exists():
                    os.chmod(p, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self, *, writable: bool = False) -> Iterator[sqlite3.Connection]:
        if writable:
            conn = sqlite3.connect(self._db_path, timeout=10.0)
        else:
            # If DB doesn't exist yet, fail fast rather than creating an empty file
            # that has no schema -- callers should call init_db() first.
            if not self._db_path.exists():
                raise sqlite3.OperationalError(f"database not initialized: {self._db_path}")
            uri = self._db_path.absolute().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
        try:
            _configure(conn, read_only=not writable)
            yield conn
        finally:
            conn.close()

    def open_writer(self) -> sqlite3.Connection:
        """Open a long-lived writer connection. Caller owns the lifetime."""
        # Ensure parent exists so sqlite can create the file; chmod will be done in init_db.
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        try:
            _configure(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def insert_many(self, rows: list[EventRow], conn: sqlite3.Connection | None = None) -> int:
        if not rows:
            return 0
        payload = [_row_params(r) for r in rows]
        if conn is not None:
            with conn:
                conn.executemany(_INSERT_SQL, payload)
            return len(rows)
        with self._connect(writable=True) as owned:
            with owned:
                owned.executemany(_INSERT_SQL, payload)
        return len(rows)

    def optimize(self) -> None:
        """Cheap shutdown maintenance. Never VACUUM here.

        VACUUM takes an exclusive lock, rewrites the whole file into
        the WAL, and delays systemd stop. PRAGMA optimize updates
        query planner stats; a PASSIVE checkpoint never fights readers.
        """
        with self._connect(writable=True) as conn:
            conn.execute("PRAGMA optimize")
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def total(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()
        return int(row[0])

    def count_between(self, start: datetime, end: datetime) -> int:
        lo = int(start.timestamp() * 1000)
        hi = int(end.timestamp() * 1000)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE ts_ms >= ? AND ts_ms < ?",
                (lo, hi),
            ).fetchone()
        return int(row[0])

    def count_ranges(self, ranges: list[tuple[datetime, datetime]]) -> list[int]:
        """Independent covering-index range counts for dashboard cards."""
        if not ranges:
            return []
        parts: list[str] = []
        params: list[int] = []
        for start, end in ranges:
            parts.append(
                "(SELECT COUNT(*) FROM events WHERE ts_ms >= ? AND ts_ms < ?)"
            )
            params.append(int(start.timestamp() * 1000))
            params.append(int(end.timestamp() * 1000))
        sql = f"SELECT {', '.join(parts)}"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return [int(v or 0) for v in row]

    def count_days_ago(self, days: int) -> int:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        day_start = start - timedelta(days=days)
        return self.count_between(day_start, day_start + timedelta(days=1))

    def daily_totals(self, days: int) -> list[tuple[str, int]]:
        today = date.today()
        cutoff = (today - timedelta(days=days - 1)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT local_date, COUNT(*) AS c FROM events "
                "WHERE local_date >= ? GROUP BY local_date ORDER BY local_date",
                (cutoff,),
            ).fetchall()
        counts = {str(d): int(c) for d, c in rows}
        result: list[tuple[str, int]] = []
        for i in range(days - 1, -1, -1):
            d_str = (today - timedelta(days=i)).isoformat()
            result.append((d_str, counts.get(d_str, 0)))
        return result

    def hourly_totals(self, day: str) -> list[tuple[int, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT hour, COUNT(*) AS c FROM events "
                "WHERE local_date = ? GROUP BY hour ORDER BY hour",
                (day,),
            ).fetchall()
        counts = {int(h): int(c) for h, c in rows}
        return [(h, counts.get(h, 0)) for h in range(24)]

    def active_minutes(self, start: datetime, end: datetime) -> int:
        lo = int(start.timestamp() * 1000)
        hi = int(end.timestamp() * 1000)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM ("
                "  SELECT 1 FROM events"
                "  WHERE ts_ms >= ? AND ts_ms < ?"
                "  GROUP BY ts_ms / 60000"
                ")",
                (lo, hi),
            ).fetchone()
        return int(row[0])

    def kind_totals(self, start: datetime, end: datetime) -> dict[str, int]:
        lo = int(start.timestamp() * 1000)
        hi = int(end.timestamp() * 1000)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS c FROM events "
                "WHERE ts_ms >= ? AND ts_ms < ? GROUP BY kind",
                (lo, hi),
            ).fetchall()
        return {str(k): int(c) for k, c in rows}

    def top_keys(
        self,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[tuple[str, int]]:
        limit = max(1, min(int(limit), 1000))
        if start is not None and end is not None:
            sql = (
                "SELECT key_name, COUNT(*) AS c FROM events "
                "WHERE ts_ms >= ? AND ts_ms < ? "
                "GROUP BY key_name ORDER BY c DESC LIMIT ?"
            )
            params: tuple = (
                int(start.timestamp() * 1000),
                int(end.timestamp() * 1000),
                limit,
            )
        else:
            sql = (
                "SELECT key_name, COUNT(*) AS c FROM events "
                "GROUP BY key_name ORDER BY c DESC LIMIT ?"
            )
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(str(k), int(c)) for k, c in rows]

    def printable_frequency(
        self,
        limit: int = 20,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[tuple[str, int]]:
        limit = max(1, min(int(limit), 1000))
        if start is not None and end is not None:
            sql = (
                "SELECT char, COUNT(*) AS c FROM events "
                "WHERE char IS NOT NULL AND ts_ms >= ? AND ts_ms < ? "
                "GROUP BY char ORDER BY c DESC LIMIT ?"
            )
            params: tuple = (
                int(start.timestamp() * 1000),
                int(end.timestamp() * 1000),
                limit,
            )
        else:
            sql = (
                "SELECT char, COUNT(*) AS c FROM events "
                "WHERE char IS NOT NULL "
                "GROUP BY char ORDER BY c DESC LIMIT ?"
            )
            params = (limit,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(str(c), int(n)) for c, n in rows]

    def recent(self, limit: int = 30) -> list[EventRow]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts_us, ts_ms, local_date, hour, minute, minute_of_day, "
                "weekday, key_name, keycode, char, kind, device "
                "FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [EventRow(*r) for r in rows]

    def first_last_ts(self) -> tuple[int | None, int | None]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT (SELECT MIN(ts_ms) FROM events), (SELECT MAX(ts_ms) FROM events)"
            ).fetchone()
        return (
            int(row[0]) if row[0] is not None else None,
            int(row[1]) if row[1] is not None else None,
        )

    def max_id(self) -> int:
        """Latest event rowid -- O(1) change detector for dashboard caches."""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(id) FROM events").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.Error:
            return 0

    def iter_between(
        self, start: datetime, end: datetime
    ) -> Iterator[EventRow]:
        """Stream events in a range in time order (exports / transcripts).

        Read-only; never writes to the store.
        """
        lo = int(start.timestamp() * 1000)
        hi = int(end.timestamp() * 1000)
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT ts_us, ts_ms, local_date, hour, minute, minute_of_day, "
                "weekday, key_name, keycode, char, kind, device "
                "FROM events WHERE ts_ms >= ? AND ts_ms < ? ORDER BY ts_ms, ts_us, id",
                (lo, hi),
            )
            for r in cur:
                yield EventRow(*r)


class EventWriter:
    """Bounded, non-blocking producer queue with one SQLite writer.

    Failed batches retry even while idle. During a database outage, the worker
    keeps its current batch and stops consuming the bounded queue.
    """

    def __init__(self, store: KeyStore, queue_size: int = 64) -> None:
        self._store = store
        self._queue: queue.Queue[list[EventRow]] = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._closing = threading.Event()
        self._error: BaseException | None = None
        self._written = 0
        self._lock = threading.Lock()
        self._retry: list[EventRow] = []

    @property
    def written(self) -> int:
        with self._lock:
            return self._written

    @property
    def last_error(self) -> BaseException | None:
        return self._error

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, ownership_fd: int | None = None) -> None:
        if self._thread is not None:
            return
        # Share the same flock ownership until the worker actually exits,
        # including when its caller's close deadline expires.
        worker_fd = os.dup(ownership_fd) if ownership_fd is not None else None
        try:
            self._thread = threading.Thread(
                target=self._run, args=(worker_fd,), name="dusky-sqlite-writer", daemon=True
            )
            self._thread.start()
        except BaseException:
            self._thread = None
            if worker_fd is not None:
                os.close(worker_fd)
            raise

    def submit(self, rows: list[EventRow]) -> bool:
        if self._closing.is_set() or not self.is_alive:
            return False
        if not rows:
            return True
        try:
            self._queue.put_nowait(rows)
            return True
        except queue.Full:
            return False

    def close(self, timeout: float = 8.0) -> bool:
        """Stop after draining. False means the worker still owns its rows."""
        self._closing.set()
        if self._thread is None:
            return True
        # Wake an idle blocking get. A full queue already gives it work to drain.
        try:
            self._queue.put_nowait([])
        except queue.Full:
            pass
        self._thread.join(timeout=max(0.0, timeout))
        if self.is_alive:
            logger.error("Writer thread did not exit within %.1fs", timeout)
            return False
        return True

    def take_pending(self) -> list[EventRow]:
        """Transfer unwritten rows only after the writer has actually exited."""
        if self.is_alive:
            raise RuntimeError("writer still owns pending rows")
        rows, self._retry = self._retry, []
        while True:
            try:
                rows.extend(self._queue.get_nowait())
            except queue.Empty:
                return rows

    def _run(self, ownership_fd: int | None) -> None:
        try:
            self._write_loop()
        finally:
            if ownership_fd is not None:
                os.close(ownership_fd)

    def _write_loop(self) -> None:
        conn = None
        try:
            conn = self._store.open_writer()
            while True:
                if not self._retry:
                    try:
                        batch = self._queue.get(block=not self._closing.is_set())
                    except queue.Empty:
                        break
                    if not batch:
                        continue
                    # Bound coalescing to the queue snapshot so continuous
                    # producers cannot postpone a commit indefinitely.
                    for _ in range(self._queue.qsize()):
                        try:
                            batch.extend(self._queue.get_nowait())
                        except queue.Empty:
                            break
                    self._retry = batch
                if not self._flush(conn):
                    if self._closing.wait(0.5):
                        break
        except Exception as exc:
            self._error = exc
            logger.exception("Writer thread failed")
        finally:
            if conn is not None:
                try:
                    if self._error is None:
                        conn.execute("PRAGMA optimize")
                        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.Error:
                    logger.debug("Writer maintenance failed", exc_info=True)
                finally:
                    conn.close()

    def _flush(self, conn: sqlite3.Connection) -> bool:
        try:
            with conn:
                conn.executemany(_INSERT_SQL, [_row_params(r) for r in self._retry])
            with self._lock:
                self._written += len(self._retry)
            self._retry = []
            self._error = None
            return True
        except sqlite3.Error as exc:
            self._error = exc
            return False
