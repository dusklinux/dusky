"""Regression tests for clipboard-only capture, resource ownership and recovery."""
import contextlib
import importlib.util
import io
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

installer = load('stt_installer', 'dusky_installer.py')
trigger = load('stt_trigger', 'dusky_trigger.py')
main = load('stt_main', 'dusky_main.py')
worker = load('stt_worker', 'dusky_worker.py')
import dusky_hardware as hardware

class HardwareTests(unittest.TestCase):
    def test_supported_cards_sorted_and_old_cards_excluded(self):
        data='0, GPU-old, Old, 579.99, 8192, 8.6\n1, GPU-ok, Supported, 615.71, 4096, 8.6\n2, GPU-big, Larger, 615.71, 8192, 8.9\n3, GPU-pascal, Pascal, 615.71, 4096, 6.1\n'
        with patch.object(hardware.shutil,'which',return_value='/smi'),patch.object(hardware.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=data)):
            cards=hardware.detect_nvidia()
        self.assertEqual([card['uuid'] for card in cards],['GPU-big','GPU-ok'])
        self.assertEqual(hardware.select_gpu(cards,1)['uuid'],'GPU-ok')
        self.assertIsNone(hardware.select_gpu(cards,99))

    def test_missing_or_broken_driver_returns_cpu(self):
        with patch.object(hardware.shutil,'which',return_value=None):self.assertEqual(hardware.detect_nvidia(),[])
        with patch.object(hardware.shutil,'which',return_value='/smi'),patch.object(hardware.subprocess,'run',side_effect=subprocess.TimeoutExpired('smi',15)):
            self.assertEqual(hardware.detect_nvidia(),[])

