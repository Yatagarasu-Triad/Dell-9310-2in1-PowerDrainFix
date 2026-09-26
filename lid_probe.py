#!/usr/bin/env python3
"""Read-only Linux EV_SW probe. Never grabs devices or records keystrokes."""
import argparse
import array
import datetime
import fcntl
import json
import os
from pathlib import Path
import selectors
import struct
import sys
import time

EV_SYN, EV_SW = 0, 5
SYN_REPORT, SYN_DROPPED = 0, 3
SW_LID, SW_TABLET_MODE = 0, 1
SW_BYTES = 8  # Linux SW_MAX is currently 0x10; leave room for future switches.
EVENT = struct.Struct('@llHHi')  # struct input_event uses native struct timeval.
SYS_INPUT = Path('/sys/class/input')
WORD_BITS = struct.calcsize('@L') * 8


def ioctl_read_request(number, size):
    return (2 << 30) | (size << 16) | (ord('E') << 8) | number


def switch_name(code):
    return {0: 'SW_LID', 1: 'SW_TABLET_MODE', 2: 'SW_HEADPHONE_INSERT',
            6: 'SW_LINEOUT_INSERT', 8: 'SW_MICROPHONE_INSERT'}.get(code, f'SW_{code}')


def bitmap(text, word_bits=WORD_BITS):
    value = 0
    for word in text.split():  # sysfs prints most-significant word first.
        value = (value << word_bits) | int(word, 16)
    return value


def supported_codes(mask):
    return [bit for bit in range(mask.bit_length()) if mask & (1 << bit)]


