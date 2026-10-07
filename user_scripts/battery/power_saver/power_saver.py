#!/usr/bin/env python3
"""Stateful Dusky power saver. Python 3.15+, systemd 262+, Linux/Hyprland only."""
import argparse
import fcntl
import json
import math
import os
import re
import select
# Defer transition-only dependencies for help, config checks and status queries.
lazy import shutil
import signal
lazy import subprocess
import sys
lazy import tempfile
import time
import tomllib
lazy import uuid
from pathlib import Path

if sys.version_info < (3, 15):
    sys.exit('Python 3.15+ is required; select the ISO runtime on PATH.')

DEFAULTS = frozendict(command_timeout=45.0, process_timeout=2.0, kill_timeout=1.0,
                     brightness_percent=1, external_brightness_percent=1,
                     volume_cap_percent=50, bluetooth=True, wifi=False, theme=False,
                     pause_media=True, ddc=True)
STOPPED = frozenset({'inactive', 'failed'})
# Restore priority: theme (5), TLP (10), ASUS (20), radios (25), brightness/audio
# (30), effects (35), animations/shader (40), units (50), standalone processes (60).
# Enable stops managers first and caps audio (24) before blocking radios.


class Error(Exception):
    pass


def table(value, allowed, where):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise Error(f'{where}: expected a table with keys {sorted(allowed)}')


def strings(value, where):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v or '\0' in v for v in value):
        raise Error(f'{where}: expected an array of nonempty strings without NUL')
    return list(dict.fromkeys(value))


def unit_name(value):
    if not re.fullmatch(r'[A-Za-z0-9_:>@.\\-]+', value) or value.startswith('-'):
        raise Error(f'Expected an exact systemd unit name: {value!r}')
    return value if '.' in value.rsplit('@', 1)[-1] else value + '.service'


def config(path):
    with path.open('rb') as stream:
        raw = tomllib.load(stream)
    table(raw, {'settings', 'targets', 'integrations', 'hooks'}, 'configuration')
    settings = raw.get('settings', {})
    table(settings, set(DEFAULTS), 'settings')
    settings = dict(DEFAULTS) | settings
    for key, default in DEFAULTS.items():
        value = settings[key]
        if type(default) is bool:
            if type(value) is not bool:
                raise Error(f'settings.{key}: expected a boolean')
        elif key.endswith('_percent'):
            if type(value) is not int or not 0 <= value <= 100:
                raise Error(f'settings.{key}: expected an integer from 0 to 100')
        elif type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600:
            raise Error(f'settings.{key}: expected finite seconds from 0 (exclusive) to 3600')
    targets = raw.get('targets', {})
    table(targets, {'system_units', 'user_units', 'processes', 'scripts'}, 'targets')
    targets = {key: strings(targets.get(key, []), f'targets.{key}')
               for key in ('system_units', 'user_units', 'processes', 'scripts')}
    for key in ('system_units', 'user_units'):
        targets[key] = list(dict.fromkeys(map(unit_name, targets[key])))
    for value in targets['processes']:
        if '/' in value or len(os.fsencode(value)) > 15:
            raise Error(f'Process comm must be a basename of at most 15 bytes: {value!r}')
    for value in targets['scripts']:
        if '/' in value or any(ord(c) < 32 for c in value):
            raise Error(f'Script selector must be an entry-point basename: {value!r}')
    integrations = raw.get('integrations', {})
    table(integrations, {'tlp_script', 'visuals_script', 'theme_script'}, 'integrations')
    for key, value in integrations.items():
        if not isinstance(value, str) or not value or '\0' in value:
            raise Error(f'integrations.{key}: expected a path')
    integrations = {k: str(Path(v).expanduser().absolute()) for k, v in integrations.items()}
    hooks = raw.get('hooks', {})
    table(hooks, {'pre_enable', 'post_enable', 'pre_disable', 'post_disable'}, 'hooks')
    hooks = {key: strings(hooks.get(key, []), f'hooks.{key}')
             for key in ('pre_enable', 'post_enable', 'pre_disable', 'post_disable')}
    return settings, targets, integrations, hooks


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.power-saver-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stat_fields(pid):
    return Path(f'/proc/{pid}/stat').read_bytes().rsplit(b')', 1)[1].split()