class InstallerTests(unittest.TestCase):
    def test_auto_with_cached_wheel_and_no_gpu_does_not_install_cuda(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            with patch.object(installer,'APP_DIR',root/'app'),patch.object(installer,'BIN_DIR',root/'bin'),patch.object(installer,'UNIT_DIR',root/'units'),patch.object(installer,'DEFAULT_STATE_DIR',root/'state'),patch.object(installer,'LOG_FILE',None),patch.object(installer,'assert_runtime'),patch.object(installer,'detect_nvidia',return_value=[]),patch.object(installer,'find_gpu_wheel',return_value=root/'cached.whl'),patch.object(installer,'install_pacman_packages') as packages,patch.object(installer,'install_python_environment',side_effect=RuntimeError('selection complete')),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(RuntimeError,'selection complete'):
                    installer.main(['--yes','--backend','auto','--offline','--no-systemd'])
                packages.assert_called_once_with(False)

    def test_backend_choices_respect_explicit_and_unattended_preferences(self):
        args=installer.parse_arguments(['--yes'])
        self.assertEqual(installer.choose_backend(args,{},[]),'auto')
        self.assertEqual(installer.choose_backend(args,{'backend':'cpu'},[]),'cpu')
        args=installer.parse_arguments(['--backend','nvidia'])
        self.assertEqual(installer.choose_backend(args,{'backend':'cpu'},[]),'nvidia')

    def test_selftest_requires_real_success(self):
        for data in ('', 'invalid', '[]', '{"ok":false}', '{"ok":1}'):
            with self.subTest(data=data), patch.object(installer, 'run', return_value=SimpleNamespace(stdout=data)):
                with self.assertRaises(installer.InstallError):
                    installer.verify_worker(Path('/python'), Path('/app'), Path('/config'))

    def test_rollback_restores_application_and_launchers(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);app=root/'app';old=root/'backup';app.mkdir();old.mkdir()
            (app/'version').write_text('new');(old/'version').write_text('old')
            entry=root/'trigger';entry.write_text('new')
            with patch.object(installer,'APP_DIR',app):
                installer.rollback(old,{entry:(b'old trigger',0o755)},manage_service=False,was_active=False,was_enabled=False)
            self.assertEqual((app/'version').read_text(),'old')
            self.assertEqual(entry.read_text(),'old trigger')

    def test_failed_deployment_restores_running_service(self):
        with tempfile.TemporaryDirectory() as td:
            app=Path(td)/'app';app.mkdir();(app/'version').write_text('old')
            with patch.object(installer,'APP_DIR',app),patch.object(installer.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as commands:
                with self.assertRaises(installer.InstallError):
                    installer.deploy_stage(Path(td)/'missing-stage',was_active=True)
            self.assertEqual((app/'version').read_text(),'old')
            self.assertIn(['systemctl','--user','start',installer.UNIT_NAME],[c.args[0] for c in commands.call_args_list])

    def test_custom_paths_are_quoted(self):
        with tempfile.TemporaryDirectory(prefix='app space ') as td:
            root=Path(td);app=root/'app';app.mkdir()
            (app/'config.json').write_text(json.dumps({'state_dir':str(root/'state')}))
            with patch.object(installer,'APP_DIR',app),patch.object(installer,'BIN_DIR',root/'bin'),patch.object(installer,'UNIT_DIR',root/'units'),patch.object(installer,'run'):
                installer.install_entrypoints(manage_service=False)
            self.assertFalse((root/'units'/installer.UNIT_NAME).exists())
            self.assertIn('export DUSKY_APP_DIR=',(root/'bin/dusky_trigger').read_text())

class RecordingTests(unittest.TestCase):
    def session(self, root, **cfg):
        daemon=SimpleNamespace(config={'state_dir':str(root),'notifications':False,**cfg},
            worker=SimpleNamespace(submit_fd=Mock(return_value='job'),wait_result=Mock(return_value={'ok':True,'text':'Test words.'})),
            _lock=threading.RLock(),state='recording')
        return main.RecordingSession(daemon)

    def capture_fixture(self, sess, spool, read):
        read_fd,write_fd=os.pipe();os.write(write_fd,b'x')
        output=os.fdopen(read_fd,'rb');proc=Mock(stdout=output)
        proc.poll.return_value=0;proc.wait.return_value=0
        try:
            with patch.object(main.subprocess,'Popen',return_value=proc) as launch,patch.object(main.os,'read',side_effect=read):
                sess._capture_loop(spool)
            self.assertEqual(launch.call_args.args[0][0],'pw-record')
            proc.terminate.assert_called_once()
        finally:
            os.close(write_fd)
            if not output.closed:output.close()

    def test_capture_is_lossless_and_does_not_recognize(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryFile() as spool:
            sess=self.session(td)
            reference=b''.join(main.np.full(1024,i,dtype='<i2').tobytes() for i in range(100))
            frames=[reference[i:i+777] for i in range(0,len(reference),777)]
            def read(*_):
                if frames:
                    data=frames.pop(0)
                    if not frames:sess.stop_event.set()
                    return data
                return b''
            self.capture_fixture(sess,spool,read)
            spool.seek(0);self.assertEqual(spool.read(),reference)
            self.assertEqual(sess.samples,len(reference)//2)
            sess.daemon.worker.submit_fd.assert_not_called()

    def test_paused_audio_is_excluded_and_stream_keeps_draining(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryFile() as spool:
            sess=self.session(td);calls=0
            def read(*_):
                nonlocal calls
                calls+=1
                if calls==2:sess.paused.set()
                if calls==3:sess.paused.clear();sess.stop_event.set()
                return bytes([calls,0])*1024 if calls<=3 else b''
            self.capture_fixture(sess,spool,read)
            spool.seek(0);self.assertEqual(spool.read(),bytes([1,0])*1024+bytes([3,0])*1024)

    def test_pipewire_error_is_reported_and_pipe_closed(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryFile() as spool:
            sess=self.session(td)
            read_fd,write_fd=os.pipe();os.close(write_fd)
            output=os.fdopen(read_fd,'rb');proc=Mock(stdout=output)
            proc.poll.return_value=1;proc.wait.return_value=1
            with patch.object(main.subprocess,'Popen',return_value=proc):
                with self.assertRaisesRegex(RuntimeError,'capture ended unexpectedly'):
                    sess._capture_loop(spool)
            self.assertTrue(output.closed)
            self.assertFalse(sess.ready.is_set())

    def test_old_typing_settings_cannot_enable_typing(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td,output_mode='realtime-both',push_type_at_end=True)
            with patch.object(main.subprocess,'run') as commands:sess._publish('Saved words.')
            self.assertEqual(commands.call_count,1)
            self.assertIn('wl-copy',commands.call_args.args[0])
            self.assertNotIn('wtype',commands.call_args.args[0])
            self.assertTrue(Path(sess.transcript_path).exists())

    def test_silence_preserves_clipboard(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td)
            with patch.object(main.subprocess,'run') as commands:sess._publish('')
            commands.assert_not_called()
            self.assertTrue(Path(sess.transcript_path).exists())

    def test_clipboard_failure_is_reported_with_saved_text(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td)
            with patch.object(main.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['wl-copy'])):
                sess._publish('Retained text.')
            self.assertTrue(sess.errors)
            self.assertEqual(Path(sess.transcript_path).read_text(),'Retained text.\n')

    def test_processing_starts_after_capture_finishes(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td);steps=[]
            def capture(spool):
                self.assertFalse(sess.daemon.worker.submit_fd.called)
                spool.write(bytes(32000));sess.samples=16000;steps.append('captured');sess.stop_event.set()
            def recognize(spool,cancel):steps.append('processed');return 'Final words.'
            with patch.object(sess,'_capture_loop',side_effect=capture),patch.object(sess,'_recognize',side_effect=recognize),patch.object(sess,'_publish',return_value='Final words.'),patch.object(main,'_kill_indicator') as close_indicator:
                sess.run()
            close_indicator.assert_not_called()
            self.assertEqual(steps,['captured','processed'])
            self.assertEqual(sess.daemon.state,'finalizing')

    def test_capture_error_preserves_audio_and_marks_partial(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td)
            def capture(spool):
                spool.write(bytes(32000));sess.samples=16000;raise RuntimeError('device disconnected')
            with patch.object(sess,'_capture_loop',side_effect=capture),patch.object(sess,'_publish',return_value='Partial words.') as publish:
                with self.assertRaisesRegex(RuntimeError,'device disconnected'):sess.run()
            self.assertFalse(publish.call_args.kwargs['complete'])
            self.assertEqual(publish.call_args.args[0],'Test words.')

    def test_retry_after_worker_failure_reuses_spool(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryFile() as spool:
            sess=self.session(td);sess.samples=16000;spool.write(bytes(32000))
            sess.daemon.worker.wait_result.side_effect=[{'ok':False,'error':'worker exited'},{'ok':True,'text':'Recovered.'}]
            self.assertEqual(sess._recognize(spool,threading.Event()),'Recovered.')
            self.assertEqual(sess.daemon.worker.submit_fd.call_count,2)

    def test_finalization_can_be_cancelled(self):
        with tempfile.TemporaryDirectory() as td,tempfile.TemporaryFile() as spool:
            sess=self.session(td);sess.samples=16000;sess.cancel_event.set()
            with self.assertRaisesRegex(RuntimeError,'cancelled'):sess._recognize(spool,sess.cancel_event)
            sess.daemon.worker.submit_fd.assert_not_called()

    def test_disk_spool_is_removed_on_error(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session(td)
            with patch.object(sess,'_capture_loop',side_effect=RuntimeError('failed')),patch.object(sess,'_publish',return_value=''):
                with self.assertRaises(RuntimeError):sess.run()
            self.assertEqual(list(Path(td).iterdir()),[])

class RuntimeTests(unittest.TestCase):
    def test_pill_launch_does_not_wait_for_microphone_initialization(self):
        with tempfile.TemporaryDirectory() as td:
            daemon=object.__new__(main.DuskyDaemon)
            daemon.config={'state_dir':td,'notifications':False}
            daemon._lock=threading.RLock();daemon._stop=threading.Event()
            daemon.worker=SimpleNamespace(stop=Mock())
            sess=main.RecordingSession(daemon);daemon._session=sess
            preloaded=threading.Event()
            def record():
                self.assertFalse(sess.ready.is_set())
                show.assert_called_once_with(sess)
                self.assertFalse(preloaded.is_set())
                sess.ready.set()
                self.assertTrue(preloaded.wait(2))
                sess.stop_event.set()
            with patch.object(sess,'run',side_effect=record),patch.object(daemon,'_spawn_indicator',return_value=None) as show,patch.object(daemon,'_prewarm_worker',side_effect=preloaded.set):
                daemon._run_session(sess,False,None)
            self.assertTrue(daemon._stop.is_set())

    def test_shutdown_acknowledges_before_process_exit(self):
        daemon=object.__new__(main.DuskyDaemon)
        daemon._lock=threading.RLock();daemon._stop=threading.Event();daemon.state='idle'
        conn=Mock();conn.__enter__=Mock(return_value=conn);conn.__exit__=Mock(return_value=False)
        conn.getsockopt.return_value=struct.pack('3i',os.getpid(),os.getuid(),os.getgid())
        conn.recvmsg.return_value=(b'{"command":"shutdown"}',[],0,None)
        def acknowledge(data):
            self.assertFalse(daemon._stop.is_set())
            self.assertTrue(json.loads(data[0])['ok'])
        conn.sendmsg.side_effect=acknowledge
        daemon._handle_conn(conn)
        self.assertTrue(daemon._stop.is_set())
        conn.sendmsg.assert_called_once()

    def test_second_recorder_cannot_replace_live_socket(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);config=root/'config.json'
            config.write_text(json.dumps({'schema_version':3,'backend':'cpu'}))
            with patch.dict(os.environ,XDG_RUNTIME_DIR=td):
                daemon=main.DuskyDaemon(config)
                inode=daemon.control_path.stat().st_ino
                try:
                    with self.assertRaisesRegex(RuntimeError,'already running'):
                        main.DuskyDaemon(config)
                    self.assertEqual(daemon.control_path.stat().st_ino,inode)
                finally:
                    daemon._listener.close();daemon._instance_lock.close()

    def test_gpu_probe_is_deferred_and_does_not_block_status(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'.venv-gpu/bin').mkdir(parents=True)
            (root/'.venv-gpu/bin/python').touch()
            entered=threading.Event();release=threading.Event()
            def detect():
                entered.set();release.wait(5)
                return [{'index':0,'uuid':'GPU-test'}]
            with patch.object(main,'APP_DIR',root),patch.object(hardware,'detect_nvidia',side_effect=detect) as probe:
                manager=main.WorkerManager({'backend':'auto','parakeet':{'model':'test'}})
                probe.assert_not_called()
                with patch.object(manager,'_spawn_locked'):
                    thread=threading.Thread(target=manager.prewarm);thread.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        self.assertIsNone(manager.pid)
                    finally:
                        release.set();thread.join(5)
                self.assertEqual(manager.gpu['uuid'],'GPU-test')

    def test_daemon_worker_survives_recording_idle_time(self):
        manager=main.WorkerManager({})
        proc=Mock();proc.pid=123;proc.poll.return_value=None
        with patch.object(main.subprocess,'Popen',return_value=proc) as launch,patch.object(main.threading,'Thread'):
            manager.prewarm()
        self.assertEqual(launch.call_args.kwargs['env'][main.NO_IDLE_EXIT_ENV],'1')
        manager._sock.close()

    def test_parakeet_clamps_old_arena_settings_and_primes_full_block(self):
        class Session:
            def get_providers(self):return ['CUDAExecutionProvider','CPUExecutionProvider']
        options=SimpleNamespace(add_session_config_entry=Mock())
        ort=SimpleNamespace(SessionOptions=lambda:options,InferenceSession=Session,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'),
            GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL='all'),
            get_available_providers=lambda:['CUDAExecutionProvider'])
        model=SimpleNamespace(session=Session(),recognize=Mock(return_value=''))
        asr=SimpleNamespace(load_model=Mock(return_value=model))
        with patch.object(worker,'preload_cuda13'),patch.dict(sys.modules,onnxruntime=ort,onnx_asr=asr):
            worker.ParakeetEngine({'parakeet':{'model':'test','model_dir':'/models','gpu_mem_limit_mb':2867}})
        providers=asr.load_model.call_args.kwargs['providers']
        self.assertEqual(providers[0][1]['gpu_mem_limit'],512*1024*1024)
        pcm=model.recognize.call_args.args[0]
        self.assertEqual(pcm.size,20*16000)
        self.assertFalse(pcm.any())
        options.add_session_config_entry.assert_called_once_with('session.intra_op.allow_spinning','0')

    def test_moonshine_final_decode_error_is_not_swallowed_by_listener(self):
        from moonshine_voice.transcriber import Stream,TranscriptEventListener
        stream=object.__new__(Stream);stream._listeners=[];stream._handle=1
        engine=object.__new__(worker.AsrEngine);engine.listener_base=TranscriptEventListener
        engine.transcriber=SimpleNamespace(create_stream=lambda **kwargs:contextlib.nullcontext(stream))
        with tempfile.TemporaryFile() as spool:
            spool.write(bytes(32000));spool.flush()
            with patch.object(stream,'start'),patch.object(stream,'add_audio'),patch.object(stream,'stop',side_effect=lambda:stream._emit_error(RuntimeError('final decode failed'))):
                with self.assertRaisesRegex(RuntimeError,'Moonshine stream failed: final decode failed'):
                    engine.recognize_fd(spool.fileno(),16000)

    def test_worker_stop_clears_handles_even_before_reader_reaps(self):
        manager=main.WorkerManager({});proc=Mock();sock=Mock()
        manager._proc=proc;manager._sock=sock;manager.ready=True;manager._resident_requested=True
        manager.stop()
        self.assertIsNone(manager._proc);self.assertIsNone(manager._sock)
        self.assertFalse(manager.ready);self.assertFalse(manager._resident_requested)
        proc.wait.assert_called_once();sock.close.assert_called_once()

    def test_prewarm_reaps_cancelled_worker_before_replacement(self):
        manager=main.WorkerManager({});proc=Mock();sock=Mock()
        proc.poll.return_value=None
        manager._proc=proc;manager._sock=sock;manager._gen=1
        manager._intentional_exits.add(1)
        def spawn():
            proc.wait.assert_called_once()
            sock.close.assert_called_once()
            self.assertIsNone(manager._proc)
            self.assertTrue(manager._resident_requested)
        with patch.object(manager,'_spawn_locked',side_effect=spawn) as replacement:
            manager.prewarm()
        replacement.assert_called_once()

    def test_completed_session_stops_without_systemctl(self):
        daemon=object.__new__(main.DuskyDaemon)
        daemon._lock=threading.RLock();daemon.state='idle';daemon._session=None
        daemon._stop=threading.Event()
        with patch.object(main.subprocess,'run') as commands:
            daemon._maybe_self_stop()
        self.assertEqual(daemon.state,'stopping');self.assertTrue(daemon._stop.is_set())
        commands.assert_not_called()

    def test_disconnect_exits_worker_during_native_work(self):
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        code='import sys,time,threading; from dusky_worker import exit_on_disconnect; threading.Thread(target=exit_on_disconnect,args=(int(sys.argv[1]),),daemon=True).start(); print("ready",flush=True); time.sleep(30)'
        proc=subprocess.Popen([sys.executable,'-c',code,str(child.fileno())],pass_fds=(child.fileno(),),cwd=ROOT,stdout=subprocess.PIPE,text=True)
        child.close()
        try:
            self.assertEqual(proc.stdout.readline().strip(),'ready')
            parent.close()
            self.assertEqual(proc.wait(timeout=3),0)
        finally:
            parent.close()
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stdout.close()

    def test_completed_job_is_published_after_cleanup_and_idle(self):
        with tempfile.TemporaryDirectory() as td:
            daemon=object.__new__(main.DuskyDaemon)
            daemon._lock=threading.RLock();daemon.config={'state_dir':td}
            daemon._stop=threading.Event();daemon.worker=SimpleNamespace(stop=Mock())
            sess=SimpleNamespace(ready=threading.Event(),session_id='exact',transcript_path='/saved',
                _indicator=None,stop_event=threading.Event(),cancel_event=threading.Event(),run_file=Mock())
            daemon._session=sess
            result=Path(td)/'jobs/exact.json'
            def release():self.assertFalse(result.exists())
            daemon.worker.stop.side_effect=release
            write=main.atomic_write_text
            def publish(path,text):
                daemon.worker.stop.assert_called_once()
                self.assertEqual(daemon.state,'idle');self.assertIsNone(daemon._session)
                write(path,text)
            with patch.object(daemon,'_prewarm_worker'),patch.object(daemon,'_maybe_self_stop'),patch.object(main,'atomic_write_text',side_effect=publish):
                daemon._run_session(sess,True,Path('/audio'))
            self.assertTrue(json.loads(result.read_text())['ok'])

    def test_gpu_process_launch_failure_selects_cpu_without_leaking_sockets(self):
        manager=main.WorkerManager({});manager.backend='nvidia';manager.gpu={'uuid':'GPU-test'}
        before=len(list(Path('/proc/self/fd').iterdir()))
        with patch.object(main.subprocess,'Popen',side_effect=FileNotFoundError('GPU interpreter removed')):
            with self.assertRaises(FileNotFoundError):manager.prewarm()
        self.assertEqual(manager.backend,'cpu')
        self.assertIn('could not start',manager.fallback_reason)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)

    def gpu_reader(self, packets, *, expected_exit=False):
        manager=main.WorkerManager({});manager._gen=1;manager.backend='nvidia';manager._inflight['pending']=1
        if expected_exit:
            manager._discarded.add('pending');manager._intentional_exits.add(1)
        proc=Mock();sock=Mock();manager._proc=proc;manager._sock=sock
        sock.recvmsg.side_effect=[(json.dumps(p).encode(),[],0,None) for p in packets]+[(b'',[],0,None)]
        manager._reader_loop(1,proc,sock,'nvidia')
        self.assertNotIn('pending',manager._inflight)
        return manager

    def test_gpu_startup_failure_selects_cpu_and_releases_request(self):
        manager=self.gpu_reader([{'event':'ready','ok':False,'error':'missing CUDA library'}])
        self.assertEqual(manager.backend,'cpu')
        self.assertIn('missing CUDA library',manager.fallback_reason)
        self.assertFalse(manager._results['pending']['ok'])

    def test_preload_failure_starts_cpu_during_on_demand_recording(self):
        manager=main.WorkerManager({});manager._gen=1;manager.backend='nvidia'
        manager._resident_requested=True
        proc=Mock();sock=Mock();manager._proc=proc;manager._sock=sock
        sock.recvmsg.return_value=(b'{"event":"ready","ok":false,"error":"GPU failed"}',[],0,None)
        with patch.object(manager,'_spawn_locked') as spawn:
            manager._reader_loop(1,proc,sock,'nvidia')
        self.assertEqual(manager.backend,'cpu');spawn.assert_called_once()

    def test_gpu_inference_failure_and_crash_select_cpu(self):
        for packets in ([{'event':'ready','ok':True},{'request_id':'pending','ok':False,'error':'GPU out of memory'}],
                        [{'event':'ready','ok':True}]):
            with self.subTest(packets=packets):
                manager=self.gpu_reader(packets)
                self.assertEqual(manager.backend,'cpu')
                self.assertFalse(manager._results['pending']['ok'])

    def test_gpu_cancellation_does_not_disable_gpu(self):
        manager=self.gpu_reader([{'event':'ready','ok':True}],expected_exit=True)
        self.assertEqual(manager.backend,'nvidia')
        self.assertNotIn('pending',manager._results)

    def test_auto_without_gpu_environment_uses_cpu(self):
        manager=main.WorkerManager({'backend':'auto'})
        self.assertEqual(manager.backend,'cpu')
        self.assertIn('not installed',manager.fallback_reason)

    def test_gpu_timeout_selects_cpu(self):
        manager=main.WorkerManager({});manager.backend='nvidia';manager._proc=Mock();manager._proc.poll.return_value=None
        manager._inflight['pending']=1
        self.assertIsNone(manager.wait_result('pending',0))
        self.assertEqual(manager.backend,'cpu')

    def test_parakeet_blocks_preserve_samples_and_final_chunk(self):
        import numpy as np
        engine=object.__new__(worker.ParakeetEngine);engine.model=Mock()
        chunks=[]
        engine.model.recognize.side_effect=lambda pcm,**kwargs:chunks.append(pcm.copy()) or 'words'
        pcm=np.arange(16000*47,dtype=np.int16);pcm[16000*18:16000*19]=0
        with tempfile.TemporaryFile() as spool:
            spool.write(pcm.tobytes());spool.flush();progress=[]
            engine.recognize_fd(spool.fileno(),pcm.size,progress.append)
        self.assertTrue(all(chunk.size<=320000 for chunk in chunks))
        np.testing.assert_array_equal(np.concatenate(chunks),pcm.astype(np.float32)/32768)
        self.assertEqual(progress[-1],.99)

    def test_progress_keeps_request_pending_until_final_result(self):
        manager=main.WorkerManager({});manager._gen=1;manager._inflight['pending']=1
        proc=Mock();sock=Mock();manager._proc=proc;manager._sock=sock
        def final_packet():
            self.assertEqual(manager.progress,.42)
            self.assertIn('pending',manager._inflight)
            self.assertNotIn('pending',manager._results)
            return json.dumps({'request_id':'pending','ok':True,'text':'Complete.'}).encode(),[],0,None
        packets=iter([
            (b'{"event":"progress","request_id":"pending","fraction":0.42}',[],0,None),
            final_packet,
            (b'',[],0,None)])
        def receive(*args):
            packet=next(packets)
            return packet() if callable(packet) else packet
        sock.recvmsg.side_effect=receive
        manager._reader_loop(1,proc,sock)
        self.assertNotIn('pending',manager._inflight)
        self.assertEqual(manager._results['pending']['text'],'Complete.')

    def test_ffmpeg_preserves_samples(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'sine.wav'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3','-ar','16000',str(path)],check=True)
            self.assertEqual(sum(pcm.size for pcm in main.decode_file_to_pcm(path,1)),48000)
            decoded=main.decode_file_to_pcm(path,1);next(decoded);decoded.close()

    def test_ffmpeg_error_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.wav';path.write_text('bad')
            with self.assertRaisesRegex(RuntimeError,'ffmpeg failed'):list(main.decode_file_to_pcm(path,1))

    def test_spawn_failure_closes_socketpair(self):
        manager=main.WorkerManager({});pair=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        with patch.object(main.socket,'socketpair',return_value=pair),patch.object(main.subprocess,'Popen',side_effect=OSError('failed')):
            with self.assertRaises(OSError):manager.prewarm()
        self.assertTrue(all(s.fileno()==-1 for s in pair))

    def test_crash_releases_queue_slot(self):
        manager=main.WorkerManager({});manager._inflight['pending']=1
        manager._fail_generation(1,'crashed')
        self.assertNotIn('pending',manager._inflight)
        self.assertFalse(manager._results['pending']['ok'])

    def test_timeout_retires_stuck_worker(self):
        manager=main.WorkerManager({});manager._proc=Mock();manager._proc.poll.return_value=None
        manager._inflight['pending']=1
        self.assertIsNone(manager.wait_result('pending',0))
        manager._proc.kill.assert_called_once()

    def test_large_reply_is_lossless(self):
        raw,fd=worker.sealed_response({'ok':True,'text':'large '*20000})
        self.assertEqual(json.loads(raw)['payload'],'memfd')
        try:self.assertEqual(len(json.loads(os.pread(fd,os.fstat(fd).st_size,0))['text']),120000)
        finally:os.close(fd)

    def test_bad_spool_size_rejected(self):
        with tempfile.TemporaryFile() as f:
            f.write(b'1234');f.flush()
            worker.validate_audio_fd(f.fileno(),2)
            with self.assertRaises(ValueError):worker.validate_audio_fd(f.fileno(),3)

class TriggerTests(unittest.TestCase):
    def test_endpoint_disappearing_after_ensure_retries_before_delivery(self):
        send=trigger.send_command
        with tempfile.TemporaryDirectory() as td:
            with patch.object(trigger,'ensure_running'),patch.object(trigger,'control_path',return_value=Path(td)/'missing.sock'),patch.object(trigger,'is_socket_secure',return_value=False),patch.object(trigger,'send_command',return_value={'ok':True}) as retry,patch.object(trigger.socket,'socket') as connect:
                self.assertTrue(send({'command':'toggle'})['ok'])
            retry.assert_called_once_with({'command':'toggle'},timeout=trigger.DEFAULT_TIMEOUT,start_process=True,_retries=1)
            connect.assert_not_called()

    def test_missing_status_does_not_start_a_process(self):
        with patch.object(trigger,'send_command',side_effect=FileNotFoundError),patch.object(trigger,'ensure_running') as start,patch.object(sys,'argv',['dusky_trigger','--status']),contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(trigger.main(),0)
        start.assert_not_called()
        self.assertIn('idle',out.getvalue())

    def test_wait_uses_exact_job(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'transcripts').mkdir();(root/'jobs').mkdir()
            text=root/'transcripts/correct.txt';text.write_text('Correct result.\n')
            (root/'jobs/exact.json').write_text(json.dumps({'ok':True,'path':str(text),'job':'exact'}))
            with patch.object(trigger,'transcripts_dir',return_value=root/'transcripts'),patch.object(trigger,'send_command',side_effect=AssertionError('must not restart service')),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertTrue(trigger.wait_for_transcript(0,'exact')['ok'])
            self.assertEqual(out.getvalue(),'Correct result.\n')

    def test_wait_reports_failed_job(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'jobs').mkdir();(root/'jobs/fail.json').write_text('{"ok":false,"error":"failure"}')
            with patch.object(trigger,'transcripts_dir',return_value=root/'transcripts'):
                self.assertFalse(trigger.wait_for_transcript(0,'fail')['ok'])

if __name__=='__main__':unittest.main()
