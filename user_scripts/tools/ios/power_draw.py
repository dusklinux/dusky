#!/usr/bin/env python3
"""Read-only iPhone dashboard: local Python 3.15 + Rich + OpenSSH; phone Python 3.

Run with --configure for saved SSH/sampling options. Example:
  power_draw.py --minutes 4 --interval 20 --metrics cpu-temp,cpu,memory
  power_draw.py --readings 19 --baseline-watts 1.44 --label 'Audio on'

Battery sampling is always enabled. Extra metric groups may be disabled with
--metrics none. Configuration and JSONL/JSON reports live under
$XDG_CONFIG_HOME/dusky/settings/ssh_phone_reading (normally ~/.config/...).
No files are installed on the phone and no recording/settings commands are used.
CPU temperature uses the same PMGR SoC sensors as Duskie's CPUTemperature.m;
VM/counter layouts follow Apple's iOS SDK mach headers. Unsupported sensors are
reported as unavailable rather than replaced with a different temperature.
"""

import argparse
from datetime import datetime
from itertools import pairwise
import json
import math
import os
import select
import shlex
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from rich import box
from rich.console import Console, Group
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.prompt import Prompt, IntPrompt, FloatPrompt
from rich.table import Table
from rich.text import Text

CONSOLE = Console()
VALID_METRICS = frozenset({'cpu-temp', 'cpu', 'memory', 'none'})
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
STATE_DIR = (CONFIG_HOME if CONFIG_HOME.is_absolute() else Path.home() / '.config') / 'dusky/settings/ssh_phone_reading'
DEFAULTS = frozendict(host="", user="", port=22, identity="", interval=20.0,
                     readings=13, label="", baseline_watts=0.0, charge_window=40.0,
                     timeout=12.0, metrics='cpu-temp,cpu,memory')

