#!/usr/bin/env python3
"""Watch Linux input LEDs and hardware keyboard-backlight events for the OSD."""

import asyncio
import errno
import logging
import re
import signal
from collections.abc import Coroutine
from contextlib import suppress
from pathlib import Path

import pyudev
from evdev import InputDevice, ecodes

SYNC_ID = "sys-osd"
IGNORE_RAW_CAPSLOCK = False  # Set when Caps Lock is remapped in Hyprland.

_active_tasks: set[asyncio.Task[None]] = set()
_device_tasks: dict[str, asyncio.Task[None]] = {}


def spawn(coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
    task = asyncio.create_task(coro)
    _active_tasks.add(task)
    task.add_done_callback(task_done)
    return task


def task_done(task: asyncio.Task[None]) -> None:
    _active_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logging.error("Background task failed", exc_info=error)


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            async with asyncio.timeout(1):
                await process.wait()
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
    await process.wait()


async def run_command(*args: str | Path, capture: bool = False) -> bytes | None:
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE if capture else None,
        )
        async with asyncio.timeout(5):
            output, _ = await process.communicate()
        if process.returncode:
            logging.warning("Command failed (%s): %s", process.returncode, args[0])
            return None
        return output or b""
    except (OSError, TimeoutError) as error:
        logging.warning("Command %s failed: %s", args[0], error)
        return None
    finally:
        if process is not None:
            await stop_process(process)


class DebouncedNotifier:
    """Coalesce LED synchronization bursts before starting notify-send."""

    def __init__(self) -> None:
        self._pending: tuple[str, str, int | None] | None = None
        self._task: asyncio.Task[None] | None = None

    def dispatch(self, icon: str, title: str, value: int | None = None) -> None:
        self._pending = (icon, title, value)
        if self._task is None or self._task.done():
            self._task = spawn(self._send())

    async def _send(self) -> None:
        while self._pending is not None:
            await asyncio.sleep(0.05)
            icon, title, value = self._pending
            self._pending = None
            # Never cancel an in-flight notification in favor of a newer one.
            args = [
                "notify-send",
                "-a",
                "OSD",
                "-h",
                f"string:x-canonical-private-synchronous:{SYNC_ID}",
                "-i",
                icon,
            ]
            if value is not None:
                args += ["-h", f"int:value:{value}"]
            await run_command(*args, "--", title)


_notifier = DebouncedNotifier()


