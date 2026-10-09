"""Raw keyboard event capture via evdev.

Architecture (Kernel 7.3+ input subsystem, python-evdev):

* Single-pass discovery. A device is a keyboard if it reports any
  EV_KEY code < 256. Combo boards (wheel / touch-strip) are accepted;
  pure mice fail the key-code test because BTN_* starts at 256.
* EVIOCSMASK restricts the per-client kernel queue to EV_KEY + EV_LED
  (EV_SYN is always delivered). REL/ABS floods from combo devices can
  no longer push KEY events out of the ring.
* The asyncio loop registers the device fd with add_reader and drains
  the whole pending batch via InputDevice.read() on every wakeup.
  That is the minimum-latency path; a per-event async for would yield
  between events and raise SYN_DROPPED risk.
* SYN_DROPPED is handled per the kernel contract in
  Documentation/input/event-codes.rst: discard every event up to and
  including the next SYN_REPORT, then EVIOCGKEY + EVIOCGLED
  (InputDevice.active_keys / leds) to rebuild modifier / lock state.
* Timestamps are the kernel's event.sec/event.usec, not time.time()
  at handle time. A stalled userspace no longer back-dates keystrokes.
* Hot-plug is inotify on /dev/input (IN_CREATE/DELETE/ATTRIB/Q_OVERFLOW)
  plus a 30s safety rescan. ATTRIB covers the udev-ACL race where
  CREATE fires before the node is readable by group input.
* Device identity is the path string, never the recycled fd number.
"""

import asyncio
import contextlib
import ctypes
import fcntl
import glob
import logging
import os
import struct
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import keycodes as kc
lazy from evdev import InputDevice

logger = logging.getLogger(__name__)
MAX_READ_BATCHES = 16

# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class KeyPress:
    """A single logged key press, kernel-timestamped in microseconds."""

    keycode: int
    key_name: str
    char: str | None
    kind: str
    device: str
    ts_us: int


@dataclass(slots=True)
class _DeviceState:
    """Per-device modifier / lock state across press + release."""

    shortcut_modifiers: set[int] = field(default_factory=set)
    shift_keys: set[int] = field(default_factory=set)
    caps_on: bool = False
    num_on: bool = True
    caps_from_led: bool = False
    num_from_led: bool = False

    @property
    def shift_pressed(self) -> bool:
        return bool(self.shift_keys)


class KeyEventHandler(Protocol):
    def __call__(self, press: KeyPress) -> None: ...


# ---------------------------------------------------------------------------
# Pure classifier -- no evdev import, fully unit-testable
# ---------------------------------------------------------------------------


