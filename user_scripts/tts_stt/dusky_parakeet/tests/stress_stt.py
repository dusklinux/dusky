"""Real-model control/recovery test. Run with .venv/bin/python stress_stt.py APP SPEECH.wav [REPEATS]."""
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def request(path,payload):
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as sock:
        sock.settimeout(5);sock.connect(str(path));sock.send(json.dumps(payload).encode())
        return json.loads(sock.recv(65536))

def serve(config):
    spec=importlib.util.spec_from_file_location('stress_main',ROOT/'dusky_main.py')
    main=importlib.util.module_from_spec(spec);sys.modules[spec.name]=main;spec.loader.exec_module(main)
    main.APP_DIR=config.parent
    main.DuskyDaemon._maybe_self_stop=lambda self:None
    indicator_env=dict(os.environ)
    def indicator(sess):
        binary=config.parent/'dusky-rec-indicator'
        if binary.is_file():
            return subprocess.Popen([str(binary),sess.session_id],env=indicator_env,
                                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL)
    main.DuskyDaemon._spawn_indicator=staticmethod(indicator)
    daemon=main.DuskyDaemon(config)
    # Socket is isolated, but Wayland must use the real
    # session runtime directory when publishing the clipboard.
    os.environ['XDG_RUNTIME_DIR']=os.environ['DUSKY_REAL_RUNTIME_DIR']
    return daemon.run()

def stress(app,audio,repeats):
    app=app.resolve();audio=audio.resolve()
    with tempfile.TemporaryDirectory(prefix='dusky-stress-') as td:
        root=Path(td);cfg=json.loads((app/'config.json').read_text())
        if (app/'.venv-gpu').is_dir():
            (root/'.venv-gpu').symlink_to(app/'.venv-gpu',target_is_directory=True)
        cfg.update(state_dir=str(root/'state'),notifications=False,
                   worker_python=str(app/'.venv/bin/python'),worker_script=str(ROOT/'dusky_worker.py'))
        config=root/'config.json';config.write_text(json.dumps(cfg))
        with wave.open(str(audio)) as wav:
            params=wav.getparams();frames=wav.readframes(wav.getnframes());duration=params.nframes/params.framerate
        long=root/'long.wav'
        with wave.open(str(long),'wb') as wav:
            wav.setparams(params)
            for _ in range(repeats):wav.writeframes(frames)
        env=dict(os.environ,XDG_RUNTIME_DIR=str(root/'runtime'),DUSKY_APP_DIR=str(root),DUSKY_CONFIG=str(config))
        env['WAYLAND_DISPLAY']=str(Path(os.environ['XDG_RUNTIME_DIR'])/os.environ['WAYLAND_DISPLAY'])
        env['DUSKY_REAL_RUNTIME_DIR']=os.environ['XDG_RUNTIME_DIR']
        env.pop('NOTIFY_SOCKET',None);env.pop('WATCHDOG_USEC',None)
        endpoint=root/'runtime/dusky-stt/control.sock'
        log=open(root/'daemon.log','w+')
        proc=subprocess.Popen([sys.executable,str(__file__),'--serve',str(config)],env=env,stdout=log,stderr=log)
        checks=[];pids=set();latencies=[];peak=0
        def idle():
            deadline=time.monotonic()+30
            while request(endpoint,{'command':'status'})['state']!='idle':
                assert time.monotonic()<deadline;time.sleep(.05)
        def job(path,cancel=False,crash=False):
            nonlocal peak
            idle();reply=request(endpoint,{'command':'file','path':str(path)});assert reply['ok'],reply
            result=root/'state/jobs'/f"{reply['job']}.json";start=time.monotonic();injected=False
            while not result.exists():
                assert proc.poll() is None,'daemon exited';assert time.monotonic()-start<600,'job timeout'
                tick=time.monotonic();status=request(endpoint,{'command':'status'});latencies.append(time.monotonic()-tick)
                pid=status.get('worker_pid')
                if pid:
                    pids.add(pid)
                    try:
                        rss=next(l.split()[1] for l in Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith('VmRSS:'))
                        peak=max(peak,int(rss)/1024)
                    except (OSError,StopIteration):pass
                if cancel and not injected and time.monotonic()-start>.3:
                    assert request(endpoint,{'command':'stop'})['ok'];injected=True
                if crash and pid and not injected and time.monotonic()-start>.4:
                    os.kill(pid,signal.SIGKILL);injected=True
                time.sleep(.05)
            answer=json.loads(result.read_text());answer['elapsed_s']=time.monotonic()-start
            return answer
        try:
            deadline=time.monotonic()+15
            while not endpoint.exists():
                assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.05)
            assert not request(endpoint,[])['ok'];checks.append('invalid control request rejected')
            baseline=job(audio);assert baseline['ok'],baseline
            backend=request(endpoint,{'command':'status'})['hardware']
            expected=Path(baseline['path']).read_text().strip()
            result=job(long);assert result['ok'],result
            assert request(endpoint,{'command':'status'})['hardware']==backend,'backend changed during long recording'
            text=Path(result['path']).read_text()
            # Natural VAD endpoints may shift at joins. Count the repeated opening
            # marker rather than require punctuation/hypotheses to be identical.
            marker=expected.split('.')[0].casefold()
            assert text.casefold().count(marker)==repeats,(marker,text)
            assert text.casefold().count('comparison only') == repeats, text
            checks.append(f'all {repeats} repeated opening and ending markers retained')
            bad=root/'bad.wav';bad.write_text('invalid');assert not job(bad)['ok'];checks.append('invalid file fails')
            assert not job(long,cancel=True)['ok'];checks.append('cancellation reports failure')
            recovered=job(audio);assert recovered['ok'],recovered;checks.append('recovered after cancellation')
            restarted=job(audio,crash=True);assert restarted['ok'],restarted;checks.append('worker crash retried')
            idle();assert request(endpoint,{'command':'unload'})['ok'];checks.append('unload succeeds')
            maps=Path(f'/proc/{proc.pid}/maps').read_text();assert 'libcuda.so' not in maps and 'libcudart' not in maps
            checks.append('CPU daemon remained CUDA-free')
            model=cfg['parakeet']['model'] if backend=='nvidia' else cfg['model']
            print(json.dumps({'backend':backend,'model':model,'audio_s':duration*repeats,'processing_s':result['elapsed_s'],
                'sampled_worker_rss_mib':round(peak,1),'max_status_ms':round(max(latencies)*1000,2),
                'checks':checks,'transcript_preview':text[:160]},indent=2),flush=True)
        finally:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            log.seek(0);logs=log.read();log.close()
            if sys.exc_info()[0]:print(logs,file=sys.stderr)
            for pid in pids:assert not Path(f'/proc/{pid}').exists(),f'worker {pid} survived shutdown'

if __name__=='__main__':
    if sys.argv[1]=='--serve':sys.exit(serve(Path(sys.argv[2])))
    stress(Path(sys.argv[1]),Path(sys.argv[2]),int(sys.argv[3]) if len(sys.argv)>3 else 8)
