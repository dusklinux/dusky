"""Real PipeWire/PortAudio capture through a disposable virtual mic.
Run with .venv/bin/python stress_microphone.py APP SPEECH.wav.
Does not change the physical microphone or global default device.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from stress_stt import ROOT,request

def stress(app,audio):
    app=app.resolve();audio=audio.resolve()
    sink=f'dusky_stt_test_{os.getpid()}'
    module=subprocess.run(['pactl','load-module','module-null-sink',f'sink_name={sink}'],capture_output=True,text=True,check=True).stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix='dusky-microphone-') as td:
            root=Path(td);config=root/'config.json';cfg=json.loads((app/'config.json').read_text())
            if (app/'.venv-gpu').is_dir():
                (root/'.venv-gpu').symlink_to(app/'.venv-gpu',target_is_directory=True)
            cfg.update(state_dir=str(root/'state'),notifications=False,input_device='pulse',
                       worker_python=str(app/'.venv/bin/python'),worker_script=str(ROOT/'dusky_worker.py'))
            config.write_text(json.dumps(cfg))
            (root/'dusky-rec-indicator').symlink_to(app/'dusky-rec-indicator')
            real_runtime=os.environ['XDG_RUNTIME_DIR']
            env=dict(os.environ,XDG_RUNTIME_DIR=str(root/'runtime'),PULSE_SERVER=f'unix:{real_runtime}/pulse/native',PULSE_SOURCE=f'{sink}.monitor')
            env['WAYLAND_DISPLAY']=str(Path(real_runtime)/os.environ['WAYLAND_DISPLAY'])
            env['DUSKY_REAL_RUNTIME_DIR']=real_runtime
            env.pop('NOTIFY_SOCKET',None);env.pop('WATCHDOG_USEC',None)
            log=open(root/'daemon.log','w+')
            proc=subprocess.Popen([sys.executable,str(ROOT/'tests/stress_stt.py'),'--serve',str(config)],env=env,stdout=log,stderr=log)
            endpoint=root/'runtime/dusky-stt/control.sock'
            try:
                deadline=time.monotonic()+15
                while not endpoint.exists():
                    assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.05)
                started=request(endpoint,{'command':'start'});assert started['ok'],started
                time.sleep(.5)
                before_stop=request(endpoint,{'command':'status'})
                assert before_stop['state']=='recording'
                assert request(endpoint,{'command':'pause'})['event']=='paused'
                subprocess.run(['pw-play','--target',sink,str(audio)],check=True,timeout=90)
                assert request(endpoint,{'command':'pause'})['event']=='resumed'
                subprocess.run(['pw-play','--target',sink,str(audio)],check=True,timeout=90)
                before_stop=request(endpoint,{'command':'status'})
                assert before_stop['worker_pid'] is not None,'model was not preloaded during capture'
                assert before_stop['worker_ready'],'model did not finish loading during capture'
                assert before_stop['progress']==0,'recorded audio was processed before Stop'
                worker_pid=before_stop['worker_pid']
                stopped=request(endpoint,{'command':'stop'});assert stopped['ok'],stopped
                result=root/'state/jobs'/f"{started['job']}.json";deadline=time.monotonic()+120
                progress=[];processing_pill_seen=False
                while not result.exists():
                    assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.1)
                    status=request(endpoint,{'command':'status'})
                    if status['state']=='finalizing':
                        progress.append(status['progress'])
                        layers=json.loads(subprocess.check_output(['hyprctl','layers','-j']))
                        processing_pill_seen |= any(x.get('namespace')=='dusky-stt'
                            for output in layers.values() for entries in output['levels'].values() for x in entries)
                job=json.loads(result.read_text());assert job['ok'],job
                assert progress and max(progress)>0,'processing progress not reported'
                assert processing_pill_seen,'pill disappeared during transcription'
                text=Path(job['path']).read_text()
                assert text.casefold().count('best of times')==1,text
                assert 'age of foolishness' in text.casefold(), text
                deadline=time.monotonic()+15
                while request(endpoint,{'command':'status'})['state']!='idle':
                    assert time.monotonic()<deadline;time.sleep(.05)
                assert not Path(f'/proc/{worker_pid}').exists(),'worker retained after on-demand capture'
                backend=request(endpoint,{'command':'status'})['hardware']
                model=cfg['parakeet']['model'] if backend=='nvidia' else cfg['model']
                print(json.dumps({'backend':backend,'model':model,'checks':['real PipeWire/PortAudio capture','paused speech excluded',
                    'worker loaded during capture; recorded audio waits for Stop','processing progress reported','pill retained during transcription',
                    'one complete utterance transcribed','on-demand worker reaped'],
                    'max_processing_progress':max(progress),'transcript':text.strip()},indent=2))
            finally:
                proc.terminate()
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                log.seek(0);logs=log.read();log.close()
                if sys.exc_info()[0]:print(logs,file=sys.stderr)
    finally:subprocess.run(['pactl','unload-module',module],check=True)

if __name__=='__main__':stress(Path(sys.argv[1]),Path(sys.argv[2]))
