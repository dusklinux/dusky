"""Full installer scenarios with simulated package tools and real temporary file writes.

The backend rejects unexpected commands and file mutations outside its temporary root.
It never executes sudo, installs packages, or changes host configuration.
"""

import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from rich.console import Console

SCRIPT = Path(__file__).resolve().parents[1] / "gaming_setup.py"
spec = importlib.util.spec_from_file_location('pipeline_setup', SCRIPT)
s = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = s
spec.loader.exec_module(s)

class Backend:
    def __init__(self, root, *, fault=None, helper='paru', vendor='0x8086'):
        self.root = root
        self.home = root / 'home/player'
        self.data = self.home / 'share'
        self.vendor = vendor
        self.fault = fault
        self.helper = helper
        self.commands = []
        self.installed = set()
        self.apps = {'--system': set(), '--user': set()}
        self.runtimes = {'--system': set(), '--user': set()}
        self.real_run = subprocess.run
        self.real_which = shutil.which
        self.contexts = []
        self.log = io.StringIO()
        self.initial_conf = '[options]\nArchitecture=auto\n#Color\n#ParallelDownloads=5\n#[multilib]\n#Include = ' + str(root/'etc/mirrorlist') + '\n'
        self.write('etc/pacman.conf', self.initial_conf)
        self.write('etc/mirrorlist', 'Server = https://example.invalid/repo\n')
        self.write('etc/makepkg.conf', 'CFLAGS="-O2"\nCXXFLAGS="-O2"\n')
        (root/'etc/sysctl.d').mkdir()
        (root/'etc/security/limits.d').mkdir(parents=True)
        self.write('proc/sys/vm/max_map_count','2097152\n')
        pci = '0000:01:00.0'
        self.write(f'sys/bus/pci/devices/{pci}/class','0x030000\n')
        self.write(f'sys/bus/pci/devices/{pci}/vendor', vendor+'\n')
        self.write(f'sys/bus/pci/devices/{pci}/boot_vga','1\n')
        self.write('sys/module/nvidia_drm/parameters/modeset','Y\n')
        self.write('usr/lib/modules/7.3-test/pkgbase','linux\n')
        card=root/'sys/class/drm/card12';card.mkdir(parents=True)
        (card/'device').symlink_to(root/f'sys/bus/pci/devices/{pci}')
        self.write('sys/bus/pci/devices/0000:00:00.0/class','0x020000\n')
        self.home.mkdir(parents=True)
    def write(self, relative, text):
        path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8');return path
    def binary(self, name):
        path=self.write('bin/'+name,'#!/bin/sh\nexit 0\n');path.chmod(0o755)
    def path_type(self):
        root = self.root
        home = self.home
        prefixes=('/etc','/proc','/sys','/usr/lib/modules','/usr/share/dxvk','/usr/share/vkd3d-proton','/usr/share/vkd3d','/var/lib/flatpak','/var/lib/pacman')
        class FixturePath(Path):
            def __init__(self, *args):
                path=Path(*args)
                if any(str(path)==prefix or str(path).startswith(prefix+'/') for prefix in prefixes):
                    path=root/str(path).lstrip('/')
                super().__init__(path)
            @classmethod
            def home(cls):return cls(home)
        return FixturePath
    def which(self, name):
        if name in {'dwarfs','protonup-qt','pupgui2','flatpak'}:
            p=self.root/'bin'/name;return str(p) if p.exists() else None
        if name in {'paru','yay'}:return '/fake/'+name if self.helper==name else None
        return self.real_which(name)
    def translators(self, package):
        if package=='dxvk-bin':
            for arch in ('x32','x64'):
                for dll in ('d3d8.dll','d3d9.dll','d3d10core.dll','d3d11.dll','dxgi.dll'):
                    self.write('usr/share/dxvk/'+arch+'/'+dll,'fixture')
        if package=='vkd3d-proton-bin':
            for dll in ('d3d12.dll','d3d12core.dll'):self.write('usr/share/vkd3d-proton/x64/'+dll,'fixture')
    def run(self, argv, **kw):
        raw=list(argv);self.commands.append(raw)
        command=raw[1:] if raw[0]=='sudo' else raw
        if command[0]=='-n':command=command[1:]
        program=command[0]
        rc=0;out='';err=''
        fault = self.fault
        if (fault=='auth' and program=='-v' or fault=='pacman' and program=='pacman' and '-Syu' in command or
            fault=='gamemode' and program=='systemctl' or
            fault=='dwarfs' and program in {'paru','yay'} and command[-1].startswith('dwarfs') or
            fault=='flatpak-app' and program=='flatpak' and command[1]=='install' and command[-1]=='com.usebottles.bottles'):
            rc=1;err='injected failure'
        elif fault=='cancel' and program=='pacman' and '-Syu' in command:
            raise KeyboardInterrupt
        elif fault=='activate' and program=='mv':
            rc=1
        elif program in {'-v','notify-send'}:
            pass
        elif program in {'cp','install','mv','rm'}:
            mutated=command[-1:] if program in {'cp','install'} else command[1:]
            for arg in mutated:
                if arg.startswith('/'):assert Path(arg).is_relative_to(self.root),command
            return self.real_run(command, **kw)
        elif program=='pacman-conf':
            assert Path(command[command.index('--config')+1]).is_relative_to(self.root) or '/gaming-pacman-' in command[command.index('--config')+1]
            return self.real_run(command, **kw)
        elif program=='pacman' and command[1]=='-T':
            deps=command[3:] if command[2]=='--' else command[2:]
            missing=[x for x in deps if x not in self.installed]
            out='\n'.join(missing);rc=127 if missing else 0
        elif program=='pacman' and '-Syu' in command:
            deps=command[command.index('--')+1:];self.installed.update(deps)
            if 'nvidia-open-dkms' in deps:self.installed.add('NVIDIA-MODULE')
            if 'flatpak' in deps:self.binary('flatpak')
        elif program in {'paru','yay'}:
            for package in command[command.index('--')+1:] if '--' in command else [command[-1]]:
                self.installed.add(package)
                if package.startswith('dwarfs'):self.binary('dwarfs')
                elif package.startswith('protonup-qt'):self.binary('protonup-qt')
                else:self.translators(package)
        elif program=='lspci':
            out=f'0000:01:00.0 "VGA [0300]" "Vendor [{self.vendor.removeprefix("0x")}]" "Fixture GPU"\n'
        elif program=='vulkaninfo':
            out='Devices:\nGPU0:\n deviceName = Fixture GPU\n'
        elif program=='flatpak':
            scope='--user' if '--user' in command else '--system'
            action=command[1]
            if action=='list':
                values=self.runtimes[scope] if '--runtime' in command else self.apps[scope]
                out='\n'.join(ref.replace('//','\t') for ref in sorted(values))
            elif action=='info':out='[Extension org.freedesktop.Platform.VulkanLayer]\nversion=25.08\n'
            elif action=='install':
                app=command[-1]
                if 'VulkanLayer.' in app:self.runtimes[scope].add(app)
                else:
                    self.apps[scope].add(app);self.runtimes[scope].add('org.gnome.Platform//50')
                    export=self.root/'var/lib/flatpak/exports/share' if scope=='--system' else self.data/'flatpak/exports/share'
                    file=export/'applications'/f'{app}.desktop';file.parent.mkdir(parents=True,exist_ok=True);file.write_text('[Desktop Entry]\nName=Fixture\n',encoding='utf-8')
            else:assert action in {'update','override','remote-add'},command
        elif program=='sysctl':
            assert command[1]=='-p',command
            assert Path(command[-1]).is_relative_to(self.root),command
        elif program in {'systemctl','update-desktop-database','gtk-update-icon-cache'}:
            pass
        elif program==sys.executable:
            assert command[-1]=='desktop-all',command
        else:raise AssertionError(f'Unexpected subprocess: {command}')
        if kw.get('check') and rc:raise subprocess.CalledProcessError(rc,raw,output=out,stderr=err)
        return subprocess.CompletedProcess(raw,rc,out,err)
    def invoke(self, args, modules=None):
        original=s.SetupContext
        parse=s.parse_arguments
        def parse_selected():
            args, selected = parse()
            return args, modules if modules is not None else selected
        def context(*a,**kw):
            ctx=original(*a,**kw);self.contexts.append(ctx);return ctx
        with (
            patch.object(s, 'parse_arguments', side_effect=parse_selected),
            patch.object(s.enable_multilib_and_optimizations, '__defaults__', (self.root/'etc/pacman.conf',)),
            patch.object(s, 'Path', self.path_type()),
            patch.object(s.os, 'geteuid', return_value=1000),
            patch.dict(os.environ, {'HOME': str(self.home), 'XDG_DATA_HOME': str(self.data),
                                    'XDG_CONFIG_HOME': str(self.home/'config')}),
            patch.object(s.shutil, 'which', side_effect=self.which),
            patch.object(s.subprocess, 'run', side_effect=self.run),
            patch.object(s.time, 'sleep'),
            patch.object(s, 'console', Console(file=self.log)),
            patch.object(sys, 'stdout', self.log),
            patch.object(s, 'SetupContext', side_effect=context),
            patch.object(sys, 'argv', ['gaming_setup.py', '--yes', *args]),
        ):
            return s.main()

