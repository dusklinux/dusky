"""Opt-in test using only a private, exclusively grabbed virtual keyboard."""

import asyncio
import os
import time
import uuid

import pytest

from dusky_keylogger import keycodes as kc
from dusky_keylogger import listener as lm


@pytest.mark.skipif(os.environ.get("DUSKY_TEST_UINPUT") != "1",
                    reason="set DUSKY_TEST_UINPUT=1 for virtual keyboard integration")
def test_virtual_keyboard_mask_capture_and_hotplug(monkeypatch):
    from evdev import InputDevice, UInput, ecodes
    name = f"Dusky audit synthetic {uuid.uuid4()}"
    monkeypatch.setenv("DUSKY_DEVICE_FILTER", name)
    devices = []
    monkeypatch.setattr(lm, "_list_device_paths", lambda: [d.device.path for d in devices])
    listener = lm.KeyListener()
    original = InputDevice
    def open_device(path, **kwargs):
        device = original(path, **kwargs)
        if device.name == name:
            device.grab()  # Keep test events away from the compositor/apps.
        return device
    listener._InputDevice = open_device
    presses = []
    listener.on_key = presses.append
    event_types = set()
    dispatch = listener._dispatch
    def observed_dispatch(live, event):
        event_types.add(event.type)
        dispatch(live, event)
    listener._dispatch = observed_dispatch
    async def wait_for(predicate):
        async with asyncio.timeout(4):
            while not predicate():
                await asyncio.sleep(0.01)
    async def run():
        await listener.start()
        try:
            ui = UInput({ecodes.EV_KEY: [kc.KEY_A, kc.KEY_LEFTSHIFT, kc.KEY_B],
                         ecodes.EV_REL: [ecodes.REL_X]}, name=name)
            devices.append(ui)
            await wait_for(lambda: ui.device.path in listener._devices)
            # More than evdev's native batch size verifies backlog draining.
            for index in range(150):
                ui.write(ecodes.EV_REL, ecodes.REL_X, 1)
                ui.write(ecodes.EV_KEY, kc.KEY_A, 1)
                ui.syn()
                ui.write(ecodes.EV_KEY, kc.KEY_A, 0)
                ui.syn()
                if index % 20 == 19:
                    await asyncio.sleep(0.01)
            await wait_for(lambda: len(presses) == 150)
            assert all(p.char == "a" for p in presses)
            assert all(abs(time.time_ns() // 1000 - p.ts_us) < 5_000_000 for p in presses)
            await wait_for(lambda: listener.received_events == 300)
            assert kc.EV_REL not in event_types
            assert listener.dropped_overruns == 0
            ui.write(ecodes.EV_KEY, kc.KEY_LEFTSHIFT, 1)
            ui.write(ecodes.EV_KEY, kc.KEY_B, 1)
            ui.syn()
            await wait_for(lambda: len(presses) == 152)
            assert presses[-1].char == "B"
            # Deliberately overrun the kernel queue while this loop is blocked.
            ui.write(ecodes.EV_KEY, kc.KEY_B, 0)
            ui.write(ecodes.EV_KEY, kc.KEY_LEFTSHIFT, 0)
            ui.syn()
            for _ in range(1000):
                ui.write(ecodes.EV_KEY, kc.KEY_A, 1)
                ui.syn()
                ui.write(ecodes.EV_KEY, kc.KEY_A, 0)
                ui.syn()
            await wait_for(lambda: listener.dropped_overruns > 0)
            await asyncio.sleep(0.05)
            previous = len(presses)
            ui.write(ecodes.EV_KEY, kc.KEY_B, 1)
            ui.syn()
            await wait_for(lambda: len(presses) == previous + 1)
            assert presses[-1].char == "b"
            path = ui.device.path
            devices.remove(ui)
            ui.close()
            await wait_for(lambda: path not in listener._devices)
            assert path not in listener.classifier._devices
        finally:
            for ui in devices:
                ui.close()
            await listener.stop()
    asyncio.run(run())


@pytest.mark.skipif(os.environ.get("DUSKY_TEST_UINPUT") != "1",
                    reason="set DUSKY_TEST_UINPUT=1 for virtual keyboard integration")
def test_live_daemon_sqlite_notify_and_sigterm(tmp_path, monkeypatch):
    import signal
    import socket
    from evdev import InputDevice, UInput, ecodes
    from dusky_keylogger import daemon as dm
    name = f"Dusky audit daemon {uuid.uuid4()}"
    monkeypatch.setenv("DUSKY_DEVICE_FILTER", name)
    notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    socket_path = tmp_path / "notify.sock"
    notify.bind(str(socket_path))
    notify.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", str(socket_path))
    monkeypatch.setenv("WATCHDOG_USEC", "1000000")
    monkeypatch.setattr(dm, "_setup_logging", lambda *_: None)
    with UInput({ecodes.EV_KEY: [kc.KEY_A]}, name=name) as ui:
        monkeypatch.setattr(lm, "_list_device_paths", lambda: [ui.device.path])
        class PrivateListener(lm.KeyListener):
            def __init__(self):
                super().__init__()
                def open_device(path, **kwargs):
                    device = InputDevice(path, **kwargs)
                    device.grab()
                    return device
                self._InputDevice = open_device
        monkeypatch.setattr(dm, "KeyListener", PrivateListener)
        daemon = dm.Daemon(tmp_path / "data", dict(dm.DEFAULT_CONFIG, flush_interval=0.05))
        async def run():
            task = asyncio.create_task(daemon.run())
            try:
                async with asyncio.timeout(4):
                    while daemon._listener is None or ui.device.path not in daemon._listener._devices:
                        await asyncio.sleep(0.01)
                for index in range(30):
                    ui.write(ecodes.EV_KEY, kc.KEY_A, 1)
                    ui.syn()
                    ui.write(ecodes.EV_KEY, kc.KEY_A, 0)
                    ui.syn()
                    if index % 10 == 9:
                        await asyncio.sleep(0.01)
                async with asyncio.timeout(4):
                    while daemon._writer.written != 30:
                        await asyncio.sleep(0.01)
                signal.raise_signal(signal.SIGTERM)
                await asyncio.wait_for(task, 4)
            finally:
                if not task.done():
                    daemon.stop_sync()
                    await task
        try:
            asyncio.run(run())
            assert daemon._store.total() == 30
            assert not daemon._writer.is_alive
            messages = []
            while True:
                try:
                    messages.append(notify.recv(4096).decode())
                except BlockingIOError:
                    break
            assert any("READY=1" in m for m in messages)
            assert any("WATCHDOG=1" in m for m in messages)
            assert any("STOPPING=1" in m for m in messages)
        finally:
            notify.close()