# One persistent remote process, without installing files or changing phone settings.
# The remote Python may be older than the local Python 3.15 baseline.
REMOTE = r'''
import ctypes as C
import json, math, resource, sys, time

metrics = frozenset(filter(None, sys.argv[1].split(',')))
cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
P = C.c_void_p

def bind(lib, name, result, args):
    fn = getattr(lib, name)
    fn.restype = result
    fn.argtypes = args
    return fn

release = bind(cf, 'CFRelease', None, [P])
string = bind(cf, 'CFStringCreateWithCString', P, [P, C.c_char_p, C.c_uint32])
number_get = bind(cf, 'CFNumberGetValue', C.c_bool, [P, C.c_long, P])
boolean_get = bind(cf, 'CFBooleanGetValue', C.c_bool, [P])
dict_get = bind(cf, 'CFDictionaryGetValue', P, [P, P])
service_matching = bind(io, 'IOServiceMatching', P, [C.c_char_p])
service_get = bind(io, 'IOServiceGetMatchingService', C.c_uint32, [C.c_uint32, P])
properties = bind(io, 'IORegistryEntryCreateCFProperties', C.c_int, [C.c_uint32, P, P, C.c_uint32])
service_release = bind(io, 'IOObjectRelease', C.c_int, [C.c_uint32])

fields = {
    'CurrentCapacity': 'percent',
    'AppleRawCurrentCapacity': 'raw_mah',
    'NominalChargeCapacity': 'capacity_mah',
    'AppleRawMaxCapacity': 'raw_max_mah',
    'Voltage': 'voltage_mv',
    'InstantAmperage': 'current_ma',
    'Temperature': 'battery_c',
    'ExternalConnected': 'external_power',
    'UpdateTime': 'gauge_time',
}

# Mach ABI from Apple's XNU/iOS SDK: natural_t=uint32, counters=uint64.
# Free pages include speculative pages; never add speculative_count to free_count.
class VMStatistics(C.Structure):
    _fields_ = [(k, C.c_uint32) for k in ('free_count', 'active_count', 'inactive_count', 'wire_count')]
    _fields_ += [(k, C.c_uint64) for k in ('zero_fill_count', 'reactivations', 'pageins', 'pageouts', 'faults', 'cow_faults', 'lookups', 'hits', 'purges')]
    _fields_ += [(k, C.c_uint32) for k in ('purgeable_count', 'speculative_count')]
    _fields_ += [(k, C.c_uint64) for k in ('decompressions', 'compressions', 'swapins', 'swapouts')]
    _fields_ += [(k, C.c_uint32) for k in ('compressor_page_count', 'throttled_count', 'external_page_count', 'internal_page_count')]
    _fields_ += [('total_uncompressed_pages_in_compressor', C.c_uint64)]

owned = []
battery = 0
host = 0
task = 0
port_release = None
sensors = []
cpu_error = None
mach_error = None
previous_ticks = None

def own(ptr, message='CoreFoundation allocation failed'):
    if not ptr:
        raise RuntimeError(message)
    owned.append(ptr)
    return ptr

try:
    keys = {key: own(string(None, key.encode(), 0x08000100)) for key in fields}
    battery = service_get(0, service_matching(b'AppleSmartBattery'))
    if not battery:
        raise RuntimeError('AppleSmartBattery service unavailable')

    if 'cpu-temp' in metrics:
        try:
            data_create = bind(cf, 'CFDataCreate', P, [P, C.c_char_p, C.c_long])
            plist_create = bind(cf, 'CFPropertyListCreateWithData', P, [P, P, C.c_ulong, P, P])
            count = bind(cf, 'CFArrayGetCount', C.c_long, [P])
            item = bind(cf, 'CFArrayGetValueAtIndex', P, [P, C.c_long])
            cstring = bind(cf, 'CFStringGetCString', C.c_bool, [P, P, C.c_long, C.c_uint32])
            create = bind(io, 'IOHIDEventSystemClientCreate', P, [P])
            matching = bind(io, 'IOHIDEventSystemClientSetMatching', None, [P, P])
            services = bind(io, 'IOHIDEventSystemClientCopyServices', P, [P])
            prop = bind(io, 'IOHIDServiceClientCopyProperty', P, [P, P])
            event = bind(io, 'IOHIDServiceClientCopyEvent', P, [P, C.c_uint32, P, C.c_uint32])
            temperature = bind(io, 'IOHIDEventGetFloatValue', C.c_double, [P, C.c_uint32])
            payload = (
                b'<?xml version="1.0"?><plist version="1.0"><dict>'
                b'<key>PrimaryUsagePage</key><integer>65280</integer>'
                b'<key>PrimaryUsage</key><integer>5</integer>'
                b'</dict></plist>'
            )
            data = own(data_create(None, payload, len(payload)), 'Temperature interface unavailable')
            match = own(plist_create(None, data, 0, None, None), 'Temperature interface unavailable')
            client = own(create(None), 'Temperature interface unavailable')
            matching(client, match)
            array = own(services(client), 'Temperature interface unavailable')
            product = own(string(None, b'Product', 0x08000100), 'Temperature interface unavailable')
            buf = C.create_string_buffer(512)
            for index in range(count(array)):
                service = item(array, index)
                name = prop(service, product)
                if not name:
                    continue
                try:
                    if cstring(name, buf, len(buf), 0x08000100) and buf.value.startswith(b'PMGR SOC Die Temp Sensor'):
                        sensors.append(service)
                finally:
                    release(name)
            if not sensors:
                cpu_error = 'No accessible PMGR SoC die temperature sensors'
        except Exception as exc:
            cpu_error = str(exc)

    if metrics.intersection(('cpu', 'memory')):
        try:
            system = C.CDLL('/usr/lib/libSystem.B.dylib')
            task = C.c_uint32.in_dll(system, 'mach_task_self_').value
            port_release = bind(system, 'mach_port_deallocate', C.c_int, [C.c_uint32, C.c_uint32])
            vm_release = bind(system, 'mach_vm_deallocate', C.c_int, [C.c_uint32, C.c_uint64, C.c_uint64])
            processor_info = bind(system, 'host_processor_info', C.c_int, [C.c_uint32, C.c_int, P, P, P])
            vm_info = bind(system, 'host_statistics64', C.c_int, [C.c_uint32, C.c_int, P, P])
            page_size_get = bind(system, 'host_page_size', C.c_int, [C.c_uint32, P])
            sysctl = bind(system, 'sysctlbyname', C.c_int, [C.c_char_p, P, P, P, C.c_size_t])
            host_self = bind(system, 'mach_host_self', C.c_uint32, [])
            host = host_self()
            # vm_size_t is pointer-sized: the output buffer must be eight bytes on arm64.
            page_size = C.c_size_t()
            if page_size_get(host, C.byref(page_size)):
                raise RuntimeError('Cannot read page size')
            total_ram = C.c_uint64()
            length = C.c_size_t(C.sizeof(total_ram))
            if sysctl(b'hw.memsize', C.byref(total_ram), C.byref(length), None, 0):
                total_ram.value = 0
        except Exception as exc:
            mach_error = str(exc)
            if host and port_release:
                port_release(task, host)
                host = 0

    def read_system(row):
        global previous_ticks
        if mach_error:
            row['system_error'] = mach_error
            return
        if 'cpu' in metrics:
            cores = C.c_uint32()
            ticks = P()
            tick_count = C.c_uint32()
            result = processor_info(host, 2, C.byref(cores), C.byref(ticks), C.byref(tick_count))
            if result:
                row['cpu_usage_error'] = 'host_processor_info returned ' + str(result)
            elif ticks:
                try:
                    if tick_count.value != cores.value * 4:
                        row['cpu_usage_error'] = 'Unexpected CPU counter layout'
                    else:
                        current = list(C.cast(ticks, C.POINTER(C.c_uint32 * tick_count.value)).contents)
                        row['core_count'] = cores.value
                        row['cpu_cores_percent'] = [None] * cores.value
                        row['cpu_percent'] = None
                        if previous_ticks is not None and len(current) == len(previous_ticks):
                            busy_all = total_all = 0
                            for core in range(cores.value):
                                offset = core * 4
                                delta = [(current[offset + i] - previous_ticks[offset + i]) % 2**32 for i in range(4)]
                                total = sum(delta)
                                busy = total - delta[2]
                                if total:
                                    row['cpu_cores_percent'][core] = 100 * busy / total
                                busy_all += busy
                                total_all += total
                            if total_all:
                                row['cpu_percent'] = 100 * busy_all / total_all
                        previous_ticks = current
                finally:
                    vm_release(task, ticks.value, tick_count.value * C.sizeof(C.c_uint32))
        if 'memory' in metrics:
            vm = VMStatistics()
            size = C.c_uint32(C.sizeof(vm) // 4)
            result = vm_info(host, 4, C.byref(vm), C.byref(size))
            if result:
                row['memory_error'] = 'host_statistics64 returned ' + str(result)
            elif size.value < C.sizeof(vm) // 4:
                row['memory_error'] = 'Incomplete VM counter layout'
            else:
                mapping = {
                    'free': 'free_count',
                    'active': 'active_count',
                    'inactive': 'inactive_count',
                    'wired': 'wire_count',
                    'compressed': 'compressor_page_count',
                    'compressed_logical': 'total_uncompressed_pages_in_compressor',
                    'purgeable': 'purgeable_count',
                    'speculative': 'speculative_count',
                }
                row['memory'] = {key: getattr(vm, field) * page_size.value for key, field in mapping.items()}
                row['memory']['total'] = total_ram.value
                row['memory']['page_size'] = page_size.value

    for command in sys.stdin:
        if command.strip() != 'read':
            continue
        cpu_start = time.process_time()
        wall_start = time.monotonic()
        row = {'time': time.time(), 'cpu_c': None}
        dictionary = P()
        if properties(battery, C.byref(dictionary), None, 0) or not dictionary:
            raise RuntimeError('Cannot read battery service')
        try:
            for key, target in fields.items():
                pointer = dict_get(dictionary, keys[key])
                if not pointer:
                    continue
                if key == 'ExternalConnected':
                    value = bool(boolean_get(pointer))
                else:
                    value = C.c_longlong()
                    if not number_get(pointer, 4, C.byref(value)):
                        continue
                    value = value.value
                if key == 'Temperature':
                    value /= 100
                row[target] = value
        finally:
            release(dictionary)
        values = []
        for service in sensors:
            sample = event(service, 15, None, 0)
            if not sample:
                continue
            try:
                value = temperature(sample, 15 << 16)
                if math.isfinite(value) and 0 < value < 150:
                    values.append(value)
            finally:
                release(sample)
        if values:
            row['cpu_c'] = max(values)
        elif 'cpu-temp' in metrics:
            row['cpu_error'] = cpu_error or 'No current CPU temperature event'
        if not all(key in row for key in ('percent', 'voltage_mv', 'current_ma', 'battery_c', 'external_power')):
            raise RuntimeError('Battery telemetry fields unavailable')
        read_system(row)
        row['collector_cpu_ms'] = (time.process_time() - cpu_start) * 1000
        row['collector_wall_ms'] = (time.monotonic() - wall_start) * 1000
        row['collector_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(json.dumps(row, allow_nan=False), flush=True)
finally:
    if host and port_release:
        port_release(task, host)
    if battery:
        service_release(battery)
    for ptr in reversed(owned):
        if ptr:
            release(ptr)
'''


