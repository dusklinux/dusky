#!/usr/bin/env python3
import os
import asyncio
import logging
import pyudev
from typing import Set
from evdev import InputDevice, ecodes

# Configure logging to route to stderr for proper journald/uwsm capture
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

SYNC_ID = "sys-osd"
ROUTER_SCRIPT = os.path.expanduser("~/user_scripts/mako_osd/osd_router.sh")

# Retain strong references to prevent mid-execution garbage collection of tasks
_active_tasks: Set[asyncio.Task] = set()

# Track actively monitored device nodes to prevent duplicate tasks from udev spam
_monitored_devices: Set[str] = set()

# Track active debounced actions to prevent subprocess bombs on key-hold
_active_actions: Set[str] = set()


async def _safe_notify(icon: str, title: str) -> None:
    """
    Fire-and-forget notification dispatch. The synchronous D-Bus hint 
    is handled natively by Mako/Dunst, replacing overlapping OSDs immediately.
    """
    process = await asyncio.create_subprocess_exec(
        "notify-send", "-a", "OSD", 
        "-h", f"string:x-canonical-private-synchronous:{SYNC_ID}", 
        "-i", icon, title
    )
    # Await completion in a detached task to prevent blocking the event loop
    wait_task = asyncio.create_task(process.wait())
    _active_tasks.add(wait_task)
    wait_task.add_done_callback(_active_tasks.discard)


async def trigger_router(action: str, step: str = "10") -> None:
    """
    Dispatches the stateless bash router script with active debouncing.
    Drops identical rapid-repeat (EV_KEY value=2) events if a subprocess 
    for this exact action is already executing.
    """
    if action in _active_actions:
        return
    _active_actions.add(action)
    try:
        process = await asyncio.create_subprocess_exec(ROUTER_SCRIPT, action, step)
        await process.wait()
    finally:
        _active_actions.discard(action)


def dispatch_notification(icon: str, title: str) -> None:
    """
    Spawns the notification task and registers a strong reference to prevent 
    the Python event loop from garbage collecting it while pending.
    """
    task = asyncio.create_task(_safe_notify(icon, title))
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)


async def monitor_device(dev_path: str) -> None:
    """
    Monitors a specific evdev device node for LED state changes and ACPI keys.
    Ensures file descriptors are strictly managed and closed.
    """
    # Concurrency Guard: Prevent duplicate monitoring of the same node
    if dev_path in _monitored_devices:
        return
    _monitored_devices.add(dev_path)

    device = None
    try:
        device = InputDevice(dev_path)
        caps = device.capabilities()
        
        # Determine if this device has Lock LEDs or Keyboard Illumination Keys
        has_led = ecodes.EV_LED in caps
        has_kbd_keys = ecodes.EV_KEY in caps and (
            ecodes.KEY_KBDILLUMUP in caps[ecodes.EV_KEY] or 
            ecodes.KEY_KBDILLUMDOWN in caps[ecodes.EV_KEY]
        )
        
        if not (has_led or has_kbd_keys):
            return

        async for event in device.async_read_loop():
            # 1. Handle Stateful Hardware LEDs (Lock Keys)
            if event.type == ecodes.EV_LED:
                if event.code == ecodes.LED_CAPSL:
                    state = "ON" if event.value == 1 else "OFF"
                    dispatch_notification(f"caps-lock-{state.lower()}", f"Caps Lock: {state}")
                elif event.code == ecodes.LED_NUML:
                    state = "ON" if event.value == 1 else "OFF"
                    dispatch_notification(f"num-lock-{state.lower()}", f"Num Lock: {state}")
            
            # 2. Handle ACPI/Hardware Keys that bypass Wayland (Keyboard Backlight)
            # value 1 means KeyDown, value 2 means KeyRepeat (holding the key)
            elif event.type == ecodes.EV_KEY and event.value in (1, 2): 
                if event.code == ecodes.KEY_KBDILLUMUP:
                    task = asyncio.create_task(trigger_router("--kbd-bright-up"))
                    _active_tasks.add(task)
                    task.add_done_callback(_active_tasks.discard)
                elif event.code == ecodes.KEY_KBDILLUMDOWN:
                    task = asyncio.create_task(trigger_router("--kbd-bright-down"))
                    _active_tasks.add(task)
                    task.add_done_callback(_active_tasks.discard)
                    
    except (OSError, PermissionError):
        # Expected behavior: Gracefully handle devices disconnecting or permission drops dynamically
        pass
    except Exception as e:
        # Log the traceback but do not crash the daemon
        logging.error(f"Unexpected failure on device {dev_path}: {e}", exc_info=True)
    finally:
        # Guarantee file descriptor closure and release the concurrency lock
        _monitored_devices.discard(dev_path)
        if device is not None:
            device.close()


async def main() -> None:
    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem='input')
    monitor.start()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[pyudev.Device] = asyncio.Queue()
    
    # Filter spurious epoll wakeups natively before queue ingestion using the walrus operator
    loop.add_reader(
        monitor.fileno(), 
        lambda: (dev := monitor.poll()) is not None and queue.put_nowait(dev)
    )

    # 1. Enumerate and attach to currently connected devices
    for device in context.list_devices(subsystem='input'):
        if device.device_node:
            task = asyncio.create_task(monitor_device(device.device_node))
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)

    # 2. Maintain daemon lifecycle independently to prevent cascading failures
    while True:
        device = await queue.get()
        if device and device.action == 'add' and device.device_node:
            task = asyncio.create_task(monitor_device(device.device_node))
            _active_tasks.add(task)
            task.add_done_callback(_active_tasks.discard)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Catch standard interrupt for clean daemon termination without tracebacks
        pass