def script_arg(argv, exe):
    interpreter = Path(exe).name
    python = re.fullmatch(r'(?:python|pypy)\d*(?:\.\d+)*', interpreter)
    shell = interpreter in ('bash', 'sh', 'dash', 'zsh')
    if not python and not shell:
        return ''
    args = iter(argv[1:])
    for arg in args:
        if arg == '--' or (interpreter == 'zsh' and arg == '-b'):
            return next(args, '')
        if arg == '-':
            return ''
        if arg.startswith('--'):
            if arg in ('--check-hash-based-pycs', '--rcfile', '--init-file'):
                next(args, None)
            continue
        if arg.startswith('-') or (shell and arg.startswith('+')):
            for index, option in enumerate(arg[1:], 1):
                if (python and option in 'cm') or (shell and arg[0] == '-' and option in 'cs'):
                    return ''
                if (python and option in 'WX') or (shell and option in 'oO'):
                    if index == len(arg) - 1:
                        next(args, None)
                    break
            continue
        return arg
    return ''


def processes(targets):
    """One procfs scan; exclude other users, ancestors, zombies and unrelated PIDs."""
    excluded = {1}
    pid = os.getpid()
    while pid > 1 and pid not in excluded:
        excluded.add(pid)
        try:
            pid = int(stat_fields(pid)[1])
        except (FileNotFoundError, ProcessLookupError):
            break
    result = []
    with os.scandir('/proc') as entries:
        for entry in entries:
            if not entry.name.isdecimal() or int(entry.name) in excluded:
                continue
            base = Path(entry.path)
            matched = False
            comm = ''
            try:
                if base.stat().st_uid != os.getuid():
                    continue
                pid = int(entry.name)
                fields = stat_fields(pid)
                if fields[0] in (b'Z', b'X'):
                    continue
                start = int(fields[19])
                comm = os.fsdecode((base / 'comm').read_bytes().removesuffix(b'\n'))
                argv = [os.fsdecode(a) for a in (base / 'cmdline').read_bytes().removesuffix(b'\0').split(b'\0')]
                if not argv or not argv[0]:
                    continue
                exe = os.readlink(base / 'exe')
                if comm not in targets['processes'] and Path(script_arg(argv, exe)).name not in targets['scripts']:
                    continue
                matched = True
                cwd = os.readlink(base / 'cwd')
                env = dict(os.fsdecode(v).split('=', 1) for v in
                           (base / 'environ').read_bytes().split(b'\0') if b'=' in v)
                # Preserve venv interpreter paths: canonical /proc/exe loses the venv.
                executable = argv[0]
                if '/' in executable:
                    executable = os.path.abspath(os.path.join(cwd, executable))
                else:
                    executable = shutil.which(executable, path=env.get('PATH', os.defpath)) or exe
                if not os.path.samefile(executable, base / 'exe'):
                    raise Error(f'PID {pid}: argv[0] does not identify its executable; use its owning unit')
                argv[0] = executable
                cgroup = (base / 'cgroup').read_text(encoding='utf-8')
                if int(stat_fields(pid)[19]) == start:
                    result.append(dict(pid=pid, start=start, comm=comm, argv=argv, cwd=cwd,
                                       env=env, cgroup=cgroup))
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as exc:
                if matched or comm in targets['processes']:
                    raise Error(f'Cannot capture matching process: {exc}') from exc
    return result


