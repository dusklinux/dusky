#!/usr/bin/env python3
"""Read-only iPhone telemetry. Pick what to watch; combine flags for more.

  power_draw.py --current            Current + battery charge/depletion ETA
  power_draw.py --cpu-temp           CPU temperature only
  power_draw.py --ram                RAM only
  power_draw.py --battery-temp       Battery temperature only
  power_draw.py --current --power    Current and power
  power_draw.py --all                Full dashboard

Metric flags watch live until Ctrl+C. Add --readings 1 for a single reading,
or --minutes 2 for a finite run. --configure saves connection/settings.
Battery power is net battery flow, not charger input. ETAs are host-calculated
constant-rate estimates; charging taper and workload changes affect accuracy.
Health is estimated from reported full/design capacity. Capacity is refreshed
at startup, source changes, long gaps and every five minutes for recalibration.
"""

import argparse
from collections import deque
from contextlib import nullcontext
from datetime import datetime
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
lazy from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
lazy from rich.prompt import Prompt, IntPrompt, FloatPrompt, Confirm
from rich.table import Table
from rich.text import Text

CONSOLE = Console()
VALID_METRICS = frozenset({'current', 'power', 'battery', 'battery-temp', 'health', 'voltage', 'cpu-temp', 'cpu', 'memory', 'none'})
BATTERY_METRICS = frozenset({'current', 'power', 'battery', 'battery-temp', 'health', 'voltage'})
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
STATE_DIR = (CONFIG_HOME if CONFIG_HOME.is_absolute() else Path.home() / '.config') / 'dusky/settings/ssh_phone_reading'
DEFAULTS = frozendict(host="", user="", port=22, identity="", interval=20.0,
                     readings=13, live=False, label="", baseline_watts=0.0, charge_window=40.0,
                     timeout=12.0, metrics='cpu-temp,cpu,memory', focus='')