class KeyEventClassifier:
    """Decide which raw (keycode, value) events become KeyPress records.

    value == 1 is a press, value == 2 (auto-repeat) extends a held key
    and is not counted, value == 0 is a release. Non-modifier,
    non-backspace keys are dropped while a shortcut modifier (Ctrl/Meta)
    is held on the same device.
    """

    def __init__(self) -> None:
        self._devices: dict[str, _DeviceState] = {}

    def reset(self, device_id: str | None = None) -> None:
        if device_id is None:
            self._devices.clear()
        else:
            self._devices.pop(device_id, None)

    def state_for(self, device_id: str) -> _DeviceState:
        state = self._devices.get(device_id)
        if state is None:
            state = self._devices[device_id] = _DeviceState()
        return state

    def sync_from_kernel(
        self,
        device_id: str,
        active_keys: Iterable[int],
        caps_led: bool | None,
        num_led: bool | None,
    ) -> None:
        """Replace software state with EVIOCGKEY / EVIOCGLED truth."""
        state = self.state_for(device_id)
        held = set(active_keys)
        state.shortcut_modifiers = {k for k in held if kc.is_shortcut_modifier(k)}
        state.shift_keys = {k for k in held if kc.is_shift(k)}
        if caps_led is not None:
            state.caps_on = caps_led
            state.caps_from_led = True
        if num_led is not None:
            state.num_on = num_led
            state.num_from_led = True

    def handle_led(self, device_id: str, led_code: int, value: int) -> None:
        state = self.state_for(device_id)
        on = value != 0
        if led_code == kc.LED_CAPSL:
            state.caps_on = on
            state.caps_from_led = True
        elif led_code == kc.LED_NUML:
            state.num_on = on
            state.num_from_led = True

    def handle(
        self,
        device_id: str,
        device_name: str,
        keycode: int,
        value: int,
        ts_us: int,
    ) -> KeyPress | None:
        """Feed one EV_KEY event. Returns a KeyPress or None."""
        if kc.is_button(keycode):
            return None
        state = self.state_for(device_id)

        if kc.is_shortcut_modifier(keycode):
            if value in (1, 2):
                state.shortcut_modifiers.add(keycode)
            elif value == 0:
                state.shortcut_modifiers.discard(keycode)

        if kc.is_shift(keycode):
            if value in (1, 2):
                state.shift_keys.add(keycode)
            elif value == 0:
                state.shift_keys.discard(keycode)
            if value != 1:
                return None
            return KeyPress(
                keycode=keycode,
                key_name=kc.key_name(keycode),
                char=None,
                kind=kc.KIND_MODIFIER,
                device=device_name,
                ts_us=ts_us,
            )

        if keycode == kc.KEY_CAPSLOCK:
            if value == 1:
                # If we are tracking real LED state, don't soft-toggle.
                if not state.caps_from_led:
                    state.caps_on = not state.caps_on
                return KeyPress(
                    keycode=keycode,
                    key_name=kc.key_name(keycode),
                    char=None,
                    kind=kc.KIND_MODIFIER,
                    device=device_name,
                    ts_us=ts_us,
                )
            return None

        if keycode == kc.KEY_NUMLOCK:
            if value == 1:
                if not state.num_from_led:
                    state.num_on = not state.num_on
                return KeyPress(
                    keycode=keycode,
                    key_name=kc.key_name(keycode),
                    char=None,
                    kind=kc.KIND_MODIFIER,
                    device=device_name,
                    ts_us=ts_us,
                )
            return None

        if value != 1:
            return None

        # While a shortcut modifier (Ctrl/Meta) is held, most keys are part
        # of a shortcut (Ctrl+C etc.) and should not be counted as typed
        # text. Backspace is exempt so Ctrl+Backspace still tracks corrections,
        # and modifiers themselves are always logged for stats completeness.
        # Delete is intentionally *not* exempt: Ctrl+Delete is a word-delete
        # shortcut, not free-form typing.
        if state.shortcut_modifiers and not (
            kc.is_backspace(keycode) or kc.is_modifier(keycode)
        ):
            return None

        char = kc.char_for(
            keycode, state.shift_pressed, state.caps_on, state.num_on
        )
        kind: str = kc.classify_key(keycode)
        if keycode in kc.KEYPAD_NAV_KEYS and not state.num_on:
            kind = kc.KIND_NAVIGATION
            char = None
        return KeyPress(
            keycode=keycode,
            key_name=kc.key_name(keycode),
            char=char,
            kind=kind,
            device=device_name,
            ts_us=ts_us,
        )


# ---------------------------------------------------------------------------
# ioctl: EVIOCSMASK -- drop REL/ABS/MSC from this client's kernel queue
# ---------------------------------------------------------------------------

_IOC_WRITE = 1


def _ioc(direction: int, ioc_type: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ioc_type << 8) | nr


class _InputMask(ctypes.Structure):
    _fields_ = (
        ("type", ctypes.c_uint32),
        ("codes_size", ctypes.c_uint32),
        ("codes_ptr", ctypes.c_uint64),
    )


_EVIOCSMASK = _ioc(_IOC_WRITE, ord("E"), 0x93, ctypes.sizeof(_InputMask))


def apply_event_mask(fd: int) -> None:
    """Use EVIOCSMASK type 0 to admit KEY/LED; SYN is always delivered.

    A per-client event-type mask filters all other known types before they
    enter its ring, including types without individual code masks.
    """
    bits = (1 << kc.EV_KEY) | (1 << kc.EV_LED)
    buf = ctypes.c_ulong(bits)
    mask = _InputMask(0, ctypes.sizeof(buf), ctypes.addressof(buf))
    fcntl.ioctl(fd, _EVIOCSMASK, mask)


# ---------------------------------------------------------------------------
# inotify on /dev/input
# ---------------------------------------------------------------------------

