#!/usr/bin/env python3
"""Read-only capture and explicitly opted-in, RTC-backed s2idle experiments.

No packages, mounts, persistent configuration, or service changes are made.
Only experiment modes write an RTC alarm and acquire transient inhibitors.
"""
import argparse
import contextlib
import datetime
import fcntl
import glob
import json
import os
import platform
import select
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
S0IX = '/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us'
PC10 = '/sys/devices/system/cpu/cpuidle/low_power_idle_cpu_residency_us'
MAX_DURATION = 900
PROBE_READY_TIMEOUT = 10
RAW_CLEANUP_MARGIN = 30
RAW_REOPEN_GRACE = 10
COMMAND_DEADLINE: float | None = None  # Absolute CLOCK_BOOTTIME deadline, set by an importing worker.


class SafetyError(RuntimeError):
    pass


def read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError) as exc:
        return {'error': str(exc)}


def number(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def command(argv, timeout=15):
    if COMMAND_DEADLINE is not None:
        remaining = COMMAND_DEADLINE - time.clock_gettime(time.CLOCK_BOOTTIME)
        if remaining <= 0:
            return {'argv': argv, 'error': 'CLOCK_BOOTTIME command deadline exceeded; subprocess not started',
                    'deadline_exceeded': True}
        timeout = remaining if timeout is None else min(timeout, remaining)
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                           env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'})
        return {'argv': argv, 'returncode': p.returncode, 'stdout': p.stdout,
                'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'argv': argv, 'error': str(exc)}


def dump(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def clocks():
    return {'wall': time.time(), 'boottime': time.clock_gettime(time.CLOCK_BOOTTIME),
            'monotonic': time.monotonic()}


def counter_delta(before, after, elapsed):
    """Do not interpret a reset/wrap as residency or guess a counter width."""
    before, after = number(before), number(after)
    if before is None or after is None:
        return {'status': 'unavailable', 'seconds': None, 'percent': None}
    if after < before:
        return {'status': 'reset_or_wrap', 'seconds': None, 'percent': None}
    seconds = (after - before) / 1_000_000
    return {'status': 'ok' if elapsed > 0 and seconds <= elapsed * 1.05 else 'implausible',
            'seconds': seconds, 'percent': seconds / elapsed * 100 if elapsed > 0 else None}


def interrupts(text):
    result = {}
    if not isinstance(text, str):
        return result
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, values = line.split(':', 1)
        counts = []
        for value in values.split():
            if not value.isdigit():
                break
            counts.append(int(value))
        if counts:
            result[key.strip()] = sum(counts)
    return result


def interrupt_deltas(before, after):
    a, b = interrupts(before), interrupts(after)
    return {key: {'delta': b[key] - a[key] if b[key] >= a[key] else None,
                  'status': 'ok' if b[key] >= a[key] else 'reset_or_wrap'}
            for key in a.keys() & b.keys()}


def raw_lids():
    """Independent fail-closed safety query; full enumeration belongs to lid_probe."""
    devices = []
    for event in sorted(Path('/sys/class/input').glob('event*')):
        cap = read(event / 'device/capabilities/sw')
        try:
            if not isinstance(cap, str):
                raise TypeError('unreadable capability')
            capable = bool(int(cap.split()[-1], 16) & 1)
        except (IndexError, TypeError, ValueError):
            devices.append({'device': str(event), 'error': 'cannot read switch capability'})
            continue
        if not capable:
            continue
        device = '/dev/input/' + event.name
        try:
            with open(device, 'rb', buffering=0) as stream:
                bits = bytearray(8)
                # Linux EVIOCGSW(8); SW_LID is bit zero of native unsigned long.
                fcntl.ioctl(stream.fileno(), 0x8008451B, bits, True)
                state = bool(int.from_bytes(bits, sys.byteorder) & 1)
                devices.append({'device': device, 'SW_LID': state,
                                'name': read(event / 'device/name')})
        except OSError as exc:
            devices.append({'device': device, 'error': str(exc)})
    return devices


def require_lid(devices, closed):
    if not devices or any(d.get('error') or d.get('SW_LID') is not closed for d in devices):
        raise SafetyError('Raw SW_LID is missing, unreadable, conflicting, or not ' +
                          ('closed' if closed else 'open'))


def probe_snapshot():
    return command([sys.executable, str(ROOT / 'lid_probe.py')])


def logind():
    return {name: command(['busctl', '--system', 'get-property', 'org.freedesktop.login1',
                          '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', name])
            for name in ('LidClosed', 'PreparingForSleep', 'Docked', 'OnExternalPower',
                         'HandleLidSwitch', 'HandleLidSwitchExternalPower', 'HandleLidSwitchDocked',
                         'HoldoffTimeoutUSec', 'InhibitDelayMaxUSec')
            }


def runtime_pm():
    result = {}
    # Bus devices include PCI/VMD/NVMe, I2C, HID, ISH, USB, platform and ACPI.
    for item in sorted(glob.glob('/sys/bus/*/devices/*')):
        device = Path(item)
        if not (device / 'power').is_dir():
            continue
        result[item] = {'realpath': str(device.resolve()),
                        'driver': str((device / 'driver').resolve()) if (device / 'driver').exists() else None,
                        'power': {key: read(device / 'power' / key) for key in
                                  ('control', 'runtime_status', 'runtime_active_time',
                                   'runtime_suspended_time', 'wakeup') if (device / 'power' / key).exists()}}
    return result


def directory_entries(root, errors):
    # Unlike glob/rglob, explicit enumeration does not silently hide access errors.
    try:
        return sorted(root.iterdir())
    except OSError as exc:
        errors[str(root)] = {'error': str(exc)}
        return []


def read_exports(root, names=None):
    result = {'files': {}, 'unavailable': {}}
    for path in directory_entries(root, result['unavailable']):
        if (names is None or path.name in names) and path.is_file():
            result['files'][str(path)] = read(path)
    return result


def acpi_lid_states(root=Path('/proc/acpi/button/lid')):
    result = {'files': {}, 'unavailable': {}}
    for directory in directory_entries(root, result['unavailable']):
        if directory.is_dir():
            lid = read_exports(directory, {'state'})
            result['files'].update(lid['files'])
            result['unavailable'].update(lid['unavailable'])
    return result


def acpi_lid_closed(value):
    if not isinstance(value, str):
        return None
    words = value.lower().split()
    if words == ['state:', 'closed']:
        return True
    if words == ['state:', 'open']:
        return False
    return None


def acpi_button_metadata(parameters=Path('/sys/module/button/parameters'),
                         devices=Path('/sys/bus/acpi/devices')):
    result = {'parameters': read_exports(parameters), 'lid_devices': {}, 'unavailable': {}}
    for device in directory_entries(devices, result['unavailable']):
        if device.name.startswith('PNP0C0D:'):
            info = {**read_exports(device, {'path', 'hid', 'uid', 'status', 'modalias'}),
                    'sysfs': str(device.resolve())}
            result['lid_devices'][str(device)] = info
    return result


def acpi_irq_count(value):
    if not isinstance(value, str) or not value.split():
        return None
    count = number(value.split()[0])
    return count if count is not None and count >= 0 else None


def acpi_irq_snapshot(selected=False, root=Path('/sys/firmware/acpi/interrupts')):
    result = read_exports(root, {'sci', 'gpe_all'} if selected else None)
    result['counts'] = {path: acpi_irq_count(value) for path, value in result['files'].items()}
    return result


def acpi_irq_deltas(before, after):
    a, b = before.get('counts', {}), after.get('counts', {})
    result = {}
    for key in sorted(a.keys() | b.keys()):
        first, last = a.get(key), b.get(key)
        valid = type(first) is int and first >= 0 and type(last) is int and last >= 0
        status = 'unavailable' if not valid else 'reset_or_wrap' if last < first else 'ok'
        result[key] = {'before': first, 'after': last, 'status': status,
                       'delta': last - first if status == 'ok' else None}
    return result


def pmc_debugfs(root=Path('/sys/kernel/debug')):
    """Discover both kernel PMC layouts; report directory access failures explicitly."""
    names = {'lpm_status', 'lpm_requirements', 'substate_residencies', 'slp_s0_residency_usec'}
    result = {'files': {}, 'unavailable': {}}
    directories = [p for p in directory_entries(root, result['unavailable'])
                   if p.name == 'pmc_core' or p.name.startswith('intel_pmc_core')]
    if not directories and not result['unavailable']:
        result['unavailable'][str(root)] = {'error': 'No pmc_core or intel_pmc_core* directory found'}

    def inaccessible(exc):
        result['unavailable'][str(exc.filename or root)] = {'error': str(exc)}

    for directory in directories:
        for parent, _, files in os.walk(directory, onerror=inaccessible, followlinks=False):
            for name in sorted(set(files) & names):
                path = Path(parent) / name
                value = read(path)
                result['files' if isinstance(value, str) else 'unavailable'][str(path)] = value
    return result


def snapshot():
    return {'clock': clocks(), 's0ix_us': read(S0IX), 'pc10_us': read(PC10),
            'intel_pmc_core': pmc_debugfs(),
            'interrupts': read('/proc/interrupts'),
            'wakeup_sources': read('/sys/kernel/debug/wakeup_sources'),
            'acpi_wakeup': read('/proc/acpi/wakeup'),
            'acpi_lid': acpi_lid_states(), 'acpi_button': acpi_button_metadata(),
            'acpi_interrupts': acpi_irq_snapshot(),
            'power': {p: read('/sys/power/' + p) for p in ('state', 'mem_sleep', 'wakeup_count')},
            'battery': {str(p): {f.name: read(f) for f in p.iterdir() if f.name in
                                ('type', 'status', 'capacity', 'charge_now', 'charge_full',
                                 'charge_full_design', 'energy_now', 'energy_full',
                                 'power_now', 'current_now', 'voltage_now', 'online')}
                        for p in Path('/sys/class/power_supply').glob('*')},
            'runtime_pm': runtime_pm(), 'logind': logind(),
            'raw_lids': raw_lids(), 'raw_probe': probe_snapshot(),
            'input_devices': read('/proc/bus/input/devices')}


def metadata():
    return {'uname': list(platform.uname()), 'os_release': read('/etc/os-release'),
            'cmdline': read('/proc/cmdline'), 'boot_id': read('/proc/sys/kernel/random/boot_id'),
            'dmi': {p: read('/sys/class/dmi/id/' + p) for p in
                    ('sys_vendor', 'product_name', 'bios_version', 'bios_date')},
            'logind_config': command(['systemd-analyze', 'cat-config', 'systemd/logind.conf']),
            'inhibitors': command(['systemd-inhibit', '--list', '--no-pager']),
            'pci': command(['lspci', '-nnk']), 'usb': command(['lsusb', '-t']),
            'recent_kernel_journal': command(['journalctl', '-b', '-k', '-n', '300', '--no-pager']),
            'recent_logind_journal': command(['journalctl', '-b', '-u', 'systemd-logind', '-n', '100', '--no-pager']),
            'relevant_boot_kernel_journal': command(['journalctl', '-b', '-k', '--no-pager',
                '--grep=INTC1051|intel.hid|input28|PM: suspend|switch event|intel_pmc']),
            'rtc': {str(p): read(p) for p in Path('/sys/class/rtc').glob('rtc*/wakealarm')},
            'input_udev': {str(p): command(['udevadm', 'info', '--query=all', '--path=' + str(p)])
                           for p in Path('/sys/class/input').glob('event*')}}


def journal_cursor():
    result = command(['journalctl', '-b', '-n', '1', '-o', 'json', '--no-pager'])
    try:
        return json.loads(result['stdout'].splitlines()[-1])['__CURSOR']
    except (KeyError, ValueError, IndexError):
        return None


def journal(cursor, kernel=False):
    if not cursor:
        return {'error': 'No journal cursor; suspend verification unavailable'}
    args = ['journalctl', '-b', '--after-cursor=' + cursor, '-o', 'json', '--no-pager']
    args += ['-k'] if kernel else ['-u', 'systemd-logind']
    return command(args)


def suspend_evidence(result):
    entry = None
    if result.get('returncode') != 0:
        return None
    for line in result.get('stdout', '').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get('MESSAGE', '')
        if not isinstance(message, str):
            continue
        if 'PM: suspend entry (s2idle)' in message:
            entry = row
        elif 'PM: suspend exit' in message and entry is not None:
            return {'entry': entry, 'exit': row}
    return None


@contextlib.contextmanager
def inhibitor(what, lifetime=1500):
    # Readiness comes from the protected child, never from a timing assumption.
    child = subprocess.Popen(['systemd-inhibit', '--what=' + what, '--mode=block',
                              '--who=xps-s0ix-diagnose', '--why=bounded diagnostic',
                              sys.executable, '-c',
                              ("import select, sys, time\n"
                              "deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + float(sys.argv[1])\n"
                              "print('READY', flush=True)\n"
                              "while time.clock_gettime(time.CLOCK_BOOTTIME) < deadline:\n"
                              "    if select.select([sys.stdin], [], [], 0.5)[0]:\n"
                              "        break\n"), str(lifetime)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert child.stdin is not None and child.stdout is not None
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        if not ready or child.stdout.readline().strip() != b'READY' or child.poll() is not None:
            raise SafetyError('Could not acquire runtime inhibitor')
        yield child
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child.stdout.close()


class RTCAlarm:
    def __init__(self, path):
        self.path = Path(path)
        self.target = None
        self.owned = False

    def check_free(self):
        value = read(self.path)
        if not isinstance(value, str) or value not in ('', '0'):
            raise SafetyError('RTC alarm unavailable or already set: ' + str(value))

    def arm(self, seconds):
        self.check_free()
        rtc_now = number(read(self.path.parent / 'since_epoch'))
        if rtc_now is None:
            raise SafetyError('Cannot read RTC time; refusing to assume it matches wall time')
        self.target = rtc_now + seconds
        # Never clear an alarm in order to set ours. Kernel rejects an occupied RTC.
        self.path.write_text(str(self.target))
        self.owned = True
        if number(read(self.path)) != self.target:
            raise SafetyError('RTC alarm readback did not match; refusing suspend')

    def cleanup(self):
        if not self.owned:
            return 'not armed'
        current = read(self.path)
        if number(current) == self.target:
            self.path.write_text('0')
            self.owned = False
            return 'cleared owned alarm'
        if current in ('', '0'):
            self.owned = False
            return 'already expired/cleared'
        raise SafetyError('RTC changed externally or unreadable; not clearing another alarm')


def preflight(args):
    if not args.allow_suspend:
        raise SafetyError('Suspend requires --allow-suspend; no changes made')
    if not 30 <= args.duration <= MAX_DURATION or not 5 <= args.entry_timeout <= 120:
        raise SafetyError('Duration must be 30..900 and entry timeout 5..120 seconds')
    if os.geteuid() != 0:
        raise SafetyError('Suspend experiments require root for RTC and raw input access')
    if read('/sys/power/mem_sleep') != '[s2idle]':
        raise SafetyError('Expected s2idle as the only advertised sleep mode; not changing it')
    pm_test = read('/sys/power/pm_test')
    if not isinstance(pm_test, str) or '[none]' not in pm_test.split():
        raise SafetyError('Cannot establish that /sys/power/pm_test is disabled')
    for supply in Path('/sys/class/power_supply').glob('*'):
        if read(supply / 'type') == 'Battery':
            capacity = number(read(supply / 'capacity'))
            if capacity is not None and capacity < 15:
                raise SafetyError('Battery below 15%; refusing suspend experiment')
    for path in Path('/sys/class/rtc').glob('rtc*/wakealarm'):
        RTCAlarm(path).check_free()
    RTCAlarm(args.rtc).check_free()
    if read(Path(args.rtc).parent / 'device/power/wakeup') != 'enabled':
        raise SafetyError('RTC device wakeup is not confirmed enabled; not changing it')
    require_lid(raw_lids(), False)


def await_suspend(cursor, deadline, grace=0):
    # Always query once on return, even if firmware woke us later than requested.
    while True:
        last = journal(cursor, kernel=True)
        evidence = suspend_evidence(last)
        if evidence:
            return {'status': 'verified_entry_exit', 'evidence': evidence}
        remaining = deadline + grace - time.clock_gettime(time.CLOCK_BOOTTIME)
        if remaining <= 0:
            break
        time.sleep(min(2, remaining))
    return {'status': 'unverified', 'reason': 'No s2idle entry/exit pair established by kernel journal',
            'last_query': last}


def compare(before, after):
    elapsed = after['clock']['boottime'] - before['clock']['boottime']
    battery = {}
    for name in before['battery'].keys() & after['battery'].keys():
        battery[name] = {}
        for key in ('capacity', 'charge_now', 'energy_now'):
            a, b = number(before['battery'][name].get(key)), number(after['battery'][name].get(key))
            battery[name][key + '_delta_after_minus_before'] = b - a if a is not None and b is not None else None
    pm = {p: {'before': before['runtime_pm'][p], 'after': after['runtime_pm'][p]}
          for p in before['runtime_pm'].keys() & after['runtime_pm'].keys()
          if before['runtime_pm'][p] != after['runtime_pm'][p]}
    return {'elapsed_boottime_seconds': elapsed,
            'elapsed_wall_seconds': after['clock']['wall'] - before['clock']['wall'],
            'elapsed_monotonic_seconds': after['clock']['monotonic'] - before['clock']['monotonic'],
            's0ix': counter_delta(before['s0ix_us'], after['s0ix_us'], elapsed),
            'pc10': counter_delta(before['pc10_us'], after['pc10_us'], elapsed),
            'battery': battery,
            'interrupts': interrupt_deltas(before['interrupts'], after['interrupts']),
            'acpi_interrupts': acpi_irq_deltas(before.get('acpi_interrupts', {}), after.get('acpi_interrupts', {})),
            'runtime_pm_changes': pm}


def experiment(args, directory, report):
    preflight(args)
    alarm = RTCAlarm(args.rtc)
    # Normal lid receives NO inhibitor during the actual test.
    with contextlib.ExitStack() as stack:
        if args.mode in ('open', 'closed-manual'):
            guard = stack.enter_context(inhibitor('handle-lid-switch'))
        else:
            guard = None
        try:
            if args.mode == 'closed-manual':
                assert guard is not None
                print('Close the lid now; waiting for verified raw SW_LID=true. '
                      f'Reopen after the RTC wake (about {args.duration} seconds).', flush=True)
                deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + args.entry_timeout
                while True:
                    if guard.poll() is not None:
                        raise SafetyError('Lid inhibitor exited unexpectedly')
                    states = raw_lids()
                    try:
                        require_lid(states, True)
                        break
                    except SafetyError:
                        if time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline:
                            raise SafetyError('Timed out waiting for unambiguous raw closed lid')
                        time.sleep(0.5)
            before = snapshot()
            dump(directory / 'before.json', before)
            require_lid(raw_lids(), args.mode == 'closed-manual')
            # For normal-lid, the entry allowance is part of the single RTC budget.
            budget = args.duration + (args.entry_timeout if args.mode == 'normal-lid' else 0)
            verification_cursor = journal_cursor()
            alarm.arm(budget)
            report['rtc_target_seconds_since_epoch'] = alarm.target
            report['rtc_budget_seconds'] = budget
            deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + budget
            states = raw_lids()
            dump(directory / 'pre_suspend_raw_lid.json', {'clock': clocks(), 'devices': states})
            require_lid(states, args.mode == 'closed-manual')
            if guard is not None and guard.poll() is not None:
                raise SafetyError('Lid inhibitor lost before suspend')
            if args.mode == 'normal-lid':
                print('Close the lid now for normal lid-triggered suspend; RTC wake is armed.', flush=True)
            else:
                result = command(['systemctl', '--no-ask-password', 'suspend'], timeout=budget + 20)
                dump(directory / 'suspend-command.json', result)
                if result.get('returncode') != 0:
                    raise SafetyError('Suspend command failed/timed out; inspect suspend-command.json')
            report['suspend'] = await_suspend(verification_cursor, deadline, grace=10)
            if guard is not None and guard.poll() is not None:
                raise SafetyError('Runtime lid inhibitor ended before the experiment finished')
            # Capture while the manual-path inhibitor still prevents a second lid action.
            dump(directory / 'after.json', snapshot())
            if args.mode == 'closed-manual':
                print('Recording complete. Reopen the lid now; lid handling is being restored.', flush=True)
        finally:
            report['alarm_cleanup'] = alarm.cleanup()


def check_ready_path(path):
    if path is not None and (path.exists() or path.is_symlink() or not path.parent.is_dir()):
        raise SafetyError('Ready file must not exist and its parent must exist: ' + str(path))


@contextlib.contextmanager
def ready_marker(path, payload):
    """Publish only after readiness; do not replace or delete someone else's file."""
    identity = None
    temporary = None
    try:
        if path is not None:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.raw-lid-ready-', delete=False) as stream:
                temporary = Path(stream.name)
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                json.dump(payload, stream)
                stream.write('\n')
            # A hard link publishes complete JSON atomically without replacing a stale marker.
            os.link(temporary, path)
            temporary.unlink()
            temporary = None
        yield
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if path is not None and identity is not None:
            try:
                stat = path.lstat()
                if (stat.st_dev, stat.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass


def wait_probe_ready(child, guard, ready_path):
    deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + PROBE_READY_TIMEOUT
    while True:
        if guard.poll() is not None:
            raise SafetyError('Observation inhibitor exited before probe readiness')
        if child.poll() is not None:
            raise SafetyError('Raw probe exited before readiness; inspect raw-probe-stderr.txt')
        if ready_path.is_file() and read(ready_path) == 'ready':
            return
        if time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline:
            raise SafetyError('Timed out waiting for raw probe readiness')
        time.sleep(0.05)


class SwitchHistory:
    """Per-source, per-device transitions: never join different lids into a cycle."""
    def __init__(self):
        self.state = {}
        self.pending_close = set()
        self.transitions = []
        self.cycles = set()
        self.lid_readings = 0

    def reset(self, path=None):
        if path is None:
            self.state.clear()
            self.pending_close.clear()
        else:
            self.state.pop(path, None)
            self.pending_close.discard(path)

    def add(self, path, state, stamp):
        if not isinstance(path, str) or not isinstance(state, dict):
            return
        previous = self.state.setdefault(path, {})
        for switch in ('SW_LID', 'SW_TABLET_MODE'):
            value = state.get(switch)
            if not isinstance(value, bool):
                continue
            if switch == 'SW_LID':
                self.lid_readings += 1
            old = previous.get(switch)
            if old is not None and old != value:
                self.transitions.append({'path': path, 'switch': switch, 'closed_or_on': value,
                                         'boottime_seconds': stamp})
                if switch == 'SW_LID':
                    if value:
                        self.pending_close.add(path)
                    elif path in self.pending_close:
                        self.cycles.add(path)
                        self.pending_close.discard(path)
            previous[switch] = value

    def summary(self):
        lids = [t for t in self.transitions if t['switch'] == 'SW_LID']
        return {'close_transitions': sum(t['closed_or_on'] for t in lids),
                'open_transitions': sum(not t['closed_or_on'] for t in lids),
                'tablet_toggles': sum(t['switch'] == 'SW_TABLET_MODE' for t in self.transitions),
                'cycle_devices': sorted(self.cycles), 'lid_readings': self.lid_readings,
                'transitions': self.transitions}


def interpret_acpi_samples(samples, enabled=False):
    selected = [s for s in samples if 'acpi_lid' in s]
    report = {'enabled': enabled or bool(selected), 'sample_count': len(selected),
              'closed_samples': 0, 'closed_readings': 0, 'readable_samples': 0,
              'compared_samples': 0, 'unavailable_or_ambiguous_samples': 0,
              'disagreement_samples': 0, 'acpi_closed_raw_open_samples': 0,
              'acpi_open_raw_closed_samples': 0, 'examples': []}
    for sample in selected:
        acpi = sample['acpi_lid']
        parsed = [acpi_lid_closed(value) for value in acpi.get('files', {}).values()]
        report['closed_readings'] += sum(value is True for value in parsed)
        report['closed_samples'] += any(value is True for value in parsed)
        report['readable_samples'] += any(value is not None for value in parsed)
        states = sample.get('raw_lids', [])
        raw = [state.get('SW_LID') for state in states]
        if (not parsed or any(value is None for value in parsed) or len(set(parsed)) != 1
                or acpi.get('unavailable') or not raw or any(type(value) is not bool for value in raw)
                or any(state.get('error') for state in states) or len(set(raw)) != 1):
            report['unavailable_or_ambiguous_samples'] += 1
            continue
        report['compared_samples'] += 1
        if parsed[0] != raw[0]:
            report['disagreement_samples'] += 1
            direction = 'acpi_closed_raw_open_samples' if parsed[0] else 'acpi_open_raw_closed_samples'
            report[direction] += 1
            if len(report['examples']) < 5:
                report['examples'].append({'clock': sample.get('clock'),
                                           'acpi_clock': sample.get('acpi_clock'),
                                           'acpi_closed': parsed[0], 'raw_SW_LID': raw[0],
                                           'irq_counts': sample.get('acpi_interrupts', {}).get('counts', {})})
    report['sampled_irq_deltas'] = (acpi_irq_deltas(selected[0].get('acpi_interrupts', {}),
                                                 selected[-1].get('acpi_interrupts', {})) if selected else {})
    if report['acpi_closed_raw_open_samples']:
        report['interpretation'] = ('ACPI reported closed while raw input reported open. Investigate the '
                                    'notification/button-to-input path; this does not establish which component failed.')
    elif report['closed_samples']:
        report['interpretation'] = 'ACPI reported closure; inspect raw comparisons and event timing before attributing a fault.'
    elif report['readable_samples']:
        report['interpretation'] = 'ACPI did not report closed in this window; physical closure is not ruled out.'
    else:
        report['interpretation'] = 'No readable ACPI lid samples; firmware lid state is unverified.'
    report['caveat'] = ('Absence of an observed transition does not establish that no physical action occurred. ACPI/raw/logind '
                        'reads are non-atomic; reading ACPI lid state evaluates _LID and may perturb firmware. '
                        'SCI/GPE totals include unrelated activity and do not prove a lid notification was delivered.')
    return report


def interpret_raw(jsonl, samples, acpi_lid=False):
    probe, sampled = SwitchHistory(), SwitchHistory()
    errors, dropped, malformed = 0, 0, 0
    for line in jsonl.splitlines() if isinstance(jsonl, str) else []:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError('not a record')
        except (TypeError, ValueError):
            malformed += 1
            probe.reset()
            continue
        kind = row.get('kind')
        device = row.get('device', {})
        path = row.get('path') or (device.get('path') if isinstance(device, dict) else None)
        if kind in ('syn_dropped', 'error'):
            dropped += kind == 'syn_dropped'
            errors += kind == 'error'
            probe.reset(path)
        elif kind in ('initial', 'state_change', 'resync', 'final'):
            if kind in ('initial', 'resync'):
                probe.reset(path)
            probe.add(path, row.get('state'), row.get('boottime_seconds'))
        elif kind == 'event' and type(row.get('value')) is int and row['value'] in (0, 1):
            probe.add(path, {row.get('switch'): bool(row['value'])}, row.get('boottime_seconds'))

    compared = unavailable = 0
    mismatches = []
    for sample in samples:
        states = sample.get('raw_lids', [])
        stamp = sample.get('clock', {}).get('boottime')
        present = {state.get('device') for state in states}
        for missing in sampled.state.keys() - present:
            sampled.reset(missing)
        for state in states:
            if state.get('error') or not isinstance(state.get('SW_LID'), bool):
                sampled.reset(state.get('device'))
            else:
                sampled.add(state.get('device'), {'SW_LID': state['SW_LID']}, stamp)
        if not states:
            sampled.reset()
        lid = sample.get('LidClosed', {})
        text = lid.get('stdout', '').strip()
        raw_values = {s.get('SW_LID') for s in states if isinstance(s.get('SW_LID'), bool)}
        if (not states or any(s.get('error') or not isinstance(s.get('SW_LID'), bool) for s in states)
                or len(raw_values) != 1 or lid.get('returncode') != 0 or text not in ('b true', 'b false')):
            unavailable += 1
            continue
        raw = next(iter(raw_values))
        logged = text == 'b true'
        compared += 1
        if raw != logged:
            mismatches.append({'boottime_seconds': stamp, 'raw_SW_LID': raw, 'logind_LidClosed': logged})

    p, s = probe.summary(), sampled.summary()
    cycle = bool(p['cycle_devices'] or s['cycle_devices'])
    transitions = p['close_transitions'] + p['open_transitions'] + s['close_transitions'] + s['open_transitions']
    status = ('raw_cycle_observed' if cycle else 'incomplete_lid_cycle' if transitions else
              'no_lid_cycle_observed' if probe.lid_readings + sampled.lid_readings else 'raw_data_unavailable')
    return {'status': status, 'complete_open_close_open_cycle': cycle, 'probe': p, 'samples': s,
            'probe_errors': errors, 'syn_dropped': dropped, 'malformed_records': malformed,
            'acpi_correlation': interpret_acpi_samples(samples, enabled=acpi_lid),
            'logind_comparison': {'compared_samples': compared, 'unavailable_or_ambiguous_samples': unavailable,
                                  'mismatch_samples': len(mismatches), 'examples': mismatches[:5]},
            'caveat': 'No observed cycle does not prove no physical action. Raw/logind reads are not atomic; '
                      'a mismatch can reflect a transition or logind lag, not necessarily a persistent fault.'}


def raw_report_lines(report):
    p, s, log = report['probe'], report['samples'], report['logind_comparison']
    lines = [f"Raw lid: {report['status']} (requires observed open -> closed -> open)",
            (f"Probe lid close/open transitions: {p['close_transitions']}/{p['open_transitions']}; "
             f"sampled: {s['close_transitions']}/{s['open_transitions']}"),
            (f"Tablet toggles: {p['tablet_toggles']}; logind/raw mismatches: "
             f"{log['mismatch_samples']}/{log['compared_samples']} comparable samples "
             f"({log['unavailable_or_ambiguous_samples']} unavailable/ambiguous)"), report['caveat']]
    acpi = report.get('acpi_correlation', {})
    if acpi.get('enabled'):
        lines += [(f"ACPI closed samples: {acpi['closed_samples']}/{acpi['sample_count']}; "
                   f"ACPI/raw disagreements: {acpi['disagreement_samples']}/{acpi['compared_samples']} "
                   f"({acpi['unavailable_or_ambiguous_samples']} unavailable/ambiguous)"),
                  'Sampled SCI/GPE deltas: ' + json.dumps(acpi['sampled_irq_deltas']),
                  acpi['interpretation'], acpi['caveat']]
    return lines


def raw_sample(acpi_lid=False):
    sample = {'clock': clocks(), 'raw_lids': raw_lids()}
    if acpi_lid:
        sample['acpi_clock'] = clocks()
        sample['acpi_lid'] = acpi_lid_states()
        sample['acpi_interrupts'] = acpi_irq_snapshot(selected=True)
        sample['acpi_finished_clock'] = clocks()
    sample['LidClosed'] = command(['busctl', '--system', 'get-property', 'org.freedesktop.login1',
                                  '/org/freedesktop/login1', 'org.freedesktop.login1.Manager', 'LidClosed'], timeout=2)
    sample['finished_clock'] = clocks()
    return sample


def observe_raw(duration, directory, guard, ready_file=None, acpi_lid=False):
    probe_ready = directory / 'probe-ready'
    check_ready_path(probe_ready)
    check_ready_path(ready_file)
    argv = [sys.executable, str(ROOT / 'lid_probe.py'), '--duration', str(duration),
            '--output', str(directory / 'raw-lid.jsonl'), '--ready-file', str(probe_ready)]
    samples = []
    with (directory / 'raw-logind-samples.jsonl').open('x') as sample_log, \
            (directory / 'raw-probe-stdout.txt').open('w') as out, \
            (directory / 'raw-probe-stderr.txt').open('w') as err:
        child = subprocess.Popen(argv, stdout=out, stderr=err)
        try:
            wait_probe_ready(child, guard, probe_ready)
            deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + duration + 10
            payload = {'status': 'ready', 'pid': os.getpid(), 'results': str(directory.resolve()),
                       'clock': clocks(), 'observation_duration_seconds': duration,
                       'worker_deadline_boottime': deadline, 'acpi_lid_polling': acpi_lid}
            if guard.poll() is not None or child.poll() is not None:
                raise SafetyError('Probe or inhibitor ended before readiness could be published')
            with ready_marker(ready_file, payload):
                print('Probe and inhibitor ready. Close the lid for about 10 seconds, then reopen it.', flush=True)
                while child.poll() is None:
                    if guard.poll() is not None:
                        raise SafetyError('Observation inhibitor exited unexpectedly')
                    if time.clock_gettime(time.CLOCK_BOOTTIME) >= deadline:
                        raise SafetyError('Raw probe exceeded bounded observation window')
                    sample = raw_sample(acpi_lid=acpi_lid)
                    samples.append(sample)
                    # Preserve completed samples even if cancellation bypasses finally.
                    sample_log.write(json.dumps(sample) + '\n')
                    sample_log.flush()
                    time.sleep(0.5)
                if child.returncode != 0:
                    raise SafetyError('Raw probe failed; inspect raw-probe-stderr.txt')
                if guard.poll() is not None:
                    raise SafetyError('Observation inhibitor ended before capture completed')
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            probe_ready.unlink(missing_ok=True)
            dump(directory / 'raw-probe-command.json', {'argv': argv, 'returncode': child.returncode})
            dump(directory / 'raw-logind-samples.json', samples)
            result = interpret_raw(read(directory / 'raw-lid.jsonl'), samples, acpi_lid=acpi_lid)
            dump(directory / 'raw-lid-report.json', result)
    return result


def reopen_before_release(guard, release_deadline):
    """Best-effort fresh lid check while inhibited, never extending the guard's budget."""
    started = time.clock_gettime(time.CLOCK_BOOTTIME)
    deadline = min(started + RAW_REOPEN_GRACE, release_deadline)
    warned = False
    while True:
        try:
            states = raw_lids()
        except OSError as exc:
            states = [{'error': str(exc)}]
        try:
            require_lid(states, False)
            status = 'open_verified'
            break
        except SafetyError:
            if not warned:
                print('WARNING: Raw SW_LID is closed or unknown. Reopen the lid now; '
                      'waiting up to 10 seconds, only while the inhibitor budget remains.', flush=True)
                warned = True
        remaining = deadline - time.clock_gettime(time.CLOCK_BOOTTIME)
        if guard.poll() is not None:
            status = 'inhibitor_lost'
            print('WARNING: Inhibitor already ended and raw lid-open state is unverified; '
                  'reopen now. Normal lid policy may suspend the machine.', flush=True)
            break
        if remaining <= 0:
            status = 'released_unverified'
            print('WARNING: Reopening grace/budget expired with raw lid-open state unverified; '
                  'releasing the inhibitor. Normal lid policy may suspend the machine.', flush=True)
            break
        time.sleep(min(0.5, remaining))
    return {'status': status, 'raw_lids': states,
            'elapsed_seconds': time.clock_gettime(time.CLOCK_BOOTTIME) - started}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    for mode in ('baseline', 'open', 'normal-lid', 'closed-manual', 'raw-lid'):
        s = sub.add_parser(mode)
        s.add_argument('--output-root', type=Path, default=ROOT / 'runs')
        if mode != 'baseline':
            s.add_argument('--duration', type=int, default=30 if mode == 'raw-lid' else 600)
        if mode == 'raw-lid':
            s.add_argument('--acpi-lid', action='store_true',
                           help='Poll ACPI _LID and SCI/GPE totals too; a separate experiment that may perturb firmware')
            s.add_argument('--ready-file', type=Path,
                           help='New marker published only while inhibitor and readable lid probe are ready')
        if mode in ('open', 'normal-lid', 'closed-manual'):
            s.add_argument('--allow-suspend', action='store_true')
            s.add_argument('--entry-timeout', type=int, default=60)
            s.add_argument('--rtc', choices=['/sys/class/rtc/rtc0/wakealarm'],
                           default='/sys/class/rtc/rtc0/wakealarm')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.mode in ('open', 'normal-lid', 'closed-manual'):
        # Refuse before creating artifacts or doing slow capture.
        preflight(args)
    if args.mode == 'raw-lid':
        if not 1 <= args.duration <= 120:
            raise SafetyError('Raw observation duration must be 1..120 seconds')
        if not (ROOT / 'lid_probe.py').is_file():
            raise SafetyError('raw-lid requires lid_probe.py alongside this script')
        check_ready_path(args.ready_file)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    directory = args.output_root / (stamp + '-' + args.mode)
    directory.mkdir(parents=True, mode=0o700)
    print('Results:', directory, flush=True)
    report = {'mode': args.mode, 'arguments': {k: str(v) if isinstance(v, Path) else v
                                              for k, v in vars(args).items()},
              'status': 'running', 'limitations': [
                  'RTC wake is not a guarantee: firmware, policy, power loss or hardware can prevent resume.',
                  'Percentages use capture-to-capture CLOCK_BOOTTIME, including awake setup/collection.',
                  'A journal entry/exit pair proves a suspend attempt, not successful deep residency.',
                  'No journal evidence means unverified, never assumed suspend success.',
                  'PC10 cpuidle CPU residency is a secondary platform-exported counter; resets are not unwrapped.',
                  'Normal lid policy may re-suspend after RTC wake if the lid remains closed.']}
    before = None
    cursor = journal_cursor()
    try:
        dump(directory / 'metadata.json', metadata())
        before = snapshot()
        dump(directory / 'initial.json', before)
        if args.mode == 'baseline':
            report['status'] = 'captured'
        elif args.mode == 'raw-lid':
            lifetime = args.duration + PROBE_READY_TIMEOUT + RAW_CLEANUP_MARGIN
            # Start conservatively before acquisition; reserve a second for inhibitor release.
            release_deadline = time.clock_gettime(time.CLOCK_BOOTTIME) + lifetime - 1
            with inhibitor('sleep:handle-lid-switch', lifetime=lifetime) as guard:
                try:
                    report['raw_lid'] = observe_raw(args.duration, directory, guard, args.ready_file,
                                                                        acpi_lid=args.acpi_lid)
                finally:
                    report['raw_reopen'] = reopen_before_release(guard, release_deadline)
            report['status'] = report['raw_lid']['status']
        else:
            experiment(args, directory, report)
            report['status'] = report['suspend']['status']
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- retain evidence after any run failure
        report['status'] = 'failed'
        report['error'] = str(exc) or type(exc).__name__
    finally:
        try:
            if (directory / 'after.json').exists():
                after = json.loads((directory / 'after.json').read_text())
            else:
                after = snapshot()
                dump(directory / 'after.json', after)
            if (directory / 'before.json').exists():
                before = json.loads((directory / 'before.json').read_text())
            if before is not None:
                report['comparison'] = compare(before, after)
            dump(directory / 'kernel-journal.json', journal(cursor, kernel=True))
            dump(directory / 'logind-journal.json', journal(cursor))
            if args.mode == 'raw-lid' and (directory / 'raw-lid-report.json').exists():
                report['raw_lid'] = json.loads((directory / 'raw-lid-report.json').read_text())
        except Exception as exc:  # noqa: BLE001 -- best-effort final report after capture failure
            report['capture_error'] = str(exc)
            report['status'] = 'failed'
        dump(directory / 'summary.json', report)
        summary = ['Mode: ' + args.mode, 'Status: ' + report['status']]
        if 'error' in report:
            summary.append('Error: ' + report['error'])
        if 'raw_lid' in report:
            summary += raw_report_lines(report['raw_lid'])
        if 'raw_reopen' in report:
            summary.append('Lid state before inhibitor release: ' + report['raw_reopen']['status'])
        if 'comparison' in report:
            c = report['comparison']
            summary += [f"Elapsed CLOCK_BOOTTIME: {c['elapsed_boottime_seconds']:.3f} s",
                        f"Elapsed wall: {c['elapsed_wall_seconds']:.3f} s",
                        'Primary S0ix: ' + json.dumps(c['s0ix']),
                        'Secondary PC10: ' + json.dumps(c['pc10']),
                        'Battery (negative means consumption; charge in uAh, energy in uWh): ' + json.dumps(c['battery'])]
        summary += ['', 'Limitations:'] + ['- ' + x for x in report['limitations']]
        (directory / 'summary.txt').write_text('\n'.join(summary) + '\n')
        print('\n'.join(summary), flush=True)
    return 0 if report['status'] in ('captured', 'raw_cycle_observed', 'verified_entry_exit') else 2


def interrupted(signum, frame):
    raise KeyboardInterrupt('signal ' + str(signum))


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (SafetyError, KeyboardInterrupt) as exc:
        print('Refused:', exc, file=sys.stderr)
        sys.exit(2)