def normalize_metrics(raw_metrics):
    if not isinstance(raw_metrics, str):
        raise ValueError('metrics must be text')
    tokens = [part.strip().lower() for part in raw_metrics.split(',') if part.strip()]
    if not tokens:
        raise ValueError('Metrics must be comma-separated cpu-temp,cpu,memory or none.')
    seen = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    if 'none' in seen and len(seen) > 1:
        raise ValueError('Metric "none" cannot be combined with other metrics.')
    return ','.join(seen)


def is_real_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate(config):
    if not isinstance(config['host'], str) or not config['host'].strip() or config['host'].startswith('-'):
        raise ValueError('Enter a hostname, IP address or SSH config alias.')
    if any(c.isspace() for c in config['host']) or '@' in config['host']:
        raise ValueError('Enter the host and username in their separate fields.')
    for key in ('user', 'identity', 'label', 'metrics'):
        if not isinstance(config[key], str):
            raise ValueError(f'{key} must be text')
    if any(c.isspace() for c in config['user']) or config['user'].startswith('-'):
        raise ValueError('SSH username cannot contain whitespace or start with a hyphen.')
    if isinstance(config['port'], bool) or not isinstance(config['port'], int) or not 1 <= config['port'] <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    if isinstance(config['readings'], bool) or not isinstance(config['readings'], int) or config['readings'] < 1:
        raise ValueError('Readings must be a positive integer.')
    for key in ('interval', 'timeout', 'baseline_watts', 'charge_window'):
        if not is_real_number(config[key]):
            raise ValueError(f'{key} must be a finite number.')
    normalized = normalize_metrics(config['metrics'])
    unknown = set(normalized.split(',')) - VALID_METRICS
    if unknown:
        raise ValueError('Metrics must be comma-separated cpu-temp,cpu,memory or none.')
    config['metrics'] = normalized
    if config['interval'] < 1 or config['timeout'] < 1:
        raise ValueError('Interval and timeout must be at least one second.')
    if config['baseline_watts'] < 0 or not 0 < config['charge_window'] <= 100:
        raise ValueError('Baseline must be nonnegative; charge window must be 0–100%.')
    if config['identity']:
        identity = Path(config['identity']).expanduser().absolute()
        if not identity.is_file(): raise ValueError('SSH identity key file does not exist.')
        # Persist the location across working directories; retain symlink semantics.
        config['identity'] = str(identity)


