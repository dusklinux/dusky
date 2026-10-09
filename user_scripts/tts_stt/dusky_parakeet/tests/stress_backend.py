"""Inject GPU failures, then exercise real Moonshine/clipboard recovery.
Run with APP/.venv/bin/python stress_backend.py APP SPEECH.wav.
GPU inference itself is intentionally simulated; CPU recovery is real.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from stress_stt import ROOT, request, serve

FAKE_WORKER = '''import json,os,socket,sys,struct
from pathlib import Path
backend=sys.argv[sys.argv.index('--backend')+1]
if backend=='cpu':
    os.execv(sys.executable,[sys.executable,SOURCE,*sys.argv[1:]])
fd=int(sys.argv[sys.argv.index('--fd')+1]);sock=socket.socket(fileno=fd)
config=json.loads(Path(sys.argv[sys.argv.index('--config')+1]).read_text())
mode=config['test_failure']
if mode=='startup':
    sock.send(json.dumps({'ok':False,'event':'ready','error':'injected GPU initialization failure'}).encode())
else:
    sock.send(json.dumps({'ok':True,'event':'ready','hardware':'nvidia'}).encode())
    payload,ancdata,_,_=sock.recvmsg(65536,socket.CMSG_SPACE(4))
    for level,kind,data in ancdata:
        if kind==socket.SCM_RIGHTS:os.close(struct.unpack('i',data)[0])
    if mode=='crash':os._exit(3)
    req=json.loads(payload)
    sock.send(json.dumps({'ok':False,'request_id':req['request_id'],'error':'injected GPU inference failure'}).encode())
sock.close()
'''


def stress(app, audio):
    app=app.resolve();audio=audio.resolve();checks=[]
    for failure in ('startup','inference','crash'):
        with tempfile.TemporaryDirectory(prefix='dusky-backend-') as td:
            root=Path(td);cfg=json.loads((app/'config.json').read_text())
            cfg.update(backend='auto',state_dir=str(root/'state'),notifications=False,test_failure=failure,
                parakeet={'model':'nemo-parakeet-tdt-0.6b-v2','model_dir':str(root),'gpu_mem_limit_mb':2048},
                worker_python=str(app/'.venv/bin/python'),worker_script=str(root/'fake_worker.py'))
            (root/'.venv-gpu/bin').mkdir(parents=True)
            (root/'.venv-gpu/bin/python').symlink_to(app/'.venv/bin/python')
            (root/'fake_worker.py').write_text('SOURCE='+repr(str(ROOT/'dusky_worker.py'))+'\n'+FAKE_WORKER)
            config=root/'config.json';config.write_text(json.dumps(cfg))
            real_runtime=os.environ['XDG_RUNTIME_DIR']
            env=dict(os.environ,XDG_RUNTIME_DIR=str(root/'runtime'),DUSKY_REAL_RUNTIME_DIR=real_runtime)
            env['WAYLAND_DISPLAY']=str(Path(real_runtime)/os.environ['WAYLAND_DISPLAY'])
            env.pop('NOTIFY_SOCKET',None);env.pop('WATCHDOG_USEC',None)
            endpoint=root/'runtime/dusky-stt/control.sock'
            with (root/'daemon.log').open('w+') as log:
                proc=subprocess.Popen([sys.executable,str(__file__),'--serve',str(config)],env=env,stdout=log,stderr=log)
                try:
                    deadline=time.monotonic()+45
                    while not endpoint.exists():
                        assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.05)
                    reply=request(endpoint,{'command':'file','path':str(audio)});assert reply['ok'],reply
                    result=root/'state/jobs'/f"{reply['job']}.json"
                    while not result.exists():
                        assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.05)
                    job=json.loads(result.read_text());assert job['ok'],job
                    text=Path(job['path']).read_text();assert 'age of foolishness' in text.casefold(),text
                    while request(endpoint,{'command':'status'})['state']!='idle':
                        assert time.monotonic()<deadline;time.sleep(.05)
                    status=request(endpoint,{'command':'status'})
                    assert status['hardware']=='cpu' and status['fallback_reason'],status
                    assert subprocess.check_output(['wl-paste','--no-newline']).decode()==text.rstrip('\n')
                    reply=request(endpoint,{'command':'backend','backend':'cpu'});assert reply['ok'],reply
                    assert json.loads(config.read_text())['backend']=='cpu'
                    assert not request(endpoint,{'command':'backend','backend':'invalid'})['ok']
                    checks.append(f'{failure}: original recording recovered on CPU; clipboard and backend preference verified')
                finally:
                    proc.terminate()
                    try:proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                    if sys.exc_info()[0]:log.seek(0);print(log.read(),file=sys.stderr)
    print(json.dumps({'checks':checks,'gpu_inference':'simulated','cpu_inference':'real'},indent=2))


if __name__=='__main__':
    if sys.argv[1]=='--serve':
        import dusky_hardware
        dusky_hardware.detect_nvidia=lambda:[{'index':0,'uuid':'GPU-test','name':'Test GPU','memory_mib':4096}]
        sys.exit(serve(Path(sys.argv[2])))
    stress(Path(sys.argv[1]),Path(sys.argv[2]))