# One persistent remote process, without installing files or changing phone settings.
# The remote Python may be older than the local Python 3.15 baseline.
REMOTE = r'''
import ctypes as C
import json, math, sys, time

metrics = frozenset(filter(None, sys.argv[1].split(',')))
profile = 'collector' in metrics
if profile:
    import resource
P = C.c_void_p

def bind(lib, name, result, args):
    fn = getattr(lib, name)
    fn.restype = result
    fn.argtypes = args
    return fn

battery_metrics = metrics.intersection(('current', 'power', 'battery', 'battery-temp', 'health', 'voltage'))
needs_metadata = bool(metrics.intersection(('current', 'power', 'health')))
if battery_metrics or 'cpu-temp' in metrics:
    cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
    release = bind(cf, 'CFRelease', None, [P])
    string = bind(cf, 'CFStringCreateWithCString', P, [P, C.c_char_p, C.c_uint32])
if battery_metrics:
    number_get = bind(cf, 'CFNumberGetValue', C.c_bool, [P, C.c_long, P])
    boolean_get = bind(cf, 'CFBooleanGetValue', C.c_bool, [P])
    service_matching = bind(io, 'IOServiceMatching', P, [C.c_char_p])
    service_get = bind(io, 'IOServiceGetMatchingService', C.c_uint32, [C.c_uint32, P])
    property_get = bind(io, 'IORegistryEntryCreateCFProperty', P, [C.c_uint32, P, P, C.c_uint32])
    service_release = bind(io, 'IOObjectRelease', C.c_int, [C.c_uint32])

fields = {}
if metrics.intersection(('current', 'power')): fields['InstantAmperage'] = 'current_ma'
if metrics.intersection(('power', 'voltage')): fields['Voltage'] = 'voltage_mv'
if 'battery-temp' in metrics: fields['Temperature'] = 'battery_c'
if 'battery' in metrics: fields['CurrentCapacity'] = 'percent'
if 'health' in metrics:
    fields['CurrentCapacity'] = 'percent'
    fields['AppleRawCurrentCapacity'] = 'raw_mah'
if needs_metadata: fields['ExternalConnected'] = 'external_power'
metadata_fields = {
    'CurrentCapacity': 'percent', 'AppleRawCurrentCapacity': 'raw_mah',
    'NominalChargeCapacity': 'capacity_mah', 'AppleRawMaxCapacity': 'raw_max_mah',
    'DesignCapacity': 'design_mah', 'CycleCount': 'cycle_count', 'UpdateTime': 'gauge_time',
} if needs_metadata else {}
previous_external = None

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
    keys = {key: own(string(None, key.encode(), 0x08000100)) for key in set(fields) | set(metadata_fields)}
    if battery_metrics:
        battery = service_get(0, service_matching(b'AppleSmartBattery'))
        if not battery:
            raise RuntimeError('AppleSmartBattery service unavailable')

    def read_property(key):
        ptr = property_get(battery, keys[key], None, 0)
        if not ptr:
            return None
        try:
            if key == 'ExternalConnected':
                return bool(boolean_get(ptr))
            value = C.c_longlong()
            if not number_get(ptr, 4, C.byref(value)):
                return None
            return value.value / 100 if key == 'Temperature' else value.value
        finally:
            release(ptr)

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
            if 'cpu' in metrics:
                vm_release = bind(system, 'mach_vm_deallocate', C.c_int, [C.c_uint32, C.c_uint64, C.c_uint64])
                processor_info = bind(system, 'host_processor_info', C.c_int, [C.c_uint32, C.c_int, P, P, P])
            host_self = bind(system, 'mach_host_self', C.c_uint32, [])
            host = host_self()
            if 'memory' in metrics:
                vm_info = bind(system, 'host_statistics64', C.c_int, [C.c_uint32, C.c_int, P, P])
                page_size_get = bind(system, 'host_page_size', C.c_int, [C.c_uint32, P])
                sysctl = bind(system, 'sysctlbyname', C.c_int, [C.c_char_p, P, P, P, C.c_size_t])
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
        if command.strip() not in ('read', 'read-meta'):
            continue
        if profile:
            cpu_start = time.process_time()
            wall_start = time.monotonic()
        row = {'time': time.time()}
        for key, target in fields.items():
            value = read_property(key)
            if value is not None:
                row[target] = value
        if needs_metadata:
            if 'external_power' not in row:
                raise RuntimeError('External-power state unavailable')
            if row['external_power'] != previous_external or command.strip() == 'read-meta':
                for key, target in metadata_fields.items():
                    value = row.get(target) if key in fields else read_property(key)
                    if value is not None:
                        row[target] = value
                row['metadata_refreshed'] = True
                previous_external = row['external_power']
        row['battery_properties_read'] = len(fields) + (len(set(metadata_fields)-set(fields)) if row.get('metadata_refreshed') else 0)
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
        if metrics.intersection(('cpu', 'memory')):
            read_system(row)
        if profile:
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


def selected_metrics(config):
    if config.get('focus'):
        return frozenset(config['focus'].split(','))
    return BATTERY_METRICS | (frozenset(config['metrics'].split(',')) - {'none'})


def is_real_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate(config):
    if not isinstance(config['host'], str) or not config['host'].strip() or config['host'].startswith('-'):
        raise ValueError('Enter a hostname, IP address or SSH config alias.')
    if any(c.isspace() for c in config['host']) or '@' in config['host']:
        raise ValueError('Enter the host and username in their separate fields.')
    for key in ('user', 'identity', 'label', 'metrics', 'focus'):
        if not isinstance(config[key], str):
            raise ValueError(f'{key} must be text')
    if any(c.isspace() for c in config['user']) or config['user'].startswith('-'):
        raise ValueError('SSH username cannot contain whitespace or start with a hyphen.')
    if isinstance(config['port'], bool) or not isinstance(config['port'], int) or not 1 <= config['port'] <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    if isinstance(config['readings'], bool) or not isinstance(config['readings'], int) or config['readings'] < 1:
        raise ValueError('Readings must be a positive integer.')
    if type(config['live']) is not bool:
        raise ValueError('Live mode must be true or false.')
    for key in ('interval', 'timeout', 'baseline_watts', 'charge_window'):
        if not is_real_number(config[key]):
            raise ValueError(f'{key} must be a finite number.')
    normalized = normalize_metrics(config['metrics'])
    unknown = set(normalized.split(',')) - VALID_METRICS
    if unknown:
        raise ValueError('Metrics must be comma-separated cpu-temp,cpu,memory or none.')
    config['metrics'] = normalized
    if config['focus'] and (set(config['focus'].split(',')) - (VALID_METRICS-{'none'})):
        raise ValueError('Unknown selected metric.')
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
        config['live'] = Confirm.ask('Live mode: run until Ctrl+C', default=config['live'])
        if not config['live']:
            config['readings'] = IntPrompt.ask('Number of readings (your entry wins; duration supplies the default)', default=suggested)
        config['label'] = Prompt.ask('Session label', default=config['label'])
        choice = Prompt.ask('Show: current,power,battery,battery-temp,health,voltage,cpu-temp,cpu,ram (or all)',
                            default=config['focus'] or 'all').strip().lower()
        if choice == 'all':
            config['focus'] = ''; config['metrics'] = DEFAULTS['metrics']
        elif choice == 'none':
            config['focus'] = ''; config['metrics'] = 'none'  # Legacy battery dashboard.
        else:
            aliases = {'ram':'memory', 'capacity':'health'}
            config['focus'] = ','.join(dict.fromkeys(aliases.get(part.strip(),part.strip()) for part in choice.split(',')))
        if not config['focus']:
            config['baseline_watts'] = FloatPrompt.ask('Compare with baseline watts (0 = off)', default=config['baseline_watts'])
            config['charge_window'] = FloatPrompt.ask('Battery percentage window for runtime estimate', default=config['charge_window'])
        config['timeout'] = FloatPrompt.ask('SSH reading timeout in seconds', default=config['timeout'])
        try:
            validate(config)
            return config
        except ValueError as exc:
            CONSOLE.print(str(exc), style='red', markup=False)


class BatteryEstimate:
    """Host-only capacity cache, coulomb integration and 60-second current smoothing."""
    KEYS = frozenset({'percent', 'raw_mah', 'capacity_mah', 'raw_max_mah', 'design_mah', 'cycle_count', 'gauge_time'})

    def __init__(self, interval):
        self.interval = interval
        self.info = {}
        self.remaining = self.rate = self.previous = None
        self.refresh_needed = False

    def update(self, row):
        now = row['received_monotonic']
        refreshed = row.get('metadata_refreshed') or not self.info
        if refreshed:
            self.info = {k: row[k] for k in self.KEYS if k in row}
            self.remaining = None
            self.previous = self.rate = None
            self.refresh_needed = False
        row.update({k: v for k, v in self.info.items() if k not in row})
        capacity = next((row[k] for k in ('raw_max_mah', 'capacity_mah')
                         if is_real_number(row.get(k)) and row[k] > 0), None)
        health_capacity = next((row[k] for k in ('capacity_mah', 'raw_max_mah')
                                if is_real_number(row.get(k)) and row[k] > 0), None)
        design = row.get('design_mah')
        if is_real_number(health_capacity) and is_real_number(design) and design > 0:
            row['health_percent'] = health_capacity / design * 100
        if refreshed and capacity and self.remaining is None:
            raw = row.get('raw_mah')
            if is_real_number(raw) and raw >= 0:
                self.remaining = min(capacity, raw)
            elif is_real_number(row.get('percent')) and 0 <= row['percent'] <= 100:
                self.remaining = capacity * row['percent'] / 100
        current = row.get('current_ma')
        row['eta_seconds'] = None
        if not is_real_number(current): return
        if self.previous:
            previous_time, previous_current, previous_source = self.previous
            dt = now - previous_time
            if previous_source != row.get('external_power') or not 0 < dt <= self.interval * 2.5:
                # Keep the last capacity estimate without integrating unknown
                # time. A delayed SSH response must not permanently erase ETA.
                self.rate = None
                self.refresh_needed = True
            else:
                if self.remaining is not None:
                    self.remaining += (previous_current + current) / 2 * dt / 3600
                if self.rate is not None and current * self.rate > 0:
                    self.rate += -math.expm1(-dt / 60) * (current - self.rate)
                else:
                    self.rate = current
        if self.rate is None: self.rate = current
        self.previous = now, current, row.get('external_power')
        if capacity and self.remaining is not None:
            self.remaining = max(0, min(capacity, self.remaining))
            row['estimated_remaining_mah'] = self.remaining
            if current and self.rate and current * self.rate > 0:
                amount = capacity - self.remaining if current > 0 else self.remaining
                row['eta_seconds'] = amount / abs(self.rate) * 3600
                row['eta_target'] = 'full' if current > 0 else 'empty'
                row['eta_rate_ma'] = abs(self.rate)


class Phone:
    def __init__(self, config, socket):
        self.config = config
        self.closed = False
        self.process = None
        self.errors = None
        self.metrics = selected_metrics(config)
        self.battery = BatteryEstimate(config['interval']) if self.metrics.intersection({'current', 'power', 'health'}) else None
        self.last_metadata = None
        self.last_received = None
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
            'exec python3 -u -c ' + shlex.quote(REMOTE) + ' ' + shlex.quote(','.join(sorted(
                self.metrics if self.config.get('focus') else self.metrics | {'collector'})))
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
            metadata_due = self.battery is not None and (self.battery.refresh_needed or
                (self.last_metadata is not None and requested - self.last_metadata >= 300))
            after_gap = self.battery is not None and self.last_received is not None and requested - self.last_received > self.config['interval'] * 2.5
            self.process.stdin.write(b'read-meta\n' if metadata_due or after_gap else b'read\n')
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
            if not isinstance(row, dict): raise ValueError('Expected a JSON object')
            if not is_real_number(row.get('time')): raise ValueError('Invalid timestamp')
            for key in ('percent', 'voltage_mv', 'current_ma', 'battery_c', 'raw_mah', 'capacity_mah', 'raw_max_mah', 'design_mah', 'cycle_count'):
                if key in row and not is_real_number(row[key]):
                    raise ValueError(f'Invalid {key}')
            if 'external_power' in row and type(row['external_power']) is not bool:
                raise ValueError('Invalid external-power flag')
            if row.get('cpu_c') is not None and not is_real_number(row['cpu_c']):
                raise ValueError('Invalid CPU temperature')
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f'Invalid phone response: { bytes(payload[:200])!r}') from exc
        row['received_monotonic'] = time.monotonic()
        row['request_ms'] = (row['received_monotonic'] - requested) * 1000
        if 'current_ma' in row:
            row['battery_flow'] = battery_flow(row)
            if 'voltage_mv' in row:
                row['signed_power_w'] = -row['current_ma'] * row['voltage_mv'] / 1_000_000
                row['power_w'] = abs(row['signed_power_w'])
                if not math.isfinite(row['power_w']) or row['voltage_mv'] <= 0:
                    raise RuntimeError('Invalid calculated battery power.')
        if self.battery is not None:
            self.battery.update(row)
        if row.get('metadata_refreshed'): self.last_metadata = row['received_monotonic']
        self.last_received = row['received_monotonic']
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


class Readings:
    """Session totals plus bounded display history; full samples stream to JSONL.

    len() counts the whole session; iteration/slices expose the recent 60 rows.
    """
    def __init__(self, interval):
        self.interval = interval
        self.recent = deque(maxlen=60)
        self.first = None
        self.count = self.pairs = self.cpu_count = 0
        self.energy = self.charge = self.covered = self.charge_covered = self.cpu_ms = 0.0
        self.peak_rss = 0

    def __len__(self):
        return self.count

    def __iter__(self):
        return iter(self.recent)

    def __getitem__(self, key):
        return list(self.recent)[key] if isinstance(key, slice) else self.recent[key]

    def append(self, row):
        if self.recent:
            self._integrate(self.recent[-1], row)
        else:
            self.first = row
        self.recent.append(row)
        self.count += 1
        if is_real_number(row.get('collector_cpu_ms')):
            self.cpu_ms += row['collector_cpu_ms']
            self.cpu_count += 1
        self.peak_rss = max(self.peak_rss, row.get('collector_peak_rss_bytes', 0))

    def _integrate(self, a, b):
        if not all(k in r for r in (a, b) for k in ('current_ma', 'external_power')):
            return
        dt = b['received_monotonic'] - a['received_monotonic']
        if (a['segment'] != b['segment'] or not 0 < dt <= self.interval * 2.5
                or a['external_power'] or b['external_power']
                or a['current_ma'] > 0 or b['current_ma'] > 0):
            return
        self.charge += (-a['current_ma'] - b['current_ma']) / 2 * dt / 3600
        self.charge_covered += dt
        if not all('power_w' in r for r in (a, b)):
            return
        self.energy += (a['power_w'] + b['power_w']) / 2 * dt
        self.covered += dt
        self.pairs += 1

    def summary(self):
        return dict(mean_w=self.energy / self.covered if self.covered else None,
                    mean_ma=self.charge * 3600 / self.charge_covered if self.charge_covered else None,
                    energy_wh=self.energy / 3600, consumed_mah=self.charge,
                    covered_seconds=self.covered, charge_covered_seconds=self.charge_covered,
                    integrated_intervals=self.pairs, readings=self.count)


def summarize(rows, interval):
    if isinstance(rows, Readings):
        return rows.summary()
    session = Readings(interval)
    for row in rows:
        session.append(row)
    return session.summary()


def battery_flow(row):
    current = row.get('current_ma')
    if current is None: return 'unavailable'
    return 'charging' if current > 0 else 'discharging' if current < 0 else 'idle'


def power_source(row):
    return ('External' if row['external_power'] else 'Battery') + ' · ' + battery_flow(row)


def elapsed(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def number(value, suffix='', digits=2):
    return '—' if value is None else f'{value:.{digits}f}{suffix}'


def history(rows, limit=None):
    table = Table(box=box.SIMPLE, expand=True, header_style='bold cyan')
    for name in ('Time', 'Battery', 'Power', 'Current', 'CPU/SoC', 'Battery °C', 'Source / flow'):
        table.add_column(name, justify='left' if name == 'Source / flow' else 'right', no_wrap=True)
    for row in (rows[-limit:] if limit else rows):
        table.add_row(elapsed(row['elapsed']), number(row.get('percent'), '%', 0), number(row.get('power_w'), ' W'),
                      number(abs(row['current_ma']) if 'current_ma' in row else None, ' mA', 0), number(row.get('cpu_c'), '°C'),
                      number(row.get('battery_c'), '°C'), power_source(row) if 'external_power' in row else 'Unavailable')
    return table


def sparkline(rows):
    values = [r['power_w'] for r in rows[-60:] if 'power_w' in r]
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
    flow = battery_flow(last).capitalize() if last else 'Waiting'
    grid.add_row(Panel(f'[bold cyan]{number(last.get("power_w"), " W")}[/]\nBattery · {flow}', border_style='cyan'),
                 Panel(f'[bold green]{number(stats["mean_w"], " W")}[/]\nBattery-only discharge average', border_style='green'),
                 Panel(f'[bold magenta]{number(last.get("cpu_c"), "°C")}[/]\nCPU / SoC hottest die', border_style='magenta'))
    battery = last.get('percent')
    live_mode = config.get('live', False)
    count = f'{len(rows)} readings · LIVE' if live_mode else f'{len(rows)}/{config["readings"]} readings'
    footer = f'{count} · {elapsed(time.monotonic()-started)} elapsed · {status} · Ctrl+C to finish'
    notes = 'Net battery flow; not charger input or plugged-in phone consumption. CPU — means sensor unavailable.'
    if last.get('collector_cpu_ms') is not None:
        notes += f'\nCollector: {last["collector_cpu_ms"]:.2f} ms CPU / reading; sleeps between requests.'
    if last.get('gauge_time'):
        notes += f'  Gauge age: {max(0, last["time"]-last["gauge_time"]):.0f}s.'
    if last.get('cpu_error'): notes += '\n' + last['cpu_error']
    if last.get('external_power'): notes += '\nExternal power: excluded from battery-power averages.'
    if 'current_ma' in last: notes += '\n' + number(abs(last['current_ma']), ' mA', 0) + ' ' + battery_flow(last) + ' · ' + eta_text(last)
    if 'health_percent' in last: notes += '\n' + battery_details(last)
    if rows and time.monotonic() - last['received_monotonic'] > config['interval'] + config['timeout']:
        notes += '\nSTALE: no recent reading.'
    title = Text(f'PHONE POWER  •  {config["label"] or config["host"]}', style='bold cyan')
    compact = CONSOLE.height < 38
    body = [grid, Text(f'Battery: {battery if battery is not None else "—"}%  ·  '+sparkline(rows), style='cyan')]
    if view := system_panel(last, compact): body.append(view)
    body.extend([history(rows, 3 if compact else 5), Text(footer, style='dim'), Text(notes, style='dim')])
    if not live_mode:
        body.append(ProgressBar(total=config['readings'], completed=len(rows), complete_style='cyan', finished_style='green'))
    return Panel(Group(*body), title=title, border_style='bright_blue', padding=(1, 2))


def eta_text(row):
    seconds = row.get('eta_seconds')
    if not is_real_number(seconds):
        return 'ETA — idle' if row.get('current_ma') == 0 else 'ETA unavailable'
    minutes = math.ceil(seconds / 60)
    return f'ETA ≈{minutes//60}h {minutes%60:02d}m to {row["eta_target"]} (at current rate)'


def battery_details(row):
    source = 'External power' if row.get('external_power') is True else 'On battery' if row.get('external_power') is False else 'Source unavailable'
    parts = [source, 'Battery ' + number(row.get('percent'), '%', 0),
             'remaining ' + number(row.get('raw_mah'), ' mAh', 0),
             'nominal full ' + number(row.get('capacity_mah'), ' mAh', 0),
             'gauge full ' + number(row.get('raw_max_mah'), ' mAh', 0),
             'design ' + number(row.get('design_mah'), ' mAh', 0),
             'health ≈' + number(row.get('health_percent'), '%', 1),
             'cycles ' + number(row.get('cycle_count'), '', 0)]
    return ' · '.join(parts)


def selected_line(row, config):
    parts = []
    for metric in config['focus'].split(','):
        if metric == 'current':
            parts.append(number(abs(row['current_ma']) if 'current_ma' in row else None, ' mA', 0)
                         + ' ' + (battery_flow(row) if 'current_ma' in row else 'unavailable') + ' · ' + eta_text(row))
        elif metric == 'power':
            parts.append('Power ' + number(row.get('power_w'), ' W') + ' ' + battery_flow(row))
            if 'current' not in config['focus'].split(','): parts.append(eta_text(row))
        elif metric == 'battery': parts.append('Battery ' + number(row.get('percent'), '%', 0))
        elif metric == 'battery-temp': parts.append('Battery ' + number(row.get('battery_c'), '°C'))
        elif metric == 'voltage': parts.append('Voltage ' + number(row.get('voltage_mv'), ' mV', 0))
        elif metric == 'health': parts.append(battery_details(row))
        elif metric == 'cpu-temp': parts.append('CPU/SoC ' + number(row.get('cpu_c'), '°C'))
        elif metric == 'cpu':
            parts.append('CPU ' + number(row.get('cpu_percent'), '%', 1))
            if row.get('cpu_cores_percent'):
                parts.append(' '.join(f'C{i} {number(v,"%",1)}' for i,v in enumerate(row['cpu_cores_percent'])))
        elif metric == 'memory':
            memory = row.get('memory')
            parts.append('RAM ' + (' · '.join(f'{k} {memory[k]/2**20:,.0f} MiB' for k in ('free','active','inactive','wired','compressed','total')) if memory else 'unavailable'))
    for key in ('cpu_error','system_error','cpu_usage_error','memory_error'):
        if row.get(key): parts.append(row[key])
    return datetime.fromtimestamp(row['time']).strftime('%H:%M:%S') + '  ' + ' · '.join(parts)


def final_report(rows, config, path, reason):
    stats = summarize(rows, config['interval'])
    # A terminal can disappear on SIGHUP; save before attempting any rendering.
    summary_path = path.with_suffix('.summary.json')
    summary_path.write_text(json.dumps(dict(config=config, summary=stats, reason=reason),
                                       indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if config.get('focus'):
        CONSOLE.print(f'Stopped · {len(rows)} readings · {reason}', markup=False)
        CONSOLE.print('Saved: ' + str(path), markup=False)
        return
    CONSOLE.print(Panel(Text(reason), title='Session complete', border_style='cyan'))
    if isinstance(rows, Readings) and len(rows) > len(rows.recent):
        CONSOLE.print(f'Latest {len(rows.recent)} of {len(rows)} readings below; the JSONL contains every reading.', style='dim')
    CONSOLE.print(history(rows))
    if rows and (view := system_panel(rows[-1])):
        CONSOLE.print(view)
    CONSOLE.print(f'Average: [bold green]{number(stats["mean_w"], " W")}[/]  ·  '
                  f'Estimated use: {stats["consumed_mah"]:.1f} mAh / {stats["energy_wh"]:.3f} Wh  ·  '
                  f'Covered: {elapsed(stats["covered_seconds"])}')
    if rows:
        a, b = rows.first if isinstance(rows, Readings) else rows[0], rows[-1]
        CONSOLE.print(f'Battery: {number(a.get("percent"), "%", 0)} → {number(b.get("percent"), "%", 0)}  ·  '
                      f'CPU: {number(a.get("cpu_c"), "°C")} → {number(b.get("cpu_c"), "°C")}  ·  '
                      f'Battery temperature: {number(a.get("battery_c"), "°C")} → {number(b.get("battery_c"), "°C")}')
        capacity = b.get('capacity_mah') or b.get('raw_max_mah')
        if capacity and stats['mean_ma'] and stats['mean_ma'] > 0 and stats['covered_seconds'] >= 60:
            hours = capacity * config['charge_window'] / 100 / stats['mean_ma']
            CONSOLE.print(f'Rough runtime for a {config["charge_window"]:g} percentage-point window: '
                          f'[bold]{int(hours)}h {int(hours * 60) % 60:02d}m[/]')
    if config['baseline_watts'] and stats['mean_w'] is not None:
        change = (stats['mean_w'] / config['baseline_watts'] - 1) * 100
        CONSOLE.print(f'Observed difference from {config["baseline_watts"]:.2f} W baseline: {change:+.1f}%')
    cpu_costs = [r['collector_cpu_ms'] for r in rows if 'collector_cpu_ms' in r] if not isinstance(rows, Readings) else []
    cpu_count = rows.cpu_count if isinstance(rows, Readings) else len(cpu_costs)
    if cpu_count:
        mean_cpu_ms = rows.cpu_ms / cpu_count if isinstance(rows, Readings) else sum(cpu_costs) / cpu_count
        CONSOLE.print(f'Collector sensor reads: {mean_cpu_ms:.2f} ms CPU/reading '
                      f'(~{mean_cpu_ms / (config["interval"] * 10):.3f}% of one core at this interval). '
                      'Excludes Python startup, JSON/SSH and Wi-Fi costs.', style='dim')
    peak_rss = rows.peak_rss if isinstance(rows, Readings) else max((r.get('collector_peak_rss_bytes', 0) for r in rows), default=0)
    if peak_rss:
        CONSOLE.print(f'Collector peak resident memory: {peak_rss/2**20:.1f} MiB (Darwin getrusage).', style='dim')
    CONSOLE.print('Power/current magnitudes show battery flow, not charger input. Charging uses a positive value with a direction label.', style='dim')
    CONSOLE.print('Averages use actual sample spacing. Reconnects, long gaps and externally powered intervals are excluded.\n'
                  'Recording state is not inspected; idle readings remain included. Gauge updates may lag by ~20 seconds.', style='dim')
    CONSOLE.print('Readings: ' + str(path), markup=False)
    CONSOLE.print('Summary:  ' + str(summary_path), markup=False)


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, epilog='First run prompts for configuration. Ctrl+C saves a partial report.')
    for name in ('host', 'user', 'identity', 'label'):
        p.add_argument('--' + name)
    for flag, metric in [('current','current'), ('power','power'), ('battery','battery'),
                         ('battery-temp','battery-temp'), ('health','health'), ('voltage','voltage'),
                         ('cpu-temp','cpu-temp'), ('cpu','cpu'), ('ram','memory')]:
        names = ['--'+flag]
        if flag == 'health': names.append('--capacity')
        if flag == 'ram': names.append('--memory')
        if flag == 'battery': names.append('--battery-percent')
        label = {'current':'Current in mA + charge/depletion ETA', 'battery':'Battery percentage', 'health':'Battery capacity, health and cycles', 'ram':'RAM', 'cpu':'CPU usage', 'cpu-temp':'CPU temperature'}.get(flag, flag.replace('-', ' ').capitalize())
        p.add_argument(*names, dest='select', action='append_const', const=metric,
                       help=label + ' only; live by default')
    p.add_argument('--all', action='store_true', help='Full dashboard and all sensors')
    p.add_argument('--metrics', help=argparse.SUPPRESS)  # Keep old commands working; advertise direct flags.
    p.add_argument('--port', type=int)
    p.add_argument('--interval', type=float, help='Seconds between readings (saved; default 20)')
    count = p.add_mutually_exclusive_group()
    count.add_argument('--readings', type=int, help='Total readings, including initial baseline (saved)')
    count.add_argument('--minutes', type=float, help='Duration; rounded up to the next complete interval (saved as readings)')
    count.add_argument('--live', action='store_true', default=None, help='Run until Ctrl+C; retry lost connections automatically (saved)')
    p.add_argument('--baseline-watts', type=float, help=argparse.SUPPRESS)
    p.add_argument('--charge-window', type=float, help=argparse.SUPPRESS)
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
    if args.readings is not None or args.minutes is not None:
        config['live'] = False
    if (args.select and (args.all or args.metrics is not None)) or (args.all and args.metrics is not None):
        arg_parser.error('Use metric flags together, or --all/--metrics, not both.')
    if args.select:
        config['focus'] = ','.join(dict.fromkeys(args.select))
        if args.readings is None and args.minutes is None:
            config['live'] = True
    elif args.all or args.metrics is not None:
        config['focus'] = ''
        if args.all:
            config['metrics'] = DEFAULTS['metrics']
            if args.readings is None and args.minutes is None: config['live'] = True


    if args.configure or not config['host']:
        if not sys.stdin.isatty():
            CONSOLE.print('Specify --host or run interactively to configure SSH.', style='red')
            return 1
        # Correct invalid persisted value types before passing defaults to Rich prompts.
        for key, default in DEFAULTS.items():
            value = config[key]
            if key == 'live':
                if type(value) is not bool: config[key] = default
            elif key in ('port', 'readings'):
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
    rows = Readings(config['interval'])
    reason = 'Finished requested readings.'
    reported = False
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    sessions = directory / 'sessions'
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / (stamp + '.jsonl')
    started = time.monotonic()
    segment = 0
    phone = None
    interrupted = False

    try:
        with tempfile.TemporaryDirectory(prefix='phone-power-') as temp, path.open('w', encoding='utf-8') as log:
            while config['live'] or len(rows) < config['readings']:
                try:
                    phone = Phone(config, str(Path(temp) / 'ssh'))
                    CONSOLE.print(f'Connecting to {config["host"]}…', markup=False, style='cyan')
                    phone.connect()
                    due = time.monotonic()
                    with (nullcontext(None) if config.get('focus') else Live(console=CONSOLE, auto_refresh=False, transient=True)) as live:
                        while config['live'] or len(rows) < config['readings']:
                            while (now := time.monotonic()) < due:
                                if live is not None: live.update(
                                    dashboard(rows, config, f'next reading in {math.ceil(due - now)}s', started),
                                    refresh=True,
                                )
                                remaining = max(0.0, due - time.monotonic())
                                time.sleep(min(1.0, remaining) if live is not None else remaining)
                            row = phone.read(
                                on_wait=(lambda: live.update(
                                    dashboard(rows, config, 'reading phone…', started), refresh=True
                                )) if live is not None else None
                            )
                            row['elapsed'] = row['received_monotonic'] - started
                            row['segment'] = segment
                            rows.append(row)
                            log.write(json.dumps(row, allow_nan=False) + '\n')
                            log.flush()
                            # Never burst requests to catch up after a delayed Wi-Fi response.
                            due = row['received_monotonic'] + config['interval']
                            if config.get('focus'):
                                if row.get('metadata_refreshed') and selected_metrics(config).intersection({'current','power'}):
                                    CONSOLE.print(battery_details(row), markup=False)
                                CONSOLE.print(selected_line(row, config), markup=False)
                            elif not CONSOLE.is_terminal:
                                CONSOLE.print(
                                    f'{elapsed(row["elapsed"])}  {number(row.get("power_w"), " W")}  {power_source(row) if "external_power" in row else "Source unavailable"}  '
                                    f'CPU {number(row.get("cpu_c"), "°C")}'
                                )
                except (RuntimeError, OSError) as exc:
                    if phone:
                        phone.close()
                    CONSOLE.print(str(exc), style='red', markup=False)
                    if isinstance(exc, OSError):
                        reason = 'Stopped after local I/O error; existing readings retained.'
                        break
                    if config['live']:
                        delay = max(5, min(config['interval'], 30))
                        CONSOLE.print(f'Reconnecting in {delay:g}s; Ctrl+C to finish.', style='yellow')
                        segment += 1
                        time.sleep(delay)
                        continue
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
        interrupted = True
    except OSError:
        reason = 'Stopped after local I/O error; existing readings retained.'
        raise
    finally:
        if phone:
            phone.close()
        if not reported:
            final_report(rows, config, path, reason)
    return 0 if (config['live'] and interrupted) or (not config['live'] and len(rows) == config['readings']) else 1


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