def save_config(directory, config):
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.config-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(config, indent=2, allow_nan=False) + '\n')
        temporary.replace(directory / 'config.json')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def configure(config, *, minutes=None):
    config = dict(config)
    while True:
        CONSOLE.print(Panel('SSH aliases from ~/.ssh/config work too. Leave user/key blank to use OpenSSH defaults.\n'
                            'Enter - to clear a saved user/key. Passwords are entered through SSH; they are never saved.', title='Connection & sampling', border_style='cyan'))
        for key, title in [('host', 'SSH host / alias'), ('user', 'SSH username'), ('identity', 'Identity key path')]:
            config[key] = Prompt.ask(title, default=config[key]).strip()
            if key != 'host' and config[key] == '-': config[key] = ''
        config['port'] = IntPrompt.ask('SSH port', default=config['port'])
        config['interval'] = FloatPrompt.ask('Seconds between readings', default=config['interval'])
        suggested = config['readings']
        if minutes is not None and is_real_number(config['interval']) and config['interval'] >= 1:
            suggested = math.ceil(minutes * 60 / config['interval']) + 1
        config['readings'] = IntPrompt.ask('Number of readings (your entry wins; duration supplies the default)', default=suggested)
        config['label'] = Prompt.ask('Session label', default=config['label'])
        config['baseline_watts'] = FloatPrompt.ask('Compare with baseline watts (0 = off)', default=config['baseline_watts'])
        config['charge_window'] = FloatPrompt.ask('Battery percentage window for runtime estimate', default=config['charge_window'])
        config['metrics'] = Prompt.ask('Extra metrics: cpu-temp,cpu,memory (or none)', default=config['metrics']).strip().lower()
        config['timeout'] = FloatPrompt.ask('SSH reading timeout in seconds', default=config['timeout'])
        try:
            validate(config)
            return config
        except ValueError as exc:
            CONSOLE.print(str(exc), style='red', markup=False)