async def monitor_upower_dbus() -> None:
    # Monitor the service, including per-device objects introduced by UPower 1.91.
    # Use signal values directly: another device's brightness is not our state.
    root = b"/org/freedesktop/UPower/KbdBacklight"
    signal_marker = (
        b": org.freedesktop.UPower.KbdBacklight.BrightnessChangedWithSource "
    )
    while True:
        process = None
        maxima: dict[bytes, int] = {}
        try:
            process = await asyncio.create_subprocess_exec(
                "gdbus",
                "monitor",
                "--system",
                "--dest",
                "org.freedesktop.UPower",
                stdout=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            async for line in process.stdout:
                if not line.startswith(b"/") or b".InterfacesRemoved " in line:
                    maxima.clear()
                path, separator, payload = line.strip().partition(signal_marker)
                if not separator or not (path == root or path.startswith(root + b"/")):
                    continue
                # Software changes already have an OSD from the shell router.
                match = re.fullmatch(rb"\(([0-9]+), 'internal'\)", payload)
                if match is None:
                    continue
                current = int(match[1])
                if path not in maxima:
                    output = await run_command(
                        "gdbus",
                        "call",
                        "--system",
                        "--dest",
                        "org.freedesktop.UPower",
                        "--object-path",
                        path.decode("ascii"),
                        "--method",
                        "org.freedesktop.UPower.KbdBacklight.GetMaxBrightness",
                        capture=True,
                    )
                    maximum = re.fullmatch(rb"\(([0-9]+),\)\s*", output or b"")
                    if maximum is None or int(maximum[1]) == 0:
                        continue
                    maxima[path] = int(maximum[1])
                if current > maxima[path]:
                    continue
                value = (current * 100 + maxima[path] // 2) // maxima[path]
                _notifier.dispatch(
                    "keyboard-brightness", f"Kbd Brightness: {value}%", value
                )
            logging.warning(
                "UPower monitor exited (%s); retrying", await process.wait()
            )
        except OSError as error:
            logging.warning("UPower monitor failed: %s", error)
        finally:
            if process is not None:
                await stop_process(process)
        await asyncio.sleep(2)


async def monitor_device(dev_path: str) -> None:
    device = None
    pending: dict[int, asyncio.Task[None]] = {}
    try:
        device = InputDevice(dev_path)
        supported_leds = device.capabilities().get(ecodes.EV_LED, ())

        async def show_lock(led: int, label: str, icon: str) -> None:
            # Query the kernel state after the compositor processes the key.
            # This also works when an unchanged LED generates no EV_LED event.
            await asyncio.sleep(0.05)
            try:
                active_leds = device.leds()
            except OSError as error:
                if error.errno not in (errno.ENODEV, errno.ENOENT):
                    logging.warning("Cannot read LEDs on %s: %s", dev_path, error)
                return
            state = "ON" if led in active_leds else "OFF"
            _notifier.dispatch(f"{icon}-{state.lower()}", f"{label}: {state}")

        dropped = False
        async for event in device.async_read_loop():
            if event.type == ecodes.EV_SYN:
                if event.code == ecodes.SYN_DROPPED:
                    dropped = True
                    for task in pending.values():
                        task.cancel()
                elif event.code == ecodes.SYN_REPORT:
                    dropped = False
                continue
            if dropped or event.type != ecodes.EV_KEY or event.value != 1:
                continue
            # Hardware controls belong to Hyprland bindings; this daemon only
            # observes lock state and firmware-controlled backlight changes.
            if event.code == ecodes.KEY_CAPSLOCK and not IGNORE_RAW_CAPSLOCK:
                led, label, icon = ecodes.LED_CAPSL, "Caps Lock", "caps-lock"
            elif event.code == ecodes.KEY_NUMLOCK:
                led, label, icon = ecodes.LED_NUML, "Num Lock", "num-lock"
            else:
                continue
            if led not in supported_leds:
                continue
            if (task := pending.get(led)) is not None:
                task.cancel()
            pending[led] = spawn(show_lock(led, label, icon))
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.ENOENT):
            logging.warning("Cannot monitor %s: %s", dev_path, error)
    finally:
        for task in pending.values():
            task.cancel()
        await asyncio.gather(*pending.values(), return_exceptions=True)
        if device is not None:
            device.close()


def watch_device(device: pyudev.Device) -> None:
    node = device.device_node
    if not node or not Path(node).name.startswith("event"):
        return
    # Skip mice and unrelated devices; firmware hotkey devices remain eligible.
    if not any(
        device.properties.get(key) == "1"
        for key in (
            "ID_INPUT_KEY",
            "ID_INPUT_KEYBOARD",
        )
    ):
        return
    task = _device_tasks.get(node)
    if task is not None and not task.done():
        return
    task = spawn(monitor_device(node))
    _device_tasks[node] = task

    def forget(finished: asyncio.Task[None]) -> None:
        if _device_tasks.get(node) is finished:
            del _device_tasks[node]

    task.add_done_callback(forget)


async def main() -> None:
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="input")
    monitor.start()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[pyudev.Device] = asyncio.Queue()
    stopped = asyncio.Event()
    reader_error: OSError | None = None

    def receive_devices() -> None:
        nonlocal reader_error
        try:
            while (device := monitor.poll(timeout=0)) is not None:
                queue.put_nowait(device)
        except OSError as error:
            # A dead reader must fail the service, not spin or leave it deaf.
            loop.remove_reader(monitor.fileno())
            reader_error = error
            stopped.set()

    loop.add_reader(monitor.fileno(), receive_devices)
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)

    async def hotplug() -> None:
        while True:
            device = await queue.get()
            if device.action == "remove":
                task = _device_tasks.get(device.device_node)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            elif device.action in ("add", "change"):
                watch_device(device)

    try:
        async with asyncio.TaskGroup() as group:
            spawn(monitor_upower_dbus())
            hotplug_task = group.create_task(hotplug())
            for device in context.list_devices(subsystem="input"):
                watch_device(device)
            await stopped.wait()
            hotplug_task.cancel()
            if reader_error is not None:
                raise reader_error
    finally:
        loop.remove_reader(monitor.fileno())
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
        tasks = list(_active_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    asyncio.run(main())
