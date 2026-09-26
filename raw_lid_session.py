#!/usr/bin/env python3
"""One graphical authorization for a bounded, sleep-inhibited raw-lid capture.

Run as the desktop user. Does not suspend, install policy, or retain privileges.
"""
import argparse
import importlib.util
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def worker(ready, acpi_lid=False):
    uid = int(os.environ['PKEXEC_UID'])
    if os.geteuid() != 0 or uid == 0:
        raise RuntimeError('Worker requires pkexec from a non-root desktop user')
    gid = pwd.getpwuid(uid).pw_gid
    runs = ROOT / 'runs'
    runs.mkdir(exist_ok=True)
    # Keep generated privileged evidence private until capture finishes.
    output = Path(tempfile.mkdtemp(prefix='raw-session-', dir=runs))
    try:
        spec = importlib.util.spec_from_file_location('diagnose', ROOT / 'xps-s0ix-diagnose.py')
        if spec is None or spec.loader is None:
            raise RuntimeError('Could not load the diagnostic harness')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        signal.signal(signal.SIGTERM, module.interrupted)
        signal.signal(signal.SIGALRM, module.interrupted)
        # Bound aggregate external-command work, not just each subprocess.
        # Kernel/device reads can still be uninterruptible; no hard guarantee.
        vars(module)['COMMAND_DEADLINE'] = time.clock_gettime(time.CLOCK_BOOTTIME) + 150
        signal.alarm(160)
        return module.main(['raw-lid', '--duration', '90', '--output-root', str(output),
                            '--ready-file', str(ready)] + (['--acpi-lid'] if acpi_lid else []))
    finally:
        signal.alarm(0)
        # Only hand back this newly-created private result tree; never follow links.
        # Change directories last so the tree stays private throughout the walk.
        for parent, directories, files in os.walk(output, topdown=False, followlinks=False):
            for name in files + directories:
                os.chown(Path(parent) / name, uid, gid, follow_symlinks=False)
        os.chown(output, uid, gid, follow_symlinks=False)


def desktop_notice(title, message):
    notify = shutil.which('notify-send')
    if notify:
        try:
            subprocess.run([notify, '--urgency=critical', '--expire-time=20000', title, message],
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f'Desktop notice unavailable: {error}; use the terminal prompt.', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-ready', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--acpi-lid', action='store_true',
                        help='Also poll firmware ACPI lid state and SCI/GPE totals')
    args = parser.parse_args()
    if args.worker_ready:
        return worker(args.worker_ready, args.acpi_lid)
    if os.geteuid() == 0:
        parser.error('Run as the desktop user; this tool requests one graphical authorization')
    pkexec = shutil.which('pkexec')
    if not pkexec:
        parser.error('pkexec unavailable; use the documented sudo raw-lid command instead')
    with tempfile.TemporaryDirectory(prefix='.raw-session-', dir=ROOT) as directory:
        ready = Path(directory) / 'ready.json'
        child = subprocess.Popen([pkexec, '--disable-internal-agent', sys.executable, '-B',
                                  str(Path(__file__).resolve()), '--worker-ready', str(ready)]
                                 + (['--acpi-lid'] if args.acpi_lid else []))
        notified = False
        deadline = time.monotonic() + 60
        try:
            while child.poll() is None:
                # The root-owned marker is atomically published after both
                # children are ready. Its existence is the handshake; reading
                # its private JSON is neither necessary nor permitted here.
                if ready.is_file() and not notified:
                    print('\nRECORDING READY: close the lid for 10 seconds, then reopen. '
                          'Do not fold it into tablet mode. No commands needed. '
                          'Wait for RECORDING ENDED before sending confirmation in chat.\n', flush=True)
                    desktop_notice('Lid recording ready — sleep inhibited',
                                   'Close for 10 seconds, then reopen normally. Wait for the '
                                   'recording-ended notice before confirming in chat.')
                    notified = True
                    deadline = time.monotonic() + 140
                if time.monotonic() >= deadline:
                    raise TimeoutError('Authorization/setup or recording exceeded its session deadline')
                time.sleep(0.2)
            if notified:
                message = (f'Recorder exited with status {child.returncode}; results need review. '
                           'Keep the lid open. You can now confirm the physical closure in chat.')
                print('\nRECORDING ENDED: ' + message, flush=True)
                desktop_notice('Lid recording ended', message)
            else:
                message = f'Recorder exited with status {child.returncode} before readiness. Do not close the lid.'
                print(message, file=sys.stderr, flush=True)
                desktop_notice('Lid recorder did not become ready', message)
            return child.returncode
        finally:
            if child.poll() is None:
                # pkexec replaces itself after authorization; unprivileged signals
                # may then be denied. The root worker's capture/inhibitor still expire.
                try:
                    child.terminate()
                    child.wait(timeout=5)
                except (PermissionError, subprocess.TimeoutExpired):
                    print('Worker could not be stopped from this UID; its bounded recording '
                          'and inhibitor will expire automatically.', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