class Phone:
    def __init__(self, config, socket):
        self.config = config
        self.closed = False
        self.process = None
        self.errors = None
        self.base = [
            'ssh',
            '-o', f'ControlPath={socket}',
            '-o', 'ControlMaster=auto',
            '-o', 'ControlPersist=60',
            '-o', f'ConnectTimeout={math.ceil(config["timeout"])}',
            '-o', f'ServerAliveInterval={max(30, math.ceil(config["interval"] * 2))}',
            '-o', 'ServerAliveCountMax=2',
            '-p', str(config['port']),
        ]
        if config['user']:
            self.base += ['-l', config['user']]
        if config['identity']:
            self.base += ['-i', str(Path(config['identity']).expanduser())]
        self.target = config['host']

    def disconnect_error(self):
        # stdout EOF can precede the SSH client's last stderr write.
        if self.process is not None:
            try: self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired: pass
        if self.errors is not None:
            self.errors.seek(0)
            if message := self.errors.read().strip(): return message
        return 'Phone collector disconnected.'

    def connect(self):
        result = subprocess.run(self.base + ['-T', '--', self.target, 'true'], check=False)
        if result.returncode:
            raise RuntimeError(f'SSH login failed (exit {result.returncode}).')
        self.errors = tempfile.TemporaryFile(mode='w+t', encoding='utf-8', errors='replace')
        # Include rootless (/var/jb) and standard paths so non-login SSH finds python3 on palera1n/Procursus.
        command = (
            'export PATH="/var/jb/usr/bin:/var/jb/bin:/usr/local/bin:/usr/bin:/bin:$PATH"; '
            'exec python3 -u -c ' + shlex.quote(REMOTE) + ' ' + shlex.quote(self.config['metrics'])
        )
        self.process = subprocess.Popen(
            self.base + ['-T', '-o', 'BatchMode=yes', '--', self.target, command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errors,
            bufsize=0,
        )

    def read(self, on_wait=None):
        requested = time.monotonic()
        try:
            self.process.stdin.write(b'read\n')
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(self.disconnect_error()) from exc
        payload = bytearray()
        deadline = requested + self.config['timeout']
        next_refresh = requested
        while b'\n' not in payload:
            now = time.monotonic()
            if on_wait is not None and now >= next_refresh:
                on_wait()
                next_refresh = time.monotonic() + 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Reading timed out; the phone may be asleep or disconnected.')
            try:
                ready, _, _ = select.select([self.process.stdout], [], [], min(1.0, remaining))
                if not ready: continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except OSError as exc:
                raise RuntimeError(self.disconnect_error()) from exc
            if not chunk:
                raise RuntimeError(self.disconnect_error())
            payload.extend(chunk)
            if len(payload) > 65536:
                raise RuntimeError('Oversized phone response.')
        try:
            row = json.loads(payload)
            for key in ('time', 'percent', 'voltage_mv', 'current_ma', 'battery_c'):
                if not is_real_number(row[key]):
                    raise ValueError(f'Invalid {key}')
            if type(row['external_power']) is not bool:
                raise ValueError('Invalid external-power flag')
            if row.get('cpu_c') is not None and not is_real_number(row['cpu_c']):
                raise ValueError('Invalid CPU temperature')
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f'Invalid phone response: { bytes(payload[:200])!r}') from exc
        row['received_monotonic'] = time.monotonic()
        row['request_ms'] = (row['received_monotonic'] - requested) * 1000
        row['power_w'] = -row['current_ma'] * row['voltage_mv'] / 1_000_000
        if not math.isfinite(row['power_w']): raise RuntimeError('Invalid calculated battery power.')
        return row

    def close(self):
        if self.closed:
            return
        self.closed = True
        process = self.process
        self.process = None
        if process is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            try:
                process.stdout.close()
            except OSError:
                pass
        if self.errors is not None:
            try:
                self.errors.close()
            except OSError:
                pass
            self.errors = None
        try:
            subprocess.run(self.base + ['-O', 'exit', '--', self.target], capture_output=True, timeout=3, check=False)
        except (subprocess.TimeoutExpired, OSError):
            pass


def summarize(rows, interval):
    energy = charge = covered = 0.0
    pairs = 0
    for a, b in pairwise(rows):
        dt = b['received_monotonic'] - a['received_monotonic']
        if (a['segment'] != b['segment'] or not 0 < dt <= interval * 2.5
                or a['external_power'] or b['external_power']
                or a['current_ma'] > 0 or b['current_ma'] > 0):
            continue
        energy += (a['power_w'] + b['power_w']) / 2 * dt
        charge += (-a['current_ma'] - b['current_ma']) / 2 * dt / 3600
        covered += dt
        pairs += 1
    return dict(mean_w=energy / covered if covered else None,
                mean_ma=charge * 3600 / covered if covered else None,
                energy_wh=energy / 3600, consumed_mah=charge,
                covered_seconds=covered, integrated_intervals=pairs, readings=len(rows))


