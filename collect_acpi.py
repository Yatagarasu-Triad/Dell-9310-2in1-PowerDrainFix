#!/usr/bin/env python3
"""Copy only DSDT/SSDT AML for offline inspection; never evaluate or install AML."""
import datetime
import hashlib
import json
import os
import pwd
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    if os.geteuid() != 0:
        raise SystemExit('Use pkexec or sudo for read-only access to ACPI tables')
    owner = os.environ.get('PKEXEC_UID', os.environ.get('SUDO_UID'))
    if owner is None or int(owner) == 0:
        raise SystemExit('Run from the desktop user via pkexec/sudo for safe results handback')
    uid = int(owner)
    gid = pwd.getpwuid(uid).pw_gid
    runs = ROOT / 'runs'
    runs.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')
    output = Path(tempfile.mkdtemp(prefix=stamp + '-acpi-', dir=runs))
    manifest = []
    try:
        table_root = Path('/sys/firmware/acpi/tables')
        # Exclude MSDM/SLIC/UEFI and all unrelated tables (e.g. licensing material).
        tables = [table_root / 'DSDT', *sorted(table_root.glob('SSDT*')),
                  *sorted((table_root / 'dynamic').glob('SSDT*'))]
        for index, source in enumerate(tables):
            try:
                data = source.read_bytes()
                if len(data) < 36 or data[:4] not in (b'DSDT', b'SSDT'):
                    raise ValueError('Unexpected AML table header')
                declared = int.from_bytes(data[4:8], 'little')
                if declared != len(data) or sum(data) % 256:
                    raise ValueError('ACPI table length or checksum mismatch')
                target = output / f'{index:02d}-{source.name}.dat'
                with target.open('xb') as stream:
                    stream.write(data)
                manifest.append({'source': str(source), 'file': target.name, 'size': len(data),
                                 'sha256': hashlib.sha256(data).hexdigest()})
            except (OSError, ValueError) as error:
                manifest.append({'source': str(source), 'error': str(error)})
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('Offline AML evidence:', output, flush=True)
        print(json.dumps(manifest, indent=2), flush=True)
    finally:
        for file in output.iterdir():
            os.chown(file, uid, gid, follow_symlinks=False)
        os.chown(output, uid, gid, follow_symlinks=False)
    return 2 if any('error' in table for table in manifest) else 0


if __name__ == '__main__':
    raise SystemExit(main())
