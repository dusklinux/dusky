"""Host-only behavioral tests. No connection or phone action is performed."""
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
loader=importlib.machinery.SourceFileLoader('iphone_control',str(ROOT/'iphone-control'))
spec=importlib.util.spec_from_loader(loader.name,loader)
control=importlib.util.module_from_spec(spec)
loader.exec_module(control)


class FormatPhone:
    def __init__(self, states):
        self.states=states;self.index=0;self.actions=[]
    def rpc(self,action,parameters=None):
        self.actions.append(action)
        if action=='camera.status':return {'mode':1,'recording':False}
        if action=='camera.format':return self.states[self.index]
        if action=='camera.fps-next':self.index=(self.index+1)%len(self.states)
        return {}


def state(fps,configuration):
    return {'configuration':configuration,'fpsLabel':str(fps),'resolutionLabel':'4K'}


class ControlTests(unittest.TestCase):
    def test_ps_paths_and_header(self):
        self.assertEqual(control.parse_processes('PID COMM\n 12 /Applications/Camera.app/Camera\n13 /a path/X\n'),
            [{'pid':12,'executable':'/Applications/Camera.app/Camera'},{'pid':13,'executable':'/a path/X'}])

    def test_remote_argv_is_quoted_not_interpreted_locally(self):
        phone=control.Phone(control.parser().parse_args(['ssh','--','printf','%s','$(touch /tmp/should-not-exist); value']))
        with patch.object(control.subprocess,'check_output',return_value=b'') as mock:
            phone.remote(['printf','%s','$(touch /tmp/should-not-exist); value'])
        argv=mock.call_args.args[0]
        self.assertEqual(argv[-1],"printf %s '$(touch /tmp/should-not-exist); value'")
        self.assertNotIn('shell',mock.call_args.kwargs)

    def test_help_without_optional_frida(self):
        result=subprocess.run(['python3',str(ROOT/'iphone-control'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('set-format',subprocess.check_output(['python3',str(ROOT/'iphone-control'),'camera','--help'],text=True))

    def test_format_reaches_60_through_ui_labels(self):
        phone=FormatPhone([state(24,10),state(30,5),state(60,9)])
        with patch.object(control.time,'sleep'):
            result=control.set_format(phone,'4k',60)
        self.assertEqual(result['after']['fpsLabel'],'60')
        self.assertEqual(phone.actions.count('camera.fps-next'),2)

    def test_unsupported_rate_reports_current_and_original(self):
        phone=FormatPhone([state(24,10),state(30,5)])
        with patch.object(control.time,'sleep'),self.assertRaisesRegex(RuntimeError,'Original='):
            control.set_format(phone,'4k',60)
        self.assertLess(phone.actions.count('camera.fps-next'),12)

    def test_numeric_enum_is_not_mistaken_for_fps(self):
        value=state(24,10)|{'framerateEnum':60}
        self.assertFalse(control.format_matches(value,'4k',60))

    def test_bad_ports_and_timeout_rejected(self):
        with patch('sys.stderr',io.StringIO()):
            for flags in (['--port','0'],['--timeout','nan'],['--timeout','0'],['--port','65536']):
                with self.assertRaises(SystemExit):control.parser().parse_args(flags+['status'])

    def test_screenshot_parser_never_exports_paths_to_rpc(self):
        args=control.parser().parse_args(['screenshot','/tmp/screen.png'])
        parameters={key:getattr(args,key) for key in ('bundle_id','max_nodes','identifier','label','class_name','class_methods','matching','name') if hasattr(args,key)}
        self.assertEqual(json.dumps(parameters),'{}')

    def test_explicit_identity_and_socket(self):
        args=control.parser().parse_args(['--identity','/tmp/key','--control-path','/tmp/socket','status'])
        argv=control.Phone(args).ssh_base()
        self.assertIn('/tmp/key',argv);self.assertIn('/tmp/socket',argv)
        self.assertIn('BatchMode=yes',argv)
        self.assertNotIn('BatchMode=yes',control.Phone(args).ssh_base(interactive=True))

    def test_missing_process_stops_before_frida_attach(self):
        args=control.parser().parse_args(['camera','status'])
        # Resolve the optional module to a fake here; no real Frida/device needed.
        class FakeFrida:__version__='test'
        with patch.object(control,'frida',FakeFrida()),patch.object(control.Phone,'processes',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'Expected one running'):
                control.Phone(args).connect('Camera')

    def test_finally_detaches_after_rpc_failure(self):
        args=control.parser().parse_args(['status'])
        with patch.object(control.Phone,'connect'),patch.object(control.Phone,'rpc',side_effect=RuntimeError('failure')),patch.object(control.Phone,'close') as close:
            with self.assertRaisesRegex(RuntimeError,'failure'):control.run(args)
        close.assert_called_once()

    def test_worker_lifetime_has_no_ssh_timeout(self):
        args=control.parser().parse_args(['worker','start'])
        with patch.object(control.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as call:
            self.assertEqual(control.run(args),0)
        self.assertNotIn('timeout',call.call_args.kwargs)
        self.assertTrue(call.call_args.args[0][-1].endswith('duskie-camera start'))

    def test_environment_port_validated_like_cli(self):
        with patch.dict(control.os.environ,{'IPHONE_SSH_PORT':'0'}),patch('sys.stderr',io.StringIO()):
            with self.assertRaises(SystemExit):control.parser().parse_args(['status'])

    def test_argument_names_for_all_worker_actions(self):
        for action in (*control.WORKER_ACTIONS,'status'):
            self.assertEqual(control.parser().parse_args(['worker',action]).action,action)


if __name__=='__main__':unittest.main()