def elapsed(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def number(value, suffix='', digits=2):
    return '—' if value is None else f'{value:.{digits}f}{suffix}'


def history(rows, limit=None):
    table = Table(box=box.SIMPLE, expand=True, header_style='bold cyan')
    for name in ('Time', 'Battery', 'Draw', 'Current', 'CPU/SoC', 'Battery °C', 'Source'):
        table.add_column(name, justify='right' if name != 'Source' else 'left', no_wrap=True)
    for row in (rows[-limit:] if limit else rows):
        table.add_row(elapsed(row['elapsed']), f'{row["percent"]}%', number(row['power_w'], ' W'),
                      number(-row['current_ma'], ' mA', 0), number(row.get('cpu_c'), '°C'),
                      number(row['battery_c'], '°C'), 'External' if row['external_power'] else 'Battery')
    return table


def sparkline(rows):
    values = [r['power_w'] for r in rows[-60:]]
    if not values: return 'Waiting for first reading'
    low, high = min(values), max(values)
    blocks = '▁▂▃▄▅▆▇█'
    graph = ''.join(blocks[round((v-low)/(high-low)*7)] if high > low else '▄' for v in values)
    return f'{graph}  {low:.2f}–{high:.2f} W'


def system_panel(row, compact=False):
    if compact:
        lines = []
        if 'cpu_cores_percent' in row:
            lines.append('CPU ' + number(row.get('cpu_percent'), '%', 1) + '  ·  ' +
                         '  '.join(f'C{i} {number(v, "%", 1)}' for i, v in enumerate(row['cpu_cores_percent'])))
        if 'memory' in row:
            memory = row['memory']
            lines.append('RAM MiB  ·  ' + '  ·  '.join(f'{key} {memory[key]/2**20:,.0f}'
                                                     for key in ('free', 'active', 'inactive', 'wired')))
            lines.append(f'Compressed {memory["compressed"]/2**20:,.1f} MiB physical / '
                         f'{memory["compressed_logical"]/2**20:,.1f} MiB logical')
        for key in ('system_error', 'memory_error', 'cpu_usage_error'):
            if row.get(key): lines.append(row[key])
        return Text('\n'.join(lines), style='cyan') if lines else None
    parts = []
    if 'cpu_cores_percent' in row:
        cores = Table.grid(expand=True, padding=(0, 1))
        cores.add_column(no_wrap=True)
        cores.add_column(ratio=1)
        cores.add_column(justify='right', no_wrap=True)
        for core, value in enumerate(row['cpu_cores_percent']):
            cores.add_row(f'Core {core}', ProgressBar(total=100, completed=value or 0, width=None,
                                                    complete_style='magenta'), number(value, '%', 1))
        parts.append(Panel(Group(Text('Overall CPU: ' + number(row.get('cpu_percent'), '%', 1)), cores,
                                 Text('— = awaiting counter change since previous reading.', style='dim')),
                           title='CPU · interval usage', border_style='magenta'))
    if 'memory' in row:
        memory = row['memory']
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(); table.add_column(justify='right')
        for key, label in [('total', 'Physical RAM'), ('free', 'Free (includes speculative)'),
                           ('active', 'Active'), ('inactive', 'Inactive'), ('wired', 'Wired'),
                           ('compressed', 'Compressed · physical'), ('compressed_logical', 'Compressed · logical'),
                           ('purgeable', 'Purgeable (overlaps other categories)')]:
            table.add_row(label, f'{memory[key] / 2**20:,.1f} MiB')
        parts.append(Panel(table, title='RAM · native VM counters', border_style='blue'))
    for key in ('system_error', 'memory_error', 'cpu_usage_error'):
        if row.get(key): parts.append(Text(row[key], style='yellow'))
    return Columns(parts, equal=True, expand=True) if parts else None


def dashboard(rows, config, status, started):
    stats = summarize(rows, config['interval'])
    grid = Table.grid(expand=True, padding=(0, 2))
    for _ in range(3): grid.add_column(ratio=1)
    last = rows[-1] if rows else {}
    grid.add_row(Panel(f'[bold cyan]{number(last.get("power_w"), " W")}[/]\nLive draw', border_style='cyan'),
                 Panel(f'[bold green]{number(stats["mean_w"], " W")}[/]\nSession average', border_style='green'),
                 Panel(f'[bold magenta]{number(last.get("cpu_c"), "°C")}[/]\nCPU / SoC hottest die', border_style='magenta'))
    battery = last.get('percent')
    progress = ProgressBar(total=config['readings'], completed=len(rows), complete_style='cyan', finished_style='green')
    footer = f'{len(rows)}/{config["readings"]} readings · {elapsed(time.monotonic()-started)} elapsed · {status} · Ctrl+C to finish'
    notes = 'Whole-phone estimate, not camera-only. CPU — means the sensor is unavailable.'
    if last.get('collector_cpu_ms') is not None:
        notes += f'\nCollector: {last["collector_cpu_ms"]:.2f} ms CPU / reading; sleeps between requests.'
    if last.get('gauge_time'):
        notes += f'  Gauge age: {max(0, last["time"]-last["gauge_time"]):.0f}s.'
    if last.get('cpu_error'): notes += '\n' + last['cpu_error']
    if last.get('external_power'): notes += '\nExternal power: excluded from battery-power averages.'
    if rows and time.monotonic() - last['received_monotonic'] > config['interval'] + config['timeout']:
        notes += '\nSTALE: no recent reading.'
    title = Text(f'PHONE POWER  •  {config["label"] or config["host"]}', style='bold cyan')
    compact = CONSOLE.height < 38
    body = [grid, Text(f'Battery: {battery if battery is not None else "—"}%  ·  '+sparkline(rows), style='cyan')]
    if view := system_panel(last, compact): body.append(view)
    body.extend([history(rows, 3 if compact else 5), progress, Text(footer, style='dim'), Text(notes, style='dim')])
    return Panel(Group(*body), title=title, border_style='bright_blue', padding=(1, 2))


def final_report(rows, config, path, reason):
    stats = summarize(rows, config['interval'])
    # A terminal can disappear on SIGHUP; save before attempting any rendering.
    summary_path = path.with_suffix('.summary.json')
    summary_path.write_text(json.dumps(dict(config=config, summary=stats, reason=reason),
                                       indent=2, allow_nan=False) + '\n', encoding='utf-8')
    CONSOLE.print(Panel(Text(reason), title='Session complete', border_style='cyan'))
    CONSOLE.print(history(rows))
    if rows and (view := system_panel(rows[-1])):
        CONSOLE.print(view)
    CONSOLE.print(f'Average: [bold green]{number(stats["mean_w"], " W")}[/]  ·  '
                  f'Estimated use: {stats["consumed_mah"]:.1f} mAh / {stats["energy_wh"]:.3f} Wh  ·  '
                  f'Covered: {elapsed(stats["covered_seconds"])}')
    if rows:
        a, b = rows[0], rows[-1]
        CONSOLE.print(f'Battery: {a["percent"]}% → {b["percent"]}%  ·  '
                      f'CPU: {number(a.get("cpu_c"), "°C")} → {number(b.get("cpu_c"), "°C")}  ·  '
                      f'Battery temperature: {a["battery_c"]:.2f}°C → {b["battery_c"]:.2f}°C')
        capacity = b.get('capacity_mah') or b.get('raw_max_mah')
        if capacity and stats['mean_ma'] and stats['mean_ma'] > 0 and stats['covered_seconds'] >= 60:
            hours = capacity * config['charge_window'] / 100 / stats['mean_ma']
            CONSOLE.print(f'Rough runtime for a {config["charge_window"]:g} percentage-point window: '
                          f'[bold]{int(hours)}h {int(hours * 60) % 60:02d}m[/]')
    if config['baseline_watts'] and stats['mean_w'] is not None:
        change = (stats['mean_w'] / config['baseline_watts'] - 1) * 100
        CONSOLE.print(f'Observed difference from {config["baseline_watts"]:.2f} W baseline: {change:+.1f}%')
    cpu_costs = [r['collector_cpu_ms'] for r in rows if 'collector_cpu_ms' in r]
    if cpu_costs:
        mean_cpu_ms = sum(cpu_costs) / len(cpu_costs)
        CONSOLE.print(f'Collector sensor reads: {mean_cpu_ms:.2f} ms CPU/reading '
                      f'(~{mean_cpu_ms / (config["interval"] * 10):.3f}% of one core at this interval). '
                      'Excludes Python startup, JSON/SSH and Wi-Fi costs.', style='dim')
    rss = [r['collector_peak_rss_bytes'] for r in rows if 'collector_peak_rss_bytes' in r]
    if rss:
        CONSOLE.print(f'Collector peak resident memory: {max(rss)/2**20:.1f} MiB (Darwin getrusage).', style='dim')
    CONSOLE.print('Averages use actual sample spacing. Reconnects, long gaps and externally powered intervals are excluded.\n'
                  'Recording state is not inspected; idle readings remain included. Gauge updates may lag by ~20 seconds.', style='dim')
    CONSOLE.print('Readings: ' + str(path), markup=False)
    CONSOLE.print('Summary:  ' + str(summary_path), markup=False)


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, epilog='First run prompts for configuration. Ctrl+C saves a partial report.')
    for name in ('host', 'user', 'identity', 'label'):
        p.add_argument('--' + name)
    p.add_argument('--metrics', help='Extra metric groups: cpu-temp,cpu,memory or none for battery only (saved)')
    p.add_argument('--port', type=int)
    p.add_argument('--interval', type=float, help='Seconds between readings (saved; default 20)')
    count = p.add_mutually_exclusive_group()
    count.add_argument('--readings', type=int, help='Total readings, including initial baseline (saved)')
    count.add_argument('--minutes', type=float, help='Duration; rounded up to the next complete interval (saved as readings)')
    p.add_argument('--baseline-watts', type=float, help='Comparison baseline; 0 disables (saved)')
    p.add_argument('--charge-window', type=float, help='Percentage-point window for rough runtime (saved; default 40)')
    p.add_argument('--timeout', type=float, help='Maximum seconds for each SSH reading (saved)')
    p.add_argument('--configure', action='store_true', help='Edit saved settings before starting')
    p.add_argument('--config-dir', type=Path, default=STATE_DIR, help='Override state directory')
    return p


