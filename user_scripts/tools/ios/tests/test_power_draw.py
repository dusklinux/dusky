"""Local regression/stress tests; never connect to a phone.

Run: python3 -m unittest discover -s user_scripts/tools/ios/tests -v
POWER_DRAW_MODULE may point to a saved script for before/after comparisons.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from rich.console import Console

SCRIPT = Path(os.environ.get('POWER_DRAW_MODULE', Path(__file__).resolve().parents[1] / 'power_draw.py'))
spec = importlib.util.spec_from_file_location('power_draw_test_subject', SCRIPT)
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)

FAKE_SSH = r'''
import json, os, sys, time
if '-O' in sys.argv or sys.argv[-1] == 'true': sys.exit(0)
mode = os.environ.get('PHONE_FIXTURE', 'normal')
if os.environ.get('PHONE_FIXTURE_STARTED'):
    with open(os.environ['PHONE_FIXTURE_STARTED'],'w') as marker:marker.write(str(os.getpid()))
for index, command in enumerate(sys.stdin):
    if mode == 'eof': print('Fixture disconnect', file=sys.stderr); break
    if mode == 'late_error':
        os.close(1);time.sleep(.05);print('Delayed remote error',file=sys.stderr);break
    if mode == 'hang': time.sleep(10); continue
    if mode == 'partial': sys.stdout.write('{'); sys.stdout.flush(); time.sleep(10); continue
    if mode == 'oversize': print('x' * 66000, flush=True); continue
    row = dict(time=time.time(), percent=70, voltage_mv=3900, current_ma=-400,
               battery_c=29.0, external_power=False, cpu_c=None, capacity_mah=2200)
    if mode == 'invalid': row['percent'] = True
    if mode == 'overflow': row['voltage_mv'] = row['current_ma'] = 1e308
    if mode == 'nan': row['cpu_c'] = float('nan')
    if mode == 'charging': row.update(current_ma=400, external_power=True)
    if mode == 'external_discharge': row.update(current_ma=-400, external_power=True)
    if mode == 'idle': row.update(current_ma=0, external_power=True)
    if mode == 'bad_voltage': row['voltage_mv'] = -1
    payload = json.dumps(row) + '\n'
    if mode == 'fragmented':
        for offset in range(0,len(payload),7):
            sys.stdout.write(payload[offset:offset+7]);sys.stdout.flush()
    else: print(payload, end='', flush=True)
'''


def sample(t, watts=1, **extra):
    return dict(received_monotonic=t, power_w=watts, current_ma=-watts*250,
                segment=0, external_power=False, elapsed=t, percent=70,
                battery_c=29, cpu_c=None, time=100+t, **extra)


class Configuration(unittest.TestCase):
    def test_relative_identity_is_stable_and_preserves_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'key').write_text('fixture')
            (root/'key-link').symlink_to('key')
            previous=Path.cwd()
            try:
                os.chdir(root)
                config=dict(power.DEFAULTS,host='phone',identity='./key-link')
                power.validate(config)
                os.chdir(root.parent)
                self.assertTrue(Path(config['identity']).is_absolute())
                self.assertTrue(Path(config['identity']).is_file())
                self.assertTrue(Path(config['identity']).is_symlink())
            finally:os.chdir(previous)

    def test_empty_and_relative_xdg_use_home_config(self):
        code='import runpy; print(runpy.run_path('+repr(str(SCRIPT))+')["STATE_DIR"])'
        for value in ('','relative/path'):
            result=subprocess.run([sys.executable,'-c',code],env=os.environ|{'XDG_CONFIG_HOME':value},
                                  capture_output=True,text=True,timeout=5,check=True)
            self.assertEqual(Path(result.stdout.strip()),Path.home()/'.config/dusky/settings/ssh_phone_reading')

    def test_interactive_count_wins_even_if_equal_to_previous_count(self):
        with patch.object(power,'CONSOLE',Console(file=io.StringIO())), \
             patch.object(power.Confirm,'ask',return_value=False), \
             patch.object(power.Prompt,'ask',side_effect=['phone','','','Label','none']), \
             patch.object(power.IntPrompt,'ask',side_effect=[22,13]) as prompt, \
             patch.object(power.FloatPrompt,'ask',side_effect=[10,0,40,12]):
            config=power.configure(dict(power.DEFAULTS,host='phone'),minutes=4)
        self.assertEqual(prompt.call_args.kwargs['default'],25)
        self.assertEqual(config['readings'],13)
        self.assertEqual(config['interval'],10)

    def test_defaults_and_metrics_normalization(self):
        config = dict(power.DEFAULTS, host='phone', metrics=' CPU, memory, CPU , cpu-temp ')
        power.validate(config)
        self.assertEqual(config['metrics'], 'cpu,memory,cpu-temp')

    def test_invalid_numeric_and_metric_values(self):
        for change in ({'interval':0}, {'timeout':float('inf')}, {'interval':True},
                       {'readings':True}, {'port':True}, {'metrics':'none,cpu'},
                       {'metrics':''}, {'metrics':'unknown'}, {'baseline_watts':float('nan')}, {'live':1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                power.validate(dict(power.DEFAULTS, host='phone') | change)

    def test_concurrent_atomic_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configs = [dict(power.DEFAULTS,host=f'phone-{i}',label=f'run-{i}') for i in range(80)]
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda config: power.save_config(root, config), configs))
            result = json.loads((root/'config.json').read_text())
            self.assertIn(result, configs)
            self.assertEqual(list(root.glob('*.tmp')), [])

    def test_duration_validation_before_setup(self):
        for minutes in ('-1','nan','inf','1e308'):
            result = subprocess.run([sys.executable,str(SCRIPT),'--minutes',minutes],
                                    stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2,result.stdout+result.stderr)
            self.assertNotIn('Traceback',result.stderr)


class Accounting(unittest.TestCase):
    def test_unequal_spacing_and_charge(self):
        result = power.summarize([sample(0,1),sample(10,3),sample(30,5)],20)
        self.assertAlmostEqual(result['mean_w'],100/30)
        self.assertAlmostEqual(result['energy_wh'],100/3600)
        self.assertAlmostEqual(result['consumed_mah'],25000/3600)

    def test_skips_gaps_reconnects_external_power_and_charging(self):
        changes = [dict(segment=1),dict(external_power=True),dict(current_ma=100)]
        for change in changes:
            result = power.summarize([sample(0),sample(20)|change],20)
            self.assertIsNone(result['mean_w'])
        self.assertIsNone(power.summarize([sample(0),sample(100)],20)['mean_w'])
        self.assertIsNone(power.summarize([],20)['mean_w'])

    def test_long_session(self):
        rows = [sample(i*20,1.5) for i in range(100_000)]
        result = power.summarize(rows,20)
        self.assertAlmostEqual(result['mean_w'],1.5)
        self.assertEqual(result['integrated_intervals'],99_999)

    def test_empty_optional_panels(self):
        for compact in (True,False):
            self.assertIsNone(power.system_panel({},compact))

    def test_bounded_history_retains_full_session_totals(self):
        rows=power.Readings(20)
        for i in range(100_000):
            rows.append(sample(i*20,1.5)|{'collector_cpu_ms':2,'collector_peak_rss_bytes':1000+i})
        self.assertEqual(len(rows),100_000)
        self.assertEqual(len(rows.recent),60)
        self.assertEqual(rows.first['elapsed'],0)
        self.assertAlmostEqual(rows.summary()['mean_w'],1.5)
        self.assertEqual(rows.summary()['integrated_intervals'],99_999)
        self.assertEqual((rows.cpu_count,rows.cpu_ms,rows.peak_rss),(100_000,200_000,100_999))
        self.assertEqual(len(rows[-5:]),5)

    def test_incremental_statistics_equal_batch_with_mixed_sources(self):
        rows=[sample(0,1),sample(10,3),sample(30,5),sample(40,2)|{'external_power':True},
              sample(50,2),sample(60,4)|{'segment':1},sample(70,6)|{'segment':1}]
        session=power.Readings(20)
        for row in rows:session.append(row)
        self.assertEqual(session.summary(),power.summarize(rows,20))
        self.assertEqual(session.summary()['integrated_intervals'],3)
        self.assertAlmostEqual(session.summary()['energy_wh'],150/3600)

    def test_charging_display_has_no_negative_magnitudes(self):
        row=sample(0,1.56)|dict(current_ma=400,external_power=True)
        output=io.StringIO();console=Console(file=output,width=160,height=50)
        with patch.object(power,'CONSOLE',console):
            console.print(power.dashboard([row],dict(power.DEFAULTS,live=True,host='phone'),'waiting',time.monotonic()))
        text=output.getvalue()
        self.assertIn('Charging',text)
        self.assertIn('LIVE',text)
        self.assertIn('External · charging',text)
        self.assertNotIn('-1.56',text)
        self.assertNotIn('-400',text)

    def test_final_report_uses_first_reading_and_all_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            session=power.Readings(20)
            for i in range(100):session.append(sample(i*20)|{'percent':100-i//2})
            output=io.StringIO()
            path=Path(directory)/'session.jsonl'
            with patch.object(power,'CONSOLE',Console(file=output,width=160)):
                power.final_report(session,dict(power.DEFAULTS),path,'Done')
            self.assertIn('Latest 60 of 100',output.getvalue())
            self.assertIn('100% → 51%',output.getvalue())
            summary=json.loads(path.with_suffix('.summary.json').read_text())['summary']
            self.assertEqual((summary['readings'],summary['integrated_intervals']),(100,99))

    def test_responsive_dashboard(self):
        row = sample(0)|dict(cpu_cores_percent=[0,10,None,50,100,0],cpu_percent=32)
        row['memory'] = {key:100*2**20 for key in ('total','free','active','inactive','wired',
                                                 'compressed','compressed_logical','purgeable')}
        for width,height in ((60,24),(80,24),(140,50)):
            output=io.StringIO();console=Console(file=output,width=width,height=height)
            with patch.object(power,'CONSOLE',console):
                console.print(power.dashboard([row],dict(power.DEFAULTS,host='phone',label='[literal]'),
                                              'waiting',time.monotonic()))
            self.assertIn('PHONE POWER',output.getvalue())


class Transport(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.fake=self.root/'ssh.py';self.fake.write_text(FAKE_SSH)
        self.phone=None

    def tearDown(self):
        if self.phone:self.phone.close()
        self.directory.cleanup()

    def connect(self,timeout=1):
        self.phone=power.Phone(dict(power.DEFAULTS,host='fixture',timeout=timeout),str(self.root/'socket'))
        self.phone.base=[sys.executable,str(self.fake)]
        self.phone.connect()
        return self.phone

    def test_persistent_5000_readings_without_descriptor_growth(self):
        phone=self.connect();process=phone.process
        before=len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(5000):self.assertAlmostEqual(phone.read()['power_w'],1.56)
        self.assertIs(phone.process,process)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)
        phone.close();self.assertIsNotNone(process.poll())

    def test_repeated_connect_cleanup(self):
        before=len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(25):
            phone=self.connect();process=phone.process
            phone.read();phone.close();phone.close()
            self.assertIsNotNone(process.poll())
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())),before)

    def test_bad_and_fragmented_responses(self):
        for mode in ('eof','oversize','invalid','nan','overflow','bad_voltage','fragmented'):
            with self.subTest(mode=mode), patch.dict(os.environ,PHONE_FIXTURE=mode):
                phone=self.connect()
                if mode=='fragmented':self.assertAlmostEqual(phone.read()['power_w'],1.56)
                else:
                    with self.assertRaises(RuntimeError):phone.read()
                phone.close()

    def test_charging_discharging_and_idle_magnitudes(self):
        for mode,flow,signed in [('charging','charging',-1.56),('external_discharge','discharging',1.56),('idle','idle',0.0)]:
            with self.subTest(mode=mode),patch.dict(os.environ,PHONE_FIXTURE=mode):
                phone=self.connect();row=phone.read()
                self.assertEqual(row['battery_flow'],flow)
                self.assertAlmostEqual(row['power_w'],abs(signed))
                self.assertAlmostEqual(row['signed_power_w'],signed)
                phone.close()

    def test_remote_error_after_stdout_closes_is_reported(self):
        with patch.dict(os.environ,PHONE_FIXTURE='late_error'):
            phone=self.connect()
            with self.assertRaisesRegex(RuntimeError,'Delayed remote error'):phone.read()

    def test_pipe_oserror_is_a_recoverable_connection_error(self):
        phone=self.connect()
        with patch.object(os,'read',side_effect=OSError('fixture pipe error')):
            with self.assertRaises(RuntimeError):phone.read()

    def test_partial_and_missing_response_timeout(self):
        for mode in ('hang','partial'):
            with self.subTest(mode=mode), patch.dict(os.environ,PHONE_FIXTURE=mode):
                phone=self.connect(timeout=0.15)
                started=time.monotonic()
                with self.assertRaisesRegex(RuntimeError,'timed out'):phone.read()
                self.assertLess(time.monotonic()-started,1)
                phone.close()

    def test_callback_interrupts_active_read_without_worker(self):
        with patch.dict(os.environ,PHONE_FIXTURE='hang'):
            phone=self.connect();process=phone.process
            def interrupt():raise KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):phone.read(on_wait=interrupt)
            phone.close();self.assertIsNotNone(process.poll())

    def test_failed_process_start_closes_error_file(self):
        phone=power.Phone(dict(power.DEFAULTS,host='fixture'),str(self.root/'socket'))
        self.phone=phone
        with patch.object(subprocess,'run',return_value=subprocess.CompletedProcess([],0)), \
                patch.object(subprocess,'Popen',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):phone.connect()
        phone.close()
        self.assertIsNone(phone.errors)


class NativeContract(unittest.TestCase):
    def test_page_size_output_is_pointer_sized(self):
        tree=ast.parse(power.REMOTE)
        page_sizes=[node.value for node in ast.walk(tree) if isinstance(node,ast.Assign)
                    and any(isinstance(target,ast.Name) and target.id=='page_size' for target in node.targets)]
        self.assertEqual(len(page_sizes),1)
        self.assertEqual(ast.unparse(page_sizes[0]),'C.c_size_t()')


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        ssh=self.root/'ssh';ssh.write_text('#!'+sys.executable+'\n'+FAKE_SSH);ssh.chmod(0o755)
        self.env=os.environ|{'PATH':str(self.root)+os.pathsep+os.environ['PATH']}
        self.state=self.root/'state'
        self.command=[sys.executable,str(SCRIPT),'--config-dir',str(self.state)]

    def tearDown(self):self.directory.cleanup()

    def test_saved_settings_duration_rounding_and_reports(self):
        first=subprocess.run(self.command+['--host','fixture','--minutes','.001','--interval','1',
                                          '--label','Persistent label','--metrics','NONE'],
                             env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=8)
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        config=json.loads((self.state/'config.json').read_text())
        self.assertEqual((config['readings'],config['interval'],config['metrics']),(2,1,'none'))
        again=subprocess.run(self.command,env=self.env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=8)
        self.assertEqual(again.returncode,0,again.stdout+again.stderr)
        self.assertEqual(len(list((self.state/'sessions').glob('*.summary.json'))),2)
        for path in (self.state/'sessions').glob('*.jsonl'):
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertGreaterEqual(rows[1]['received_monotonic']-rows[0]['received_monotonic'],1)

    def test_live_survives_saved_count_and_stops_cleanly(self):
        power.save_config(self.state,dict(power.DEFAULTS,host='fixture',readings=1))
        proc=subprocess.Popen(self.command+['--host','fixture','--live','--interval','1'],
                              env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+6
            log=None
            while time.monotonic()<deadline:
                logs=list((self.state/'sessions').glob('*.jsonl'))
                if logs:
                    log=logs[0]
                    if len(log.read_text().splitlines())>=3:break
                time.sleep(.02)
            self.assertIsNotNone(log)
            self.assertGreaterEqual(len(log.read_text().splitlines()),3)
            self.assertIsNone(proc.poll())
            proc.send_signal(signal.SIGINT);out,err=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,0,out+err)
            self.assertEqual(out.count('Session complete'),1)
            self.assertNotIn('Traceback',err)
            self.assertTrue(json.loads((self.state/'config.json').read_text())['live'])
            summary=json.loads(log.with_suffix('.summary.json').read_text())
            self.assertGreaterEqual(summary['summary']['readings'],3)
            # Explicit finite count overrides and clears a saved live setting.
            done=subprocess.run(self.command+['--readings','1'],env=self.env,stdin=subprocess.DEVNULL,
                                capture_output=True,text=True,timeout=5)
            self.assertEqual(done.returncode,0,done.stdout+done.stderr)
            self.assertFalse(json.loads((self.state/'config.json').read_text())['live'])
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()

    def test_live_retries_disconnect_until_interrupted(self):
        marker=self.root/'collector-started'
        proc=subprocess.Popen(self.command+['--host','fixture','--live','--interval','1'],
                              env=self.env|{'PHONE_FIXTURE':'eof','PHONE_FIXTURE_STARTED':str(marker)},
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists());first=marker.read_text()
            deadline=time.monotonic()+9
            while marker.read_text()==first and time.monotonic()<deadline:time.sleep(.02)
            self.assertNotEqual(marker.read_text(),first)
            self.assertIsNone(proc.poll());proc.send_signal(signal.SIGINT)
            out,err=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,0,out+err)
            self.assertIn('Reconnecting in 5s',out)
            self.assertNotIn('Traceback',err)
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()

    def test_live_is_exclusive_with_duration_and_count(self):
        for option in (['--readings','1'],['--minutes','1']):
            result=subprocess.run(self.command+['--live']+option,env=self.env,stdin=subprocess.DEVNULL,
                                  capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,2)

    def assert_signal_cleanup(self,stop_signal):
        marker=self.root/'collector-started'
        proc=subprocess.Popen(self.command+['--host','fixture','--readings','10','--timeout','10'],
                              env=self.env|{'PHONE_FIXTURE':'hang','PHONE_FIXTURE_STARTED':str(marker)},
                              stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists())
            pid=int(marker.read_text());proc.send_signal(stop_signal)
            out,err=proc.communicate(timeout=8)
            self.assertEqual(proc.returncode,1,out+err)
            self.assertEqual(out.count('Session complete'),1,out)
            self.assertNotIn('Traceback',err)
            self.assertEqual(len(list((self.state/'sessions').glob('*.summary.json'))),1)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
        finally:
            if proc.poll() is None:proc.kill();proc.communicate()

    def test_sigint_during_read_saves_once_and_reaps_collector(self):
        self.assert_signal_cleanup(signal.SIGINT)

    def test_sigterm_during_read_saves_once_and_reaps_collector(self):
        self.assert_signal_cleanup(signal.SIGTERM)

    def test_sighup_during_read_saves_once_and_reaps_collector(self):
        self.assert_signal_cleanup(signal.SIGHUP)

    def test_terminal_failure_does_not_prevent_summary(self):
        self.state.mkdir()
        path=self.state/'run.jsonl'
        with patch.object(power.CONSOLE,'print',side_effect=OSError('terminal closed')):
            with self.assertRaises(OSError):
                power.final_report([],dict(power.DEFAULTS),path,'Terminal closed')
        self.assertEqual(json.loads(path.with_suffix('.summary.json').read_text())['reason'],'Terminal closed')

    def test_main_does_not_overwrite_interactive_reading_count(self):
        with patch.object(sys,'argv',self.command[1:]+['--host','fixture','--configure','--minutes','4']), \
             patch.object(sys.stdin,'isatty',return_value=True), \
             patch.object(power,'configure',side_effect=lambda config,**kwargs:config|{'interval':10,'readings':13}), \
             patch.object(power.Phone,'connect',side_effect=RuntimeError('fixture')), \
             patch.object(power.Phone,'close'),patch.object(power.Prompt,'ask',return_value='finish'), \
             patch.object(power,'CONSOLE',Console(file=io.StringIO())):
            power.main()
        config=json.loads((self.state/'config.json').read_text())
        self.assertEqual((config['interval'],config['readings']),(10,13))

    def test_malformed_state_does_not_get_overwritten(self):
        self.state.mkdir()
        for data in ('{broken','[]','{"interval":true,"host":"fixture"}'):
            (self.state/'config.json').write_text(data)
            result=subprocess.run(self.command,env=self.env,stdin=subprocess.DEVNULL,
                                  capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,1)
            self.assertNotIn('Traceback',result.stderr)
            self.assertEqual((self.state/'config.json').read_text(),data)


if __name__=='__main__':unittest.main()