def state_from_bytes(data, codes):
    # Kernel bitmaps consist of native unsigned longs, not a big-endian integer.
    words = array.array('L')
    words.frombytes(bytes(data))
    bits = words.itemsize * 8
    return {switch_name(code): bool(words[code // bits] & (1 << (code % bits)))
            for code in codes if code // bits < len(words)}


def query_state(fd, codes):
    data = bytearray(SW_BYTES)
    fcntl.ioctl(fd, ioctl_read_request(0x1b, len(data)), data, True)  # EVIOCGSW
    return state_from_bytes(data, codes)


def text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def enumerate_devices(root=SYS_INPUT):
    result = []
    for event in sorted(root.glob('event*'), key=lambda p: int(p.name[5:])):
        dev = event / 'device'
        try:
            ev = bitmap((dev / 'capabilities/ev').read_text())
            if not ev & (1 << EV_SW):
                continue
            mask = bitmap((dev / 'capabilities/sw').read_text())
        except (OSError, ValueError):
            continue
        ancestry = []
        resolved = dev.resolve()
        for parent in (resolved, *resolved.parents):
            driver = parent / 'driver'
            if driver.is_symlink():
                ancestry.append({'device': str(parent), 'driver': str(driver.resolve())})
        result.append({'path': '/dev/input/' + event.name, 'name': text(dev / 'name'),
                       'phys': text(dev / 'phys'), 'sysfs': str(resolved),
                       'supported_codes': supported_codes(mask),
                       'switches': [switch_name(c) for c in supported_codes(mask)],
                       'driver_ancestry': ancestry})
    return result


def stamp():
    return {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'boottime_seconds': time.clock_gettime(time.CLOCK_BOOTTIME)}


def snapshot():
    devices = enumerate_devices()
    for device in devices:
        try:
            fd = os.open(device['path'], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                device['state'] = query_state(fd, device['supported_codes'])
            finally:
                os.close(fd)
        except OSError as error:
            device['state'] = None
            device['error'] = str(error)
    return {'kind': 'snapshot', **stamp(), 'devices': devices}


def require_readable_lid(opened):
    if not any(SW_LID in item['device']['supported_codes'] for item in opened.values()):
        raise RuntimeError('No readable SW_LID device; cannot verify lid transitions.')


def stream(duration, emit, ready_file=None):
    """Bounded recording; initial/final states plus changes and kernel events."""
    devices = enumerate_devices()
    selector = selectors.DefaultSelector()
    opened = {}
    try:
        for device in devices:
            fd = None
            try:
                fd = os.open(device['path'], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                state = query_state(fd, device['supported_codes'])
                selector.register(fd, selectors.EVENT_READ)
                opened[fd] = {'device': device, 'buffer': b'', 'dropped': False, 'state': state}
                emit({'kind': 'initial', **stamp(), 'device': device, 'state': state})
            except OSError as error:
                if fd is not None:
                    os.close(fd)
                emit({'kind': 'error', **stamp(), 'device': device, 'error': str(error)})
        require_readable_lid(opened)
        emit({'kind': 'ready', **stamp(), 'duration_seconds': duration})
        if ready_file is not None:
            # Exclusive create prevents accidentally overwriting another file.
            with ready_file.open('x') as ready:
                ready.write('ready\n')
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            for key, _ in selector.select(min(0.25, max(0, deadline - time.monotonic()))):
                fd = key.fd
                item = opened[fd]
                try:
                    chunk = os.read(fd, EVENT.size * 64)
                    if not chunk:
                        raise OSError('Device disconnected')
                except BlockingIOError:
                    continue
                except OSError as error:
                    emit({'kind': 'error', **stamp(), 'device': item['device'], 'error': str(error)})
                    selector.unregister(fd)
                    os.close(fd)
                    del opened[fd]
                    require_readable_lid(opened)
                    continue
                item['buffer'] += chunk
                while len(item['buffer']) >= EVENT.size:
                    seconds, micros, event_type, code, value = EVENT.unpack(item['buffer'][:EVENT.size])
                    item['buffer'] = item['buffer'][EVENT.size:]
                    if event_type == EV_SYN and code == SYN_DROPPED:
                        item['dropped'] = True
                        emit({'kind': 'syn_dropped', **stamp(), 'path': item['device']['path']})
                    elif item['dropped']:
                        if event_type == EV_SYN and code == SYN_REPORT:
                            item['dropped'] = False
                            item['state'] = query_state(fd, item['device']['supported_codes'])
                            emit({'kind': 'resync', **stamp(), 'path': item['device']['path'],
                                  'state': item['state']})
                    elif event_type == EV_SW:
                        emit({'kind': 'event', **stamp(), 'path': item['device']['path'],
                              'kernel_time_seconds': seconds + micros / 1e6,
                              'kernel_clock': 'evdev default (not used for elapsed time)',
                              'code': code, 'switch': switch_name(code), 'value': value})
            # EVIOCGSW is authoritative, including when no event is queued.
            for fd, item in opened.items():
                state = query_state(fd, item['device']['supported_codes'])
                if state != item['state']:
                    emit({'kind': 'state_change', **stamp(), 'path': item['device']['path'], 'state': state})
                    item['state'] = state
        for fd, item in opened.items():
            emit({'kind': 'final', **stamp(), 'path': item['device']['path'],
                  'state': query_state(fd, item['device']['supported_codes'])})
    finally:
        selector.close()
        for fd in opened:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=float, default=0, help='Stream for 1–120 seconds; default: snapshot')
    parser.add_argument('--output', type=Path, help='New JSON/JSONL output file (never overwritten)')
    parser.add_argument('--ready-file', type=Path, help='Create only after stream has a readable lid device')
    args = parser.parse_args()
    if args.duration != 0 and not 1 <= args.duration <= 120:
        parser.error('duration must be 1–120 seconds')
    if args.ready_file and not args.duration:
        parser.error('--ready-file requires --duration')
    output = args.output.open('x') if args.output else sys.stdout
    try:
        def emit(record):
            print(json.dumps(record, sort_keys=True), file=output, flush=True)
        if args.duration:
            stream(args.duration, emit, args.ready_file)
        else:
            emit(snapshot())
    except (OSError, RuntimeError) as error:
        print(f'lid probe: {error}', file=sys.stderr)
        return 2
    finally:
        if output is not sys.stdout:
            output.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