def main():
    arg_parser = parser()
    args = arg_parser.parse_args()
    if args.minutes is not None and (not is_real_number(args.minutes) or args.minutes <= 0
                                    or not math.isfinite(args.minutes * 60)):
        arg_parser.error('Minutes must be a positive finite number.')

    directory = args.config_dir.expanduser()
    config = dict(DEFAULTS)
    state = directory / 'config.json'
    if state.exists():
        try:
            stored = json.loads(state.read_text(encoding='utf-8'))
            if not isinstance(stored, dict):
                raise ValueError('Expected a JSON object')
            config.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (ValueError, OSError) as exc:
            CONSOLE.print(f'Could not load settings: {exc}', style='yellow', markup=False)
            if not sys.stdin.isatty():
                return 1
            args.configure = True

    for key in DEFAULTS:
        value = getattr(args, key, None)
        if value is not None:
            config[key] = value

    if args.configure or not config['host']:
        if not sys.stdin.isatty():
            CONSOLE.print('Specify --host or run interactively to configure SSH.', style='red')
            return 1
        # Correct invalid persisted value types before passing defaults to Rich prompts.
        for key, default in DEFAULTS.items():
            value = config[key]
            if key in ('port', 'readings'):
                if type(value) is not int: config[key] = default
            elif isinstance(default, str):
                if not isinstance(value, str): config[key] = default
            elif not is_real_number(value): config[key] = default
        config = configure(config, minutes=args.minutes)
    else:
        try:
            validate(config)
        except ValueError as exc:
            CONSOLE.print(str(exc), style='red', markup=False)
            return 1
        if args.minutes is not None:
            config['readings'] = math.ceil(args.minutes * 60 / config['interval']) + 1

    save_config(directory, config)
    rows = []
    reason = 'Finished requested readings.'
    reported = False
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    sessions = directory / 'sessions'
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / (stamp + '.jsonl')
    started = time.monotonic()
    segment = 0
    phone = None

    try:
        with tempfile.TemporaryDirectory(prefix='phone-power-') as temp, path.open('w', encoding='utf-8') as log:
            while len(rows) < config['readings']:
                try:
                    phone = Phone(config, str(Path(temp) / 'ssh'))
                    CONSOLE.print(f'Connecting to {config["host"]}…', markup=False, style='cyan')
                    phone.connect()
                    due = time.monotonic()
                    with Live(console=CONSOLE, auto_refresh=False, transient=True) as live:
                        while len(rows) < config['readings']:
                            while (now := time.monotonic()) < due:
                                live.update(
                                    dashboard(rows, config, f'next reading in {math.ceil(due - now)}s', started),
                                    refresh=True,
                                )
                                time.sleep(min(1.0, max(0.0, due - time.monotonic())))
                            row = phone.read(
                                on_wait=lambda: live.update(
                                    dashboard(rows, config, 'reading phone…', started), refresh=True
                                )
                            )
                            row['elapsed'] = row['received_monotonic'] - started
                            row['segment'] = segment
                            rows.append(row)
                            log.write(json.dumps(row, allow_nan=False) + '\n')
                            log.flush()
                            # Never burst requests to catch up after a delayed Wi-Fi response.
                            due = row['received_monotonic'] + config['interval']
                            if not CONSOLE.is_terminal:
                                CONSOLE.print(
                                    f'{elapsed(row["elapsed"])}  {row["power_w"]:.2f} W  '
                                    f'CPU {number(row.get("cpu_c"), "°C")}'
                                )
                except (RuntimeError, OSError) as exc:
                    if phone:
                        phone.close()
                    CONSOLE.print(str(exc), style='red', markup=False)
                    if isinstance(exc, OSError):
                        reason = 'Stopped after local I/O error; existing readings retained.'
                        break
                    if not sys.stdin.isatty():
                        reason = 'Stopped after connection or reading failure.'
                        break
                    action = Prompt.ask('Connection failed', choices=['retry', 'edit', 'finish'], default='retry')
                    if action == 'finish':
                        reason = 'Finished after connection failure.'
                        break
                    if action == 'edit':
                        # Keep sessions tied to one phone/configuration for meaningful averages.
                        final_report(rows, config, path, 'Ended before changing connection/configuration.')
                        reported = True
                        config = configure(config)
                        save_config(directory, config)
                        CONSOLE.print('Settings saved. Run the script again to start a fresh session.', style='cyan')
                        return 1
                    segment += 1
                finally:
                    if phone:
                        phone.close()
    except KeyboardInterrupt:
        reason = 'Interrupted; partial readings saved.'
    except OSError:
        reason = 'Stopped after local I/O error; existing readings retained.'
        raise
    finally:
        if phone:
            phone.close()
        if not reported:
            final_report(rows, config, path, reason)
    return 0 if len(rows) == config['readings'] else 1


def request_stop(_signum, _frame):
    # One interruption requests shutdown; repeated signals must not interrupt cleanup.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, signal.SIG_IGN)
    raise KeyboardInterrupt


if __name__ == '__main__':
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, request_stop)
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        try: CONSOLE.print(str(exc), style='red', markup=False)
        except OSError: pass  # The terminal may already have closed.
        raise SystemExit(1)
    except KeyboardInterrupt:
        CONSOLE.print('Configuration cancelled.', style='dim')
        raise SystemExit(130)