class Saver:
    def __init__(self, settings, targets, integrations, hooks, dry_run=False):
        self.settings, self.targets = settings, targets
        self.integrations, self.hooks = integrations, hooks
        self.dry_run = dry_run
        self.home = Path.home()
        # Keep the existing paths used by Dusky consumers.
        self.directory = self.home / '.config/dusky/settings/power_saver'
        self.path = self.directory / 'state.json'
        self.gui = self.directory.parent / 'power_saver_state'
        self.state = {'version': 1, 'phase': 'enabling', 'records': [], 'hooks': hooks,
                      'session': os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', ''),
                      'boot': Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()}
        self.errors = []

    def run(self, argv, root=False):
        if root:
            argv = ['sudo', '-n', '--', *argv]
        try:
            with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                  start_new_session=True,
                                  env=os.environ | {'LC_ALL': 'C', 'SYSTEMD_COLORS': '0',
                                                    'LIBSMARTCOLS_JSON': 'pretty'}) as child:
                try:
                    stdout, stderr = child.communicate(timeout=self.settings['command_timeout'])
                except BaseException:
                    # Shell integrations can have children: kill the whole command group.
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    child.communicate()
                    raise
                result = subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Error(f'{argv[0]}: {exc}; timed-out manager jobs may still be running') from exc
        if result.returncode:
            raise Error(f'{" ".join(argv[:5])}: {result.stderr.strip() or result.stdout.strip() or result.returncode}')
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        return result.stdout.strip()

    def attempt(self, label, function):
        try:
            function()
        except (Error, OSError, ValueError) as exc:
            self.errors.append(f'{label}: {exc}')
            print(f'FAILED: {label}: {exc}', file=sys.stderr)

    def save(self):
        if not self.dry_run:
            atomic_write(self.path, json.dumps(self.state, ensure_ascii=True) + '\n')

    def add(self, label, enable, restore, priority=30, root=False, **extra):
        record = dict(label=label, kind='command', enable=enable, restore=restore,
                      priority=priority, root=root, touched=False, **extra)
        self.state['records'].append(record)
        return record

    def units(self, kind, names):
        if not names:
            return {}
        output = self.run(['systemctl', *(['--user'] if kind == 'user' else []), '--no-pager',
                           'show', '--property=Id,LoadState,ActiveState', '--', *names])
        blocks = [dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
                  for block in output.split('\n\n') if block]
        if len(blocks) != len(names) or any('ActiveState' not in b or 'LoadState' not in b for b in blocks):
            raise Error(f'Incomplete {kind} systemctl response')
        return dict(zip(names, blocks, strict=True))

    def plan_units(self, kind):
        names = self.targets[kind + '_units']
        states = self.units(kind, names)
        active = [name for name in names if states[name]['LoadState'] != 'not-found'
                  and states[name]['ActiveState'] not in STOPPED]
        if active:
            present = [name for name in names if states[name]['LoadState'] != 'not-found']
            prefix = ['systemctl', *(['--user'] if kind == 'user' else []),
                      '--no-pager', '--no-ask-password']
            self.add(kind + ' units', [*prefix, 'stop', '--', *present],
                     [*prefix, 'start', '--', *active], 50, kind == 'system',
                     manager=kind, session=kind == 'user', names=active, stop_names=present)

    def plan_hardware(self, theme, wifi):
        script = self.integrations.get('tlp_script')
        if script and os.access(script, os.X_OK) and shutil.which('tlp-stat'):
            probe = json.loads(self.run([script, 'status', '--probe-json']))
            previous = probe['profile']
            if previous not in ('power-saver', 'balanced', 'performance') or probe['mode'] not in ('auto', 'manual'):
                raise Error('TLP profile/mode could not be captured')
            # Restore the selected profile, not a guessed performance/source default.
            if probe['mode'] == 'manual':
                raise Error('Manual TLP mode is not managed; leave it with the TLP toggle auto command first')
            restore = [script, previous]
            self.add('TLP', [script, 'power-saver'], restore, 10,
                     needs_sudo=True)
        elif shutil.which('tlp') and shutil.which('tlp-stat'):
            raw = self.run(['tlp-stat', '-m'])
            previous = raw.split('/', 1)[0].split()[0]
            if previous not in ('power-saver', 'balanced', 'performance'):
                raise Error('Unrecognized TLP profile')
            if '(manual)' in raw:
                raise Error('Manual TLP restore requires the configured TLP integration')
            self.add('TLP', ['tlp', 'power-saver'], ['tlp', previous], 10, True)
        if shutil.which('asusctl'):
            raw = self.run(['asusctl', 'profile', 'get'])
            match = re.search(r'Active profile:\s*(\w+)', raw)
            if not match:
                raise Error('Unrecognized asusctl profile response')
            available = self.run(['asusctl', 'profile', 'list'])
            if not re.search(r'\bQuiet\b', available, re.IGNORECASE):
                raise Error('ASUS Quiet profile is unavailable')
            self.add('ASUS profile', ['asusctl', 'profile', 'set', 'Quiet'],
                     ['asusctl', 'profile', 'set', match[1]], 20)
        if shutil.which('brightnessctl'):
            for row in self.run(['brightnessctl', '--list', '--machine-readable']).splitlines():
                name, cls, current, _, _maximum = row.split(',')
                if cls == 'backlight' or (cls == 'leds' and 'kbd_backlight' in name):
                    value = f"{self.settings['brightness_percent']}%" if cls == 'backlight' else '0'
                    prefix = ['brightnessctl', '--quiet', f'--class={cls}', f'--device={name}']
                    self.add('brightness ' + name, [*prefix, 'set', value], [*prefix, 'set', str(int(current))])
        if shutil.which('wpctl'):
            # Capture all sinks so blocking Bluetooth cannot expose an uncapped fallback.
            for row in self.run(['wpctl', 'list', 'audio', 'sinks']).splitlines():
                fields = row.split('\t')
                if len(fields) < 2 or not fields[0].isdecimal():
                    raise Error('Unrecognized wpctl sink list')
                info = self.run(['wpctl', 'inspect', fields[0]])
                name = re.search(r'node.name = "([^"\n]+)"', info)
                serial = re.search(r'object.serial = "(\d+)"', info)
                volume = self.run(['wpctl', 'get-volume', fields[0]])
                match = re.fullmatch(r'Volume:\s+([0-9]+(?:\.[0-9]+)?)(?:\s+\[MUTED\])?', volume)
                if not name or not serial or not match:
                    raise Error('Cannot identify audio sink/volume')
                if float(match[1]) > self.settings['volume_cap_percent'] / 100:
                    self.add('volume ' + name[1], [], [], kind_override='audio', node=name[1], serial=serial[1],
                             previous=match[1], cap=str(self.settings['volume_cap_percent'] / 100),
                             enable_priority=24)
        if shutil.which('rfkill') and (wifi or self.settings['bluetooth']):
            radios = json.loads(self.run(['rfkill', '--json', '--output', 'ID,TYPE,DEVICE,SOFT']))['rfkilldevices']
            for row in radios:
                if row['soft'] == 'unblocked' and ((row['type'] == 'bluetooth' and self.settings['bluetooth'])
                                                  or (row['type'] == 'wlan' and wifi)):
                    self.add('radio ' + row['device'], [], [], priority=25, root=True, kind_override='radio',
                             device=row['device'], radio_type=row['type'])
        if self.settings['ddc'] and shutil.which('ddcutil'):
            output = self.run(['ddcutil', 'detect', '--terse'])
            for block in re.split(r'\n\s*\n', output):
                if not re.prefixmatch(r'Display \d+', block.strip()):
                    continue
                bus = re.search(r'I2C bus:\s+/dev/i2c-(\d+)', block)
                if not bus:
                    continue
                # EDID, not display numbering or a persistent bus ID, identifies restore targets.
                edids = [p.read_bytes().hex() for p in Path('/sys/class/drm').glob('card*-*/edid')
                         if (p.parent / 'ddc').exists() and
                         (p.parent / 'ddc').resolve().name == 'i2c-' + bus[1]]
                edids = [e[:256] for e in edids if len(e) >= 256]
                if len(edids) != 1:
                    raise Error(f'Cannot identify DDC monitor on i2c-{bus[1]} by EDID')
                edid = edids[0]
                same = [p for p in Path('/sys/class/drm').glob('card*-*/edid')
                        if p.read_bytes().hex()[:256] == edid]
                if len(same) != 1:
                    raise Error('Monitor EDID is not unique; disable DDC in the configuration')
                value = self.run(['ddcutil', '--edid', edid, 'getvcp', '10', '--terse'])
                match = re.search(r'\bVCP 10 C (\d+) (\d+)\b', value)
                if not match:
                    raise Error('Unrecognized DDC brightness response')
                level = round(int(match[2]) * self.settings['external_brightness_percent'] / 100)
                prefix = ['ddcutil', '--edid', edid, '--verify', 'setvcp', '10']
                self.add('external brightness', [*prefix, str(level)], [*prefix, match[1]])
        if shutil.which('hyprctl') and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            animations = json.loads(self.run(['hyprctl', '-j', 'getoption', 'animations:enabled']))
            enabled = animations.get('bool')
            if type(enabled) is not bool:
                raise Error('Unrecognized Hyprland animation response')
            self.add('animations', ['hyprctl', 'keyword', 'animations:enabled', '0'],
                     ['hyprctl', 'keyword', 'animations:enabled', str(int(enabled))], 40, session=True)
            script = self.integrations.get('visuals_script')
            indicator = self.home / '.config/dusky/settings/opacity_blur'
            if script and os.access(script, os.X_OK):
                if not indicator.exists():
                    raise Error('Visuals indicator is missing; cannot capture the prior master switch')
                previous = indicator.read_text(encoding='utf-8').strip().lower()
                if previous not in ('true', 'false'):
                    raise Error('Unrecognized visuals indicator')
                if previous == 'true':
                    self.add('visual effects', [script, 'off'], [script, 'on'], 35, persistent=True)
            if shutil.which('hyprshade'):
                shader = self.run(['hyprshade', 'current'])
                if shader:
                    self.add('shader', ['hyprshade', 'off'], ['hyprshade', 'on', shader], 40, session=True)
        if theme:
            script = self.integrations.get('theme_script')
            if not script or not os.access(script, os.X_OK):
                raise Error('Theme integration is unavailable')
            state = (self.home / '.config/dusky/settings/dusky_theme/state.conf').read_text(encoding='utf-8')
            match = re.search(r'^THEME_MODE=[\'"]?(dark|light)[\'"]?\s*$', state, re.MULTILINE)
            if not match:
                raise Error('Cannot capture prior theme mode')
            self.add('theme', [script, 'set', '--mode', 'light'],
                     [script, 'set', '--mode', match[1]], 5, persistent=True)

    def hooks_run(self, name):
        for hook in self.state['hooks'][name]:
            self.attempt('hook ' + name, lambda h=hook: self.run(['bash', '-e', '-o', 'pipefail', '-c', h]))

    def action(self, record, restore=False):
        kind = record.get('kind_override', record['kind'])
        if kind == 'radio':
            rows = json.loads(self.run(['rfkill', '--json', '--output', 'ID,TYPE,DEVICE,SOFT']))['rfkilldevices']
            hits = [r for r in rows if r['device'] == record['device'] and r['type'] == record['radio_type']]
            if len(hits) != 1:
                raise Error('Saved radio is unavailable; restore retained')
            self.run(['rfkill', 'unblock' if restore else 'block', str(hits[0]['id'])], root=True)
        elif kind == 'audio':
            # Resolve the stable node name again: a radio restart can change PipeWire IDs.
            deadline = time.monotonic() + (min(10, self.settings['command_timeout']) if restore else 0)
            while True:
                output = self.run(['wpctl', 'list', 'audio', 'sinks'])
                ids = [row.split('\t')[0] for row in output.splitlines()
                       if len(row.split('\t')) >= 2 and row.split('\t')[1] == record['node']]
                if len(ids) == 1:
                    break
                if time.monotonic() >= deadline:
                    raise Error('Saved audio sink is unavailable; restore retained')
                time.sleep(0.1)
            info = self.run(['wpctl', 'inspect', ids[0]])
            if not restore and f'object.serial = "{record["serial"]}"' not in info:
                raise Error('Audio sink changed before volume cap')
            self.run(['wpctl', 'set-volume', ids[0], record['previous'] if restore else record['cap']])
        elif kind == 'process':
            self.restore_process(record)
        else:
            self.run(record['restore'] if restore else record['enable'],
                     root=record['root'])
            if 'manager' in record:
                states = self.units(record['manager'], record['names'] if restore else record['stop_names'])
                bad = [n for n, s in states.items() if s['LoadState'] == 'not-found' or
                       (s['ActiveState'] in STOPPED if restore else s['ActiveState'] not in STOPPED)]
                if bad:
                    raise Error('Unexpected unit state: ' + ', '.join(bad))

    def stop_processes(self):
        handles = []
        errors = []
        poller = select.poll()
        try:
            for proc in processes(self.targets):
                owned = [part for part in proc['cgroup'].strip().split('/')
                         if part.endswith('.service') and not part.startswith('user@')]
                if owned and not owned[-1].startswith('power-saver-restore-'):
                    errors.append(f'{proc["comm"]} PID {proc["pid"]} belongs to {owned[-1]}; configure its owning unit')
                    continue
                fd = None
                try:
                    fd = os.pidfd_open(proc['pid'])
                    if int(stat_fields(proc['pid'])[19]) != proc['start']:
                        os.close(fd)
                        continue
                    record = dict(label=f'{proc["comm"]} PID {proc["pid"]}', kind='process',
                                  priority=60, touched=True, process=proc,
                                  unit='power-saver-restore-' + uuid.uuid4().hex + '.service')
                    handles.append((fd, record))
                    poller.register(fd, select.POLLIN)
                    self.state['records'].append(record)
                    self.save()  # Persist restart data BEFORE signalling.
                    if owned:
                        self.run(['systemctl', '--user', 'stop', '--', owned[-1]])
                    else:
                        signal.pidfd_send_signal(fd, signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError):
                    if fd is not None and all(h != fd for h, _ in handles):
                        os.close(fd)
            pending = {fd for fd, _ in handles}
            for sig, grace in ((None, self.settings['process_timeout']), (signal.SIGKILL, self.settings['kill_timeout'])):
                if sig:
                    for fd in pending:
                        try:
                            signal.pidfd_send_signal(fd, sig)
                        except ProcessLookupError:
                            pass
                deadline = time.monotonic() + grace
                while pending and (remaining := deadline - time.monotonic()) > 0:
                    for fd, event in poller.poll(math.ceil(remaining * 1000)):
                        if event & (select.POLLIN | select.POLLHUP):
                            pending.discard(fd)
                            poller.unregister(fd)
            if pending:
                raise Error('Some processes did not exit after SIGKILL')
        finally:
            for fd, _ in handles:
                os.close(fd)
        if processes(self.targets):
            errors.append('Target processes remain or respawned; configure their owning units')
        if errors:
            raise Error('; '.join(errors))

    def restore_process(self, record):
        proc = record['process']
        # An interrupted run may have captured a process without stopping it.
        try:
            fields = stat_fields(proc['pid'])
            if int(fields[19]) == proc['start'] and fields[0] not in (b'Z', b'X'):
                return
        except (FileNotFoundError, ProcessLookupError):
            pass
        # A stable transient unit name makes launch retries idempotent.
        state = self.units('user', [record['unit']])[record['unit']]
        if state['LoadState'] != 'not-found' and state['ActiveState'] not in STOPPED:
            return
        slices = [part for part in proc.get('cgroup', '').strip().split('/')
                  if part in ('app.slice', 'session.slice', 'background.slice')]
        slice_name = slices[-1] if slices else ('session.slice' if proc.get('comm') in
                                               ('awww-daemon', 'waybar') else 'app.slice')
        score = {'session.slice': 100, 'app.slice': 200, 'background.slice': 300}[slice_name]
        argv = ['systemd-run', '--user', '--collect', '--quiet', '--service-type=exec',
                '--expand-environment=no', '--property=ExitType=cgroup', '--slice=' + slice_name,
                '--property=OOMScoreAdjust=' + str(score),
                '--property=ManagedOOMPreference=' + ('avoid' if slice_name == 'session.slice' else 'none'),
                '--unit=' + record['unit'],
                '--working-directory=' + proc['cwd']]
        argv.extend('--setenv=' + k + '=' + v for k, v in proc['env'].items())
        self.run([*argv, '--', *proc['argv']])

    def load(self):
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(self.state, dict) or self.state.get('version') != 1 or
                    self.state.get('phase') not in ('enabling', 'enabled', 'restoring') or
                    not isinstance(self.state.get('records'), list)):
                raise Error('Unsupported or malformed restore snapshot; left untouched')
            return True
        if any(self.directory.glob('*.state')):
            raise Error('Legacy Bash snapshot found; restore it with the previous Bash version before migrating')
        return False

    def transition(self, enable, theme=False, wifi=False):
        self.errors.clear()
        exists = self.load()
        if enable and exists:
            if self.state['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip():
                raise Error('Snapshot is from a previous boot; run --disable to finish persistent restores first')
            if self.state['phase'] == 'enabled':
                print('Power saver is already enabled; original snapshot preserved.')
                return 0
            raise Error('An incomplete transition exists; run --disable to restore before enabling again')
        if not enable and not exists:
            if not self.dry_run:
                atomic_write(self.gui, 'false')
            print('No restore snapshot; nothing to restore.')
            return 0
        if enable:
            # Capture hardware/service state first; capture processes after manager stops.
            self.plan_hardware(theme or self.settings['theme'], wifi or self.settings['wifi'])
            for kind in ('user', 'system'):
                self.plan_units(kind)
            if self.dry_run:
                for r in self.state['records']:
                    print('Would apply: ' + r['label'])
                for p in processes(self.targets):
                    print(f'Would stop: {p["comm"]} PID {p["pid"]}')
                for name in ('pre_enable', 'post_enable'):
                    for hook in self.hooks[name]:
                        print('Would run hook: ' + hook)
                return 0
        else:
            current_boot = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
            previous_boot = self.state['boot'] != current_boot
            if self.dry_run:
                for r in self.state['records']:
                    if r['touched']:
                        print('Would restore: ' + r['label'])
                return 0
        needs_sudo = any((enable or r['touched']) and (enable or not previous_boot)
                         and (r.get('root') or r.get('needs_sudo')) for r in self.state['records'])
        if needs_sudo:
            # Let sudo own the prompt; all subsequent calls are noninteractive.
            if subprocess.run(['sudo', '-v']).returncode:
                raise Error('Sudo authentication failed; no changes made')
        if enable:
            self.save()
            atomic_write(self.gui, 'true')
            self.hooks_run('pre_enable')
            # Stop service managers before direct processes; hardware snapshots already exist.
            for r in sorted(self.state['records'], key=lambda r: ('manager' not in r, r.get('enable_priority', r['priority']))):
                r['touched'] = True
                self.save()
                self.attempt(r['label'], lambda r=r: self.action(r))
            self.attempt('processes', self.stop_processes)
            if self.settings['pause_media'] and shutil.which('playerctl'):
                # No running players is normal; query before issuing pause.
                result = subprocess.run(['playerctl', '--list-all'], capture_output=True,
                                        timeout=self.settings['command_timeout'])
                if result.returncode == 0 and result.stdout.strip():
                    self.attempt('media pause', lambda: self.run(['playerctl', '--all-players', 'pause']))
            self.hooks_run('post_enable')
            if not self.errors:
                self.state['phase'] = 'enabled'
            self.save()
        else:
            self.state['phase'] = 'restoring'
            self.save()
            self.hooks_run('pre_disable')
            for r in sorted(self.state['records'].copy(), key=lambda r: r['priority']):
                if not r['touched']:
                    self.state['records'].remove(r)
                    self.save()
                    continue
                def restore(r=r):
                    session = self.state['session']
                    stale = previous_boot and not r.get('persistent')
                    stale |= (r.get('session') or r['kind'] == 'process') and session != os.environ.get('HYPRLAND_INSTANCE_SIGNATURE', '')
                    if stale:
                        print('Skipping prior boot/session runtime state: ' + r['label'])
                    else:
                        self.action(r, restore=True)
                    self.state['records'].remove(r)
                    self.save()
                self.attempt(r['label'], restore)
            self.hooks_run('post_disable')
            if not self.errors and not self.state['records']:
                # GUI first: a crash still leaves an empty, retryable snapshot.
                atomic_write(self.gui, 'false')
                self.path.unlink()
        if self.errors:
            print('Transition incomplete. Restore data retained; run --disable to retry.', file=sys.stderr)
            return 1
        print('Power saver enabled.' if enable else 'Previous state restored.')
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('-e', '--enable', action='store_true')
    mode.add_argument('-d', '--disable', action='store_true')
    mode.add_argument('-i', '--interactive', action='store_true')
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--check-config', action='store_true')
    parser.add_argument('-t', '--theme', action='store_true', help='apply light mode; restore the prior mode on disable')
    parser.add_argument('-w', '--wifi', action='store_true', help='block Wi-Fi on enable')
    parser.add_argument('--dry-run', action='store_true', help='query and report without writes, hooks, signals or sudo')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_suffix('.toml'))
    args = parser.parse_args()
    settings, targets, integrations, hooks = config(args.config.expanduser())
    if args.check_config:
        print('Configuration OK.')
        return 0
    if args.interactive:
        if args.dry_run:
            parser.error('--interactive cannot be combined with --dry-run')
        if not sys.stdin.isatty() or not shutil.which('gum'):
            raise Error('Interactive mode requires a terminal and gum')
        choice = subprocess.run(['gum', 'choose', '--', 'Enable Power Saver', 'Restore Previous State'],
                                stdout=subprocess.PIPE, text=True, encoding='utf-8')
        if choice.returncode in (1, 130):
            return 130
        if choice.returncode:
            raise Error(f'gum choose exited {choice.returncode}')
        args.enable = choice.stdout.strip() == 'Enable Power Saver'
        if args.enable:
            for option, prompt in (('theme', 'Switch to light theme?'), ('wifi', 'Turn off Wi-Fi?')):
                result = subprocess.run(['gum', 'confirm', '--', prompt])
                if result.returncode == 130:
                    return 130
                if result.returncode not in (0, 1):
                    raise Error(f'gum confirm exited {result.returncode}')
                setattr(args, option, getattr(args, option) or result.returncode == 0)
    elif not (args.enable or args.disable or args.status):
        parser.error('choose --enable, --disable, --interactive, --status or --check-config')
    saver = Saver(settings, targets, integrations, hooks, args.dry_run)
    if args.status:
        exists = saver.load()
        print(saver.state['phase'] if exists else 'disabled')
        return 0
    if args.dry_run:
        return saver.transition(args.enable, args.theme, args.wifi)
    saver.directory.mkdir(parents=True, exist_ok=True)
    with (saver.directory / '.lock').open('a', encoding='utf-8') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return saver.transition(args.enable, args.theme, args.wifi)


def interrupted(signum, _frame):
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, interrupted)
    try:
        sys.exit(main())
    except (Error, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Interrupted. Any saved restore data is retained.', file=sys.stderr)
        sys.exit(130)
