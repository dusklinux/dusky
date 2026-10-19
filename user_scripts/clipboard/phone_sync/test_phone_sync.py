#!/usr/bin/env python3
"""Isolated tests: no live clipboard, SSH, history or service changes."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import phone_sync as sync
import setup


class Writer:
    def __init__(self):
        self.data = bytearray()
    def write(self, data):
        self.data.extend(data)
    async def drain(self):
        pass
    def close(self):
        pass
    async def wait_closed(self):
        pass


def reader(*messages):
    stream = asyncio.StreamReader()
    for message in messages:
        stream.feed_data(sync.encode(message))
    stream.feed_eof()
    return stream


class Framing(unittest.IsolatedAsyncioTestCase):
    async def test_startup_snapshot(self):
        for data, status, expected in ((b"old\n\n", 0, "old\n\n"), (b"", 1, None),
                                       (b"\xff", 0, None), (b"x" * (sync.MAX_TEXT + 1), 0, None)):
            with self.subTest(status=status, size=len(data)):
                process = AsyncMock(stdout=reader_from_bytes(data), stderr=None, returncode=0)
                process.wait.return_value = status
                with patch.object(sync.asyncio, "create_subprocess_exec", return_value=process):
                    self.assertEqual(await sync.read_clipboard(), expected)

    async def test_unicode_newlines_fragmented(self):
        message = {"type": "copy", "id": str(uuid.uuid4()), "text": "α 👋\n\n"}
        stream = asyncio.StreamReader()
        async def feed():
            for byte in sync.encode(message):
                stream.feed_data(bytes([byte]))
                await asyncio.sleep(0)
        async with asyncio.TaskGroup() as group:
            group.create_task(feed())
            self.assertEqual(await sync.read_frame(stream), message)

    async def test_reject_frames(self):
        for size in (0, sync.MAX_FRAME + 1):
            stream = asyncio.StreamReader()
            stream.feed_data(size.to_bytes(4, "big"))
            with self.assertRaises(ValueError):
                await sync.read_frame(stream)
        stream = asyncio.StreamReader()
        stream.feed_data(b"\0\0\0\x02{}"[:5])
        stream.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await sync.read_frame(stream)

    def test_bounds(self):
        self.assertTrue(sync.valid_text("a" * 16384))
        self.assertFalse(sync.valid_text("a" * 16385))
        self.assertTrue(sync.valid_text("👋" * 4096))
        for text in ("👋" * 4097, "", None, 42, "\ud800"):
            self.assertFalse(sync.valid_text(text))


class Behavior(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, XDG_RUNTIME_DIR=self.temp.name)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sync = sync.Sync({})

    async def local(self, text):
        await self.sync.local(reader({"type": "local", "text": text}), Writer())

    async def test_baseline_offline_echo_latest(self):
        await self.local("old clipboard")
        self.assertTrue(self.sync.copies.empty())
        await self.local("offline copy")
        self.assertTrue(self.sync.copies.empty())
        self.sync.writer = Writer()
        await self.local("offline copy")
        self.assertTrue(self.sync.copies.empty())
        await self.local("one")
        await self.local("two")
        self.assertEqual(self.sync.copies.get_nowait(), "two")
        await self.local("")
        await self.local("two")
        self.assertEqual(self.sync.copies.get_nowait(), "two")

    async def test_connection_before_initial_selection(self):
        self.sync.last_text = "existing clipboard"  # Startup snapshot precedes watching/SSH.
        self.sync.config = {"identity": "/fixture/key", "user": "mobile"}
        stream = asyncio.StreamReader()
        stream.feed_data(sync.encode({"type": "hello", "version": 1, "maxBytes": sync.MAX_TEXT}))
        process = AsyncMock(stdin=Writer(), stdout=stream, stderr=None, returncode=0)
        with patch.object(sync.asyncio, "create_subprocess_exec", return_value=process) as launch:
            task = asyncio.create_task(self.sync.connect("fixture", 22))
            try:
                async with asyncio.timeout(1):
                    while self.sync.writer is None:
                        await asyncio.sleep(0)
                await self.local("existing clipboard")
                self.assertTrue(self.sync.copies.empty(), "Initial selection was queued as a new copy")
                self.assertFalse(process.stdin.data)
                self.assertIsNone(launch.call_args.kwargs["stderr"], "SSH diagnostics were hidden")
                await self.local("fresh copy")
                await asyncio.sleep(0)
                message = await sync.read_frame(reader_from_bytes(process.stdin.data))
                self.assertEqual(message["text"], "fresh copy")
            finally:
                task.cancel()
                stream.feed_eof()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_first_text_after_initial_image(self):
        # wl-paste --type text emits no initial callback for image-only data.
        self.sync.writer = Writer()
        await self.local("first text after image")
        self.assertFalse(self.sync.copies.empty(), "First real copy was discarded as the baseline")
        self.assertEqual(self.sync.copies.get_nowait(), "first text after image")

    async def test_reconnect_fallback_and_bounded_backoff(self):
        self.sync.config = {"host": "fixture-wifi", "port": 22, "usb_port": 2222}
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 7:
                raise asyncio.CancelledError

        with patch.object(self.sync, "connect", side_effect=OSError) as connect, \
             patch.object(sync.asyncio, "sleep", side_effect=sleep), \
             self.assertLogs(level="WARNING") as logs:
            with self.assertRaises(asyncio.CancelledError):
                await self.sync.reconnect()
        self.assertIn("fixture-wifi:22", logs.output[0])
        self.assertIn("127.0.0.1:2222", logs.output[1])
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30, 30])
        self.assertEqual([call.args for call in connect.call_args_list],
                         [("fixture-wifi", 22), ("127.0.0.1", 2222)] * 7)

    async def test_remote_copy_and_echo(self):
        self.sync.writer = Writer()
        process = AsyncMock(stdout=None, stderr=None)
        process.returncode = 0
        with patch.object(sync.asyncio, "create_subprocess_exec", return_value=process) as launch:
            identifier = str(uuid.uuid4())
            with self.assertRaises(asyncio.IncompleteReadError):
                await self.sync.receive_copies(reader({"type": "copy", "id": identifier, "text": "hi\n\n"}))
            process.communicate.assert_awaited_once_with(b"hi\n\n")
            self.assertEqual(launch.call_args.args, ("wl-copy", "--type", "text/plain;charset=utf-8"))
        await self.local("hi\n\n")
        self.assertTrue(self.sync.copies.empty())
        self.assertEqual(await sync.read_frame(reader_from_bytes(self.sync.writer.data)), {"type": "ack", "id": identifier})

    async def test_copy_failure_restores_state(self):
        self.sync.writer = Writer()
        self.sync.last_text = "previous"
        process = AsyncMock(stdout=None, stderr=None)
        process.returncode = 1
        with patch.object(sync.asyncio, "create_subprocess_exec", return_value=process):
            with self.assertRaises(OSError):
                await self.sync.receive_copies(reader({"type": "copy", "id": str(uuid.uuid4()), "text": "new"}))
        self.assertEqual(self.sync.last_text, "previous")
        self.assertFalse(self.sync.writer.data)

    async def test_acknowledgement(self):
        self.sync.writer = Writer()
        task = asyncio.create_task(self.sync.send_copies())
        try:
            await self.local("new")
            await asyncio.sleep(0)
            message = await sync.read_frame(reader_from_bytes(self.sync.writer.data))
            self.assertEqual(message["text"], "new")
            with self.assertRaises(asyncio.IncompleteReadError):
                await self.sync.receive_copies(reader({"type": "ack", "id": message["id"]}))
            await asyncio.sleep(0)
            self.assertFalse(self.sync.pending)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_connection_probe_does_not_touch_clipboard(self):
        self.sync.writer = Writer()
        self.sync.last_text = "unchanged"
        identifier = str(uuid.uuid4())
        with patch.object(sync.asyncio, "create_subprocess_exec") as launch:
            with self.assertRaises(asyncio.IncompleteReadError):
                await self.sync.receive_copies(reader({"type": "ping", "id": identifier}))
            launch.assert_not_called()
        self.assertEqual(self.sync.last_text, "unchanged")
        self.assertEqual(await sync.read_frame(reader_from_bytes(self.sync.writer.data)),
                         {"type": "pong", "id": identifier})

    async def test_5000_connection_probes(self):
        self.sync.writer = Writer()
        self.sync.last_text = "unchanged"
        identifiers = [str(uuid.uuid4()) for _ in range(5000)]
        with patch.object(sync.asyncio, "create_subprocess_exec") as launch:
            with self.assertRaises(asyncio.IncompleteReadError):
                await self.sync.receive_copies(reader(*({"type": "ping", "id": key} for key in identifiers)))
            launch.assert_not_called()
        replies = reader_from_bytes(self.sync.writer.data)
        for key in identifiers:
            self.assertEqual(await sync.read_frame(replies), {"type": "pong", "id": key})
        self.assertTrue(replies.at_eof())
        self.assertEqual(self.sync.last_text, "unchanged")
        self.assertFalse(self.sync.pending)
        self.assertTrue(self.sync.copies.empty())


def reader_from_bytes(data):
    stream = asyncio.StreamReader()
    stream.feed_data(data)
    stream.feed_eof()
    return stream


class Install(unittest.TestCase):
    def test_independent_service(self):
        service = setup.unit(Path('/tmp/a space/phone%config.json'))
        self.assertIn('phone_sync.py', service)
        self.assertIn('phone%%config.json', service)
        self.assertNotIn('dusky_clipboard.service', service)
        self.assertNotIn('cliphist', service)
        command = sync.ssh_command({"identity": "/a key", "user": "mobile"}, "example", 2222, "--stream")
        self.assertEqual(command[-3:], ["mobile@example", "/var/jb/usr/libexec/duskie-clipboard", "--stream"])
        self.assertIn("BatchMode=yes", command)

    def test_install_and_offline_uninstall_are_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "key"
            identity.write_text("fixture", encoding="utf-8")
            configuration = root / "config/dusky/phone_sync.json"
            service = root / "config/systemd/user/dusky_phone_sync.service"
            existing = root / "config/systemd/user/dusky_clipboard.service"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing service remains identical\n")
            with patch.object(setup, "CONFIG", configuration), patch.object(setup, "SERVICE", service), \
                 patch.object(setup, "USB_SERVICE", service.with_name(setup.USB_UNIT)), \
                 patch.object(setup, "INSTALL_ROOT", root / "data/phone_sync"), \
                 patch.object(setup.subprocess, "run") as command, patch.object(setup.shutil, "which", return_value="/usr/bin/tool"):
                command.return_value.returncode = 0
                with patch.object(setup.sys, "argv", ["setup.py", "install", "--host", "phone", "--identity", str(identity)]):
                    setup.main()
                self.assertEqual(json.loads(configuration.read_text())["host"], "phone")
                self.assertEqual(configuration.stat().st_mode & 0o777, 0o600)
                self.assertTrue(service.is_file())
                self.assertEqual((setup.INSTALL_ROOT / "phone_sync.py").read_bytes(),
                                 (setup.ROOT / "phone_sync.py").read_bytes())
                self.assertIn(str(setup.INSTALL_ROOT), service.read_text())
                calls = [call.args[0] for call in command.call_args_list]
                self.assertEqual(calls[0][-1], "--enable")
                self.assertTrue(all("dusky_clipboard.service" not in call for call in calls))
                def offline(args, **kwargs):
                    if args[0] == "ssh":
                        raise setup.subprocess.TimeoutExpired(args, 10)
                    return setup.subprocess.CompletedProcess(args, 0)
                command.side_effect = offline
                with patch.object(setup.sys, "argv", ["setup.py", "uninstall"]):
                    setup.main()
                self.assertFalse(service.exists())
                self.assertFalse(configuration.exists())
                self.assertEqual(existing.read_bytes(), b"existing service remains identical\n")

    def test_managed_usb_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tunnel = root / "systemd/user" / setup.USB_UNIT
            with patch.object(setup, "USB_SERVICE", tunnel), \
                 patch.object(setup, "SERVICE", root / "sync.service"), \
                 patch.object(setup, "CONFIG", root / "missing.json"), \
                 patch.object(setup.shutil, "which", return_value="/usr/bin/iproxy"), \
                 patch.object(setup.subprocess, "run") as command:
                with patch.object(setup.sys, "argv", ["setup.py", "usb", "--usb-port", "2222"]):
                    setup.main()
                self.assertIn('"/usr/bin/iproxy" -s 127.0.0.1 2222:22', tunnel.read_text())
                self.assertIn("Restart=on-failure", tunnel.read_text())
                self.assertIn(["systemctl", "--user", "enable", "--now", setup.USB_UNIT],
                              [call.args[0] for call in command.call_args_list])
                with patch.object(setup.sys, "argv", ["setup.py", "uninstall"]):
                    setup.main()
                self.assertFalse(tunnel.exists())

    def test_phone_failure_does_not_install_linux_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "key"
            identity.touch()
            with patch.object(setup, "CONFIG", root / "config.json"), patch.object(setup, "SERVICE", root / "service"), \
                 patch.object(setup.shutil, "which", return_value="/usr/bin/tool"), \
                 patch.object(setup.subprocess, "run", side_effect=setup.subprocess.CalledProcessError(1, "ssh")), \
                 patch.object(setup.sys, "argv", ["setup.py", "--host", "phone", "--identity", str(identity)]):
                with self.assertRaises(setup.subprocess.CalledProcessError):
                    setup.main()
                self.assertFalse((root / "service").exists())
                self.assertFalse((root / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