class PipelineTests(unittest.TestCase):
    def scenario(self, **kwargs):
        tmp=tempfile.TemporaryDirectory(prefix='gaming-pipeline-');self.addCleanup(tmp.cleanup);return Backend(Path(tmp.name),**kwargs)
    def assert_stopped(self,b):
        for ctx in b.contexts:
            self.assertTrue(ctx.stop_sudo_event.is_set())
            if ctx.sudo_thread:self.assertFalse(ctx.sudo_thread.is_alive())
    def test_fresh_full_install_vendor_and_helper_matrix(self):
        for vendor in ('0x8086','0x1002','0x10de','0x1af4'):
            for helper in ('paru','yay'):
                with self.subTest(vendor=vendor,helper=helper):
                    b=self.scenario(vendor=vendor,helper=helper);b.invoke([])
                    self.assert_stopped(b);self.assertFalse(b.contexts[0].failures)
                    self.assertTrue((b.root/'etc/pacman.conf').read_text(encoding='utf-8').count('[multilib]')==1)
                    self.assertIn('2097152',(b.root/'etc/sysctl.d/99-gaming.conf').read_text(encoding='utf-8'))
                    self.assertEqual(sum('-Syu' in c for c in b.commands),1)
                    self.assertIn('umu-launcher',b.installed)
                    self.assertIn('com.usebottles.bottles',b.apps['--system'])
                    self.assertTrue((b.data/'applications/com.usebottles.bottles.desktop').is_symlink())
                    if vendor=='0x10de':self.assertIn('linux-headers',b.installed)
    def test_native_build_and_config_helpers(self):
        for helper in ('paru','yay'):
            with self.subTest(helper=helper):
                b=self.scenario(helper=helper);b.invoke(['--native']);self.assert_stopped(b)
                native=[c for c in b.commands if c[0]==helper and c[-1]=='dwarfs']
                self.assertEqual(len(native),1)
                self.assertIn('--makepkgconf' if helper=='yay' else '--mflags',native[0])
    def test_full_dry_run_has_no_persistent_mutation(self):
        b=self.scenario();before={str(p.relative_to(b.root)):p.read_bytes() for p in b.root.rglob('*') if p.is_file()}
        b.invoke(['--dry-run'])
        after={str(p.relative_to(b.root)):p.read_bytes() for p in b.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after);self.assert_stopped(b)
        self.assertFalse(any(c[0]=='sudo' for c in b.commands))
    def test_critical_package_failure_stops_later_steps(self):
        b=self.scenario(fault='pacman')
        with self.assertRaises(RuntimeError):b.invoke([])
        self.assert_stopped(b);self.assertFalse(any(c[0] in {'paru','yay','flatpak','vulkaninfo'} for c in b.commands))
    def test_failed_activation_preserves_config(self):
        b=self.scenario(fault='activate')
        with self.assertRaises(RuntimeError):b.invoke([])
        self.assert_stopped(b);self.assertEqual((b.root/'etc/pacman.conf').read_text(encoding='utf-8'),b.initial_conf)
        self.assertFalse(list((b.root/'etc').glob('.*.tmp')))
    def test_cancellation_stops_keepalive_and_remaining_steps(self):
        b=self.scenario(fault='cancel')
        with self.assertRaises(KeyboardInterrupt):b.invoke([])
        self.assert_stopped(b)
    def test_optional_failure_finishes_with_nonzero_status(self):
        for fault in ('gamemode','dwarfs','flatpak-app'):
            with self.subTest(fault=fault):
                b=self.scenario(fault=fault)
                with self.assertRaises(SystemExit) as error:b.invoke([])
                self.assertEqual(error.exception.code,1);self.assert_stopped(b)
                self.assertTrue(b.contexts[0].failures)
                self.assertFalse(any('notify-send'==c[0] for c in b.commands))
    def test_missing_helper_records_unfulfilled_dwarfs_and_uses_flatpak_protonup(self):
        b=self.scenario(helper=None)
        with self.assertRaises(SystemExit):b.invoke([])
        self.assert_stopped(b);self.assertIn('net.davidotek.pupgui2',b.apps['--system'])
    def test_skip_flags_do_not_execute_disabled_steps(self):
        b=self.scenario();b.invoke(['--skip-gpu','--skip-wine','--skip-perf','--skip-flatpak','--skip-sysctl','--skip-dwarfs','--skip-protonup'])
        self.assert_stopped(b)
        self.assertTrue({'umu-launcher','wine-staging','gamemode'}.isdisjoint(b.installed))
        self.assertFalse(any(c[0] in {'vulkaninfo','systemctl','paru','yay'} or c[0]=='flatpak' and c[1]!='list' for c in b.commands))

    def test_source_and_flatpak_only_modes(self):
        for helper in ('paru','yay'):
            with self.subTest(helper=helper):
                b=self.scenario(helper=helper)
                modules=s.SelectedModules(dwarfs_mode='source', protonup_mode='flatpak')
                b.invoke([],modules=modules)
                self.assert_stopped(b);self.assertFalse(b.contexts[0].failures)
                self.assertIn('net.davidotek.pupgui2',b.apps['--user'])
                self.assertIn('dwarfs',b.installed)
                self.assertNotIn('protonup-qt-bin',b.installed)
    def test_repeat_install_and_user_scope_apps(self):
        b=self.scenario()
        b.apps['--user'].add('com.usebottles.bottles')
        b.runtimes['--user'].update({'org.gnome.Platform//50',
            'org.freedesktop.Platform.VulkanLayer.MangoHud//25.08',
            'org.freedesktop.Platform.VulkanLayer.gamescope//25.08'})
        b.invoke([])
        self.assertFalse(any(c[0]=='sudo' and 'flatpak' in c and 'override' in c and c[-1]=='com.usebottles.bottles' for c in b.commands))
        self.assertFalse(any(c[0]=='flatpak' and 'install' in c and 'VulkanLayer.' in c[-1] for c in b.commands))
        b.commands.clear();b.invoke([])
        self.assert_stopped(b);self.assertFalse(b.contexts[-1].failures)
        self.assertFalse(any(c[0] in {'paru','yay'} for c in b.commands))
    def test_auth_failure_happens_before_configuration(self):
        b=self.scenario(fault='auth')
        with self.assertRaises(SystemExit) as exit_status:b.invoke([])
        self.assertEqual(exit_status.exception.code,1)
        self.assertIsNone(b.contexts[0].sudo_thread)
        self.assertEqual((b.root/'etc/pacman.conf').read_text(encoding='utf-8'),b.initial_conf)

if __name__=='__main__':unittest.main(verbosity=2)
