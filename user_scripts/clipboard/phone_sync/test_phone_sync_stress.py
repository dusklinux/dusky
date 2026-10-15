#!/usr/bin/env python3
"""Isolated stress tests using real Unix sockets, subprocess pipes and capture children.

SSH peers and wl-copy/wl-paste are fixture processes. Never touches the real
clipboard, phone, keys, user configuration or systemd service.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import phone_sync as sync
from test_phone_sync import Writer, reader


PEER = r'''
import hashlib, json, pathlib, struct, sys, time, uuid
mode, root = sys.argv[1], pathlib.Path(sys.argv[2])
def send(message):
    body=json.dumps(message,ensure_ascii=False,separators=(',',':')).encode()
    frame=struct.pack('!I',len(body))+body
    for pos in range(0,len(frame),997):
        sys.stdout.buffer.write(frame[pos:pos+997]);sys.stdout.buffer.flush()
def exact(size):
    data=b''
    while len(data)<size:
        part=sys.stdin.buffer.read(size-len(data))
        if not part: raise EOFError
        data+=part
    return data
send({'type':'hello','version':1,'maxBytes':16384})
if mode=='remote':
    for i in range(100):
        identifier=str(uuid.uuid4())
        send({'type':'copy','id':identifier,'text':f'remote-{i}: α 👋\n\n'})
        message=json.loads(exact(struct.unpack('!I',exact(4))[0]))
        assert message=={'type':'ack','id':identifier},message
    (root/'remote-done').touch()
while True:
    try: message=json.loads(exact(struct.unpack('!I',exact(4))[0]))
    except EOFError: break
    assert message['type']=='copy',message
    raw=message['text'].encode()
    with (root/'received').open('a') as out: out.write(f'{len(raw)}:{hashlib.sha256(raw).hexdigest()}\n')
    if mode=='disconnect': sys.exit(0)
    if mode=='hold':
        while not (root/'release').exists(): time.sleep(.001)
    send({'type':'ack','id':message['id']})
'''

COPY = r'''
import pathlib, subprocess, sys, os
root=pathlib.Path(sys.argv[1])
data=sys.stdin.buffer.read()
(root/'clipboard').write_bytes(data)
subprocess.run([sys.executable,sys.argv[2],'--capture',str(root/'dusky-phone-sync.sock')],
               input=data,env={**os.environ,'CLIPBOARD_STATE':'data'},check=True)
'''

PASTE = r'''
import sys, time
mode=sys.argv[1]
if mode=='image': sys.exit(1)
if mode=='hang': time.sleep(60)
if mode=='large':
    try:
        for _ in range(100000): sys.stdout.buffer.write(b'x'*65536)
    except BrokenPipeError: pass
else: sys.stdout.buffer.write('old α\n\n'.encode())
'''


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.001)


class Stress(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, content in (('peer.py', PEER), ('copy.py', COPY), ('paste.py', PASTE)):
            (self.root/name).write_text(content, encoding='utf-8')
        self.environment = patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.sync = sync.Sync({'identity':'/fixture/key','user':'mobile'})
        self.mode = 'normal'
        self.paste_mode = 'text'
        self.processes = []
        launch = asyncio.create_subprocess_exec

        async def fixture_launch(*args, **kwargs):
            if args[0] == 'ssh':
                args = (sys.executable, str(self.root/'peer.py'), self.mode, str(self.root))
            elif args[0] == 'wl-copy':
                args = (sys.executable, str(self.root/'copy.py'), str(self.root), str(Path(sync.__file__).resolve()))
            elif args[0] == 'wl-paste':
                args = (sys.executable, str(self.root/'paste.py'), self.paste_mode)
            process = await launch(*args, **kwargs)
            self.processes.append(process)
            return process

        self.launch_patch = patch.object(sync.asyncio, 'create_subprocess_exec', side_effect=fixture_launch)
        self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        self.server = await asyncio.start_unix_server(self.sync.local, path=self.sync.socket_path)
        self.connection = None

    async def asyncTearDown(self):
        if self.connection is not None and not self.connection.done():
            self.connection.cancel()
            try:
                await self.connection
            except asyncio.CancelledError:
                pass
        self.server.close()
        await self.server.wait_closed()
        for process in self.processes:
            await sync.stop_process(process)
            self.assertIsNotNone(process.returncode, 'Child process was not reaped')

    async def connect(self):
        self.connection = asyncio.create_task(self.sync.connect('fixture', 22))
        await until(lambda: self.sync.writer is not None or self.connection.done())
        self.assertFalse(self.connection.done())

    async def local(self, text):
        _, writer = await asyncio.open_unix_connection(self.sync.socket_path)
        frame = sync.encode({'type':'local','text':text})
        for start in range(0,len(frame),719):
            writer.write(frame[start:start+719])
            await writer.drain()
        writer.close()
        await writer.wait_closed()
        await until(lambda: self.sync.last_text == text)

    def received(self):
        path = self.root/'received'
        return path.read_text().splitlines() if path.exists() else []

    async def test_1000_maximum_copies(self):
        await self.connect()
        expected = []
        for i in range(1000):
            text = f'{i:04d}:'+'α'*8189+'\n' # Exactly 16 KiB, including Unicode/newline.
            self.assertEqual(len(text.encode()),sync.MAX_TEXT)
            expected.append(f'{sync.MAX_TEXT}:{hashlib.sha256(text.encode()).hexdigest()}')
            await self.local(text)
            await until(lambda: len(self.received()) == i+1 and not self.sync.pending and self.sync.copies.empty())
        self.assertEqual(self.received(),expected)

    async def test_30000_copy_burst_is_bounded(self):
        self.mode = 'hold'
        await self.connect()
        await self.local('in-flight')
        await until(lambda: len(self.received()) == 1)
        for i in range(30000):
            await self.sync.local(reader({'type':'local','text':f'burst-{i}'}),Writer())
            self.assertLessEqual(self.sync.copies.qsize(),1)
            self.assertEqual(len(self.sync.pending),1)
            if i % 256 == 0:
                await asyncio.sleep(0)
        (self.root/'release').touch()
        await until(lambda: len(self.received()) == 2 and not self.sync.pending and self.sync.copies.empty())
        self.assertEqual(self.sync.last_text,'burst-29999')
        self.assertEqual(self.received(),[f'{len(text)}:{hashlib.sha256(text.encode()).hexdigest()}' for text in ('in-flight','burst-29999')])

    async def test_100_remote_copies_and_capture_echoes(self):
        self.mode = 'remote'
        await self.connect()
        await until(lambda: (self.root/'remote-done').exists(),timeout=15)
        self.assertEqual((self.root/'clipboard').read_text(),'remote-99: α 👋\n\n')
        self.assertFalse(self.sync.pending)
        self.assertTrue(self.sync.copies.empty())
        self.assertEqual(len(self.processes),101) # Peer plus 100 real wl-copy stand-ins.

    async def test_50_disconnects_clean_pending_state(self):
        self.mode = 'disconnect'
        for i in range(50):
            await self.connect()
            await self.local(f'disconnect-{i}')
            with self.assertRaises(ExceptionGroup):
                await self.connection
            self.assertIsNone(self.sync.writer)
            self.assertFalse(self.sync.pending)
            self.assertTrue(self.sync.copies.empty())
        self.assertTrue(all(process.returncode is not None for process in self.processes))

    async def test_missing_ack_times_out_and_reaps_peer(self):
        self.mode = 'hold'
        await self.connect()
        await self.local('unacknowledged')
        async with asyncio.timeout(18):
            with self.assertRaises(ExceptionGroup) as failure:
                await self.connection
        self.assertTrue(any(isinstance(error,TimeoutError) for error in failure.exception.exceptions))
        self.assertFalse(self.sync.pending)
        self.assertIsNone(self.sync.writer)
        self.assertIsNotNone(self.processes[0].returncode)

    async def test_snapshots_are_bounded_and_reaped(self):
        for mode, expected in (('text','old α\n\n'), ('image',None), ('large',None)):
            self.paste_mode = mode
            self.assertEqual(await sync.read_clipboard(),expected)
            self.assertIsNotNone(self.processes[-1].returncode)
        self.paste_mode = 'hang'
        with self.assertRaises(TimeoutError):
            await sync.read_clipboard()
        self.assertIsNotNone(self.processes[-1].returncode)

    async def test_full_pipes_and_forced_kill_are_drained(self):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
            'import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
            'os.write(1,b"ready"); os.write(1,b"x"*131072); os.write(2,b"y"*131072); time.sleep(60)',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=16385)
        self.assertEqual(await process.stdout.readexactly(5),b'ready')
        await asyncio.sleep(.01)
        async with asyncio.timeout(4):
            await sync.stop_process(process)
        self.assertEqual(process.returncode,-9)
        self.assertTrue(process.stdout.at_eof() and process.stderr.at_eof())


if __name__ == '__main__':
    unittest.main(verbosity=2)