_IN_CLOEXEC = os.O_CLOEXEC
_IN_NONBLOCK = os.O_NONBLOCK
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_MOVED_TO = 0x00000080
_IN_MOVED_FROM = 0x00000040
_IN_ATTRIB = 0x00000004
_IN_Q_OVERFLOW = 0x00004000
_IN_IGNORED = 0x00008000
_IN_DELETE_SELF = 0x00000400
_IN_MOVE_SELF = 0x00000800
_IN_WATCH_MASK = (
    _IN_CREATE | _IN_DELETE | _IN_MOVED_TO | _IN_MOVED_FROM | _IN_ATTRIB
    | _IN_DELETE_SELF | _IN_MOVE_SELF
)
_INOTIFY_HDR = struct.Struct("iIII")  # wd, mask, cookie, len


def _libc() -> ctypes.CDLL:
    # Linux exposes libc symbols through the current process handle; no
    # library-name guessing or external discovery command is needed.
    lib = ctypes.CDLL(None, use_errno=True)
    lib.inotify_init1.argtypes = [ctypes.c_int]
    lib.inotify_init1.restype = ctypes.c_int
    lib.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    lib.inotify_add_watch.restype = ctypes.c_int
    return lib


def _open_inotify(path: str = "/dev/input") -> int:
    lib = _libc()
    fd = lib.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), "inotify_init1")
    wd = lib.inotify_add_watch(fd, path.encode(), _IN_WATCH_MASK)
    if wd < 0:
        err = ctypes.get_errno()
        os.close(fd)
        raise OSError(err, os.strerror(err), "inotify_add_watch")
    return fd


def _parse_inotify(data: bytes) -> list[tuple[int, str]]:
    events: list[tuple[int, str]] = []
    offset = 0
    n = len(data)
    while offset + _INOTIFY_HDR.size <= n:
        _wd, mask, _cookie, name_len = _INOTIFY_HDR.unpack_from(data, offset)
        offset += _INOTIFY_HDR.size
        raw = data[offset : offset + name_len]
        offset += name_len
        name = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        events.append((mask, name))
    return events


# ---------------------------------------------------------------------------
# Live device + listener
# ---------------------------------------------------------------------------


def _list_device_paths() -> list[str]:
    """Enumerate /dev/input/event* directly.

    python-evdev's list_devices() only returns nodes the caller can open
    for BOTH read and write (os.access R_OK|W_OK). Under a read-only
    systemd device sandbox (DeviceAllow=char-input r) that filter hides
    every keyboard even though O_RDONLY opens are permitted -- globbing
    the node names directly avoids the filter.
    """
    return sorted(glob.glob("/dev/input/event*"))


def _event_ts_us(event: Any) -> int:
    """Kernel input_event timestamp as epoch microseconds.

    Falls back to CLOCK_REALTIME when the event carries no usable
    timestamp (synthetic events, or a zeroed edge case).
    """
    sec = int(getattr(event, "sec", 0) or 0)
    usec = int(getattr(event, "usec", 0) or 0)
    ts = sec * 1_000_000 + usec
    if ts <= 0:
        ts = time.time_ns() // 1000
    return ts


@dataclass(slots=True)
class _LiveDevice:
    device: Any
    name: str
    path: str
    resyncing: bool = False


class KeyListener:
    """Owns evdev devices, inotify hot-plug, and the asyncio readers."""

    def __init__(self) -> None:
        self._InputDevice = InputDevice
        self.classifier = KeyEventClassifier()
        self._devices: dict[str, _LiveDevice] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inotify_fd: int | None = None
        self._rescan_handle: asyncio.TimerHandle | None = None
        self._device_filter = os.environ.get("DUSKY_DEVICE_FILTER", "")
        self.received_events = 0
        self.logged_presses = 0
        self.dropped_overruns = 0

    # -- discovery ---------------------------------------------------------

    def _is_keyboard(self, device: Any) -> bool:
        try:
            caps = device.capabilities()
        except OSError:
            return False
        keys = caps.get(kc.EV_KEY, ())
        # evdev may return list[int] for EV_KEY; some wrappers return list[tuple] for ABS.
        def _code(v: Any) -> int:
            if isinstance(v, int):
                return v
            if isinstance(v, (list, tuple)) and v:
                return int(v[0]) if isinstance(v[0], int) else 0
            return 999
        return any(_code(k) < 256 for k in keys)

    def _passes_filter(self, name: str) -> bool:
        if not self._device_filter:
            return True
        return self._device_filter.lower() in name.lower()

    def _scan(self) -> list[Any]:
        found: list[Any] = []
        for path in _list_device_paths():
            device = None
            try:
                device = self._InputDevice(path, readonly=True)
                if self._is_keyboard(device) and self._passes_filter(device.name):
                    found.append(device)
                    logger.info("Discovered keyboard: %s (%s)", device.name, path)
                else:
                    device.close()
            except OSError as exc:
                logger.debug("Skipping device %s: %s", path, exc)
                if device is not None:
                    with contextlib.suppress(OSError):
                        device.close()
        return found

    def _find_keyboards(self) -> list[Any]:
        return self._scan()

    # -- kernel state ------------------------------------------------------

    def _hydrate(self, live: _LiveDevice) -> None:
        active: list[int] = []
        caps_led: bool | None = None
        num_led: bool | None = None
        try:
            active = live.device.active_keys()
        except OSError as exc:
            logger.debug("EVIOCGKEY failed on %s: %s", live.path, exc)
        try:
            leds = set(live.device.leds())
            # capabilities for EV_LED is list[int] on most kernels; handle tuple form.
            caps_raw = live.device.capabilities().get(kc.EV_LED, ())
            caps: set[int] = set()
            for c in caps_raw:
                if isinstance(c, int):
                    caps.add(c)
                elif isinstance(c, (list, tuple)) and c:
                    caps.add(int(c[0]))
            if kc.LED_CAPSL in caps:
                caps_led = kc.LED_CAPSL in leds
            if kc.LED_NUML in caps:
                num_led = kc.LED_NUML in leds
        except OSError as exc:
            logger.debug("EVIOCGLED failed on %s: %s", live.path, exc)
        self.classifier.sync_from_kernel(live.path, active, caps_led, num_led)
        logger.info(
            "Hydrated %s: %d keys held, caps=%s num=%s",
            live.path,
            len(active),
            caps_led,
            num_led,
        )

    def _mask(self, device: Any) -> None:
        try:
            apply_event_mask(device.fd)
        except OSError as exc:
            logger.warning("EVIOCSMASK failed on %s: %s", device.path, exc)

    # -- attach / detach ---------------------------------------------------

    def _attach(self, device: Any) -> None:
        path = device.path
        if path in self._devices:
            with contextlib.suppress(OSError):
                device.close()
            return
        if not self._is_keyboard(device) or not self._passes_filter(device.name):
            with contextlib.suppress(OSError):
                device.close()
            return
        self._mask(device)
        live = _LiveDevice(device=device, name=device.name, path=path)
        self._devices[path] = live
        self._hydrate(live)
        assert self._loop is not None
        self._loop.add_reader(device.fd, self._on_readable, path)
        logger.info("Listening on: %s (%s)", device.name, path)

    def _detach(self, path: str) -> None:
        live = self._devices.pop(path, None)
        if live is None:
            return
        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(live.device.fd)
        with contextlib.suppress(OSError):
            live.device.close()
        self.classifier.reset(path)
        logger.info("Detached %s", path)

    def _try_attach_path(self, path: str) -> None:
        if path in self._devices:
            return
        # Quick check to avoid log spam on non-event nodes.
        if not path.startswith("/dev/input/event"):
            return
        try:
            device = self._InputDevice(path, readonly=True)
        except OSError as exc:
            logger.debug("Could not open %s: %s", path, exc)
            return
        self._attach(device)

    # -- event path (hot: must not block) ----------------------------------

    def _on_readable(self, path: str) -> None:
        live = self._devices.get(path)
        if live is None:
            return
        try:
            # Drain pending batches, but yield to timers/other devices under
            # continuous input. The level-triggered reader will wake us again.
            for _ in range(MAX_READ_BATCHES):
                for event in live.device.read():
                    self._dispatch(live, event)
        except BlockingIOError:
            return
        except OSError:
            self._detach(path)
            return

    def _dispatch(self, live: _LiveDevice, event: Any) -> None:
        etype = event.type
        if etype == kc.EV_SYN:
            if event.code == kc.SYN_DROPPED:
                live.resyncing = True
                self.dropped_overruns += 1
                logger.warning(
                    "SYN_DROPPED on %s (%s) -- discarding to next SYN_REPORT, "
                    "then EVIOCGKEY/EVIOCGLED",
                    live.name,
                    live.path,
                )
                return
            if live.resyncing and event.code == kc.SYN_REPORT:
                self._hydrate(live)
                live.resyncing = False
            return
        if live.resyncing:
            return
        if etype == kc.EV_LED:
            self.classifier.handle_led(live.path, event.code, event.value)
            return
        if etype != kc.EV_KEY:
            return
        self.received_events += 1
        ts_us = _event_ts_us(event)
        press = self.classifier.handle(
            live.path, live.name, event.code, event.value, ts_us
        )
        if press is not None:
            self.logged_presses += 1
            self.on_key(press)

    def _on_key(self, press: KeyPress) -> None:
        logger.debug("Key: %s %r", press.key_name, press.char)

    on_key: KeyEventHandler = _on_key

    # -- inotify + safety rescan ------------------------------------------

    def _on_inotify(self) -> None:
        if self._inotify_fd is None:
            return
        try:
            data = os.read(self._inotify_fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            logger.exception("inotify read failed")
            self._close_inotify()
            return
        if not data:
            return
        overflow = False
        names: list[tuple[int, str]] = []
        for mask, name in _parse_inotify(data):
            if mask & (_IN_IGNORED | _IN_DELETE_SELF | _IN_MOVE_SELF):
                self._close_inotify()
                self._rescan()
                return
            if mask & _IN_Q_OVERFLOW:
                overflow = True
                continue
            names.append((mask, name))
        if overflow:
            logger.warning("inotify queue overflow -- full rescan")
            self._rescan()
            return
        for mask, name in names:
            if not name.startswith("event"):
                continue
            path = f"/dev/input/{name}"
            if mask & (_IN_DELETE | _IN_MOVED_FROM):
                self._detach(path)
            else:
                # CREATE can precede udev permissions; ATTRIB retries after
                # they change, and the safety rescan covers missed changes.
                self._try_attach_path(path)

    def _rescan(self) -> None:
        current = set(_list_device_paths())
        for path in current:
            if path not in self._devices:
                self._try_attach_path(path)
        for path in list(self._devices):
            if path not in current:
                self._detach(path)
        self._watch_input()
        self._arm_rescan()

    def _close_inotify(self) -> None:
        if self._inotify_fd is not None:
            if self._loop is not None:
                self._loop.remove_reader(self._inotify_fd)
            os.close(self._inotify_fd)
            self._inotify_fd = None

    def _watch_input(self) -> None:
        if self._inotify_fd is not None or self._loop is None:
            return
        try:
            self._inotify_fd = _open_inotify("/dev/input")
            self._loop.add_reader(self._inotify_fd, self._on_inotify)
            logger.info("inotify watching /dev/input")
        except OSError as exc:
            self._close_inotify()
            logger.warning("inotify unavailable; retrying on safety rescan: %s", exc)

    def _arm_rescan(self) -> None:
        if self._loop is None:
            return
        if self._rescan_handle is not None:
            self._rescan_handle.cancel()
        self._rescan_handle = self._loop.call_later(30.0, self._rescan)

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        devices = self._find_keyboards()
        if devices:
            logger.info("Found %d keyboard(s)", len(devices))
            for device in devices:
                self._attach(device)
        else:
            logger.warning(
                "No keyboards found yet (permission problem or no device). "
                "inotify will keep looking. If you are not in the 'input' "
                "group, run: sudo usermod -aG input $USER"
            )
        self._watch_input()
        self._arm_rescan()

    async def stop(self) -> None:
        if self._rescan_handle is not None:
            self._rescan_handle.cancel()
            self._rescan_handle = None
        self._close_inotify()
        for path in list(self._devices):
            self._detach(path)
        self.classifier.reset()
        self._loop = None
        logger.info(
            "Listener stopped: %d EV_KEY received, %d presses logged, "
            "%d SYN_DROPPED overruns",
            self.received_events,
            self.logged_presses,
            self.dropped_overruns,
        )
