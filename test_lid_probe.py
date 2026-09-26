import array

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lid_probe as probe


class ProbeTests(unittest.TestCase):
    def test_bitmap_words(self):
        self.assertEqual(probe.bitmap('1 2', 64), (1 << 64) | 2)
        self.assertEqual(probe.bitmap('1 2', 32), (1 << 32) | 2)
        self.assertEqual(probe.bitmap('0'), 0)

    def test_ioctl_request(self):
        self.assertEqual(probe.ioctl_read_request(0x1b, 8), 0x8008451b)

    def test_state_uses_native_words_and_supported_bits(self):
        words = array.array('L', [3, 0])
        self.assertEqual(probe.state_from_bytes(words.tobytes(), [0, 1, 2]),
                         {'SW_LID': True, 'SW_TABLET_MODE': True, 'SW_HEADPHONE_INSERT': False})
        self.assertEqual(probe.state_from_bytes(words.tobytes(), [1]), {'SW_TABLET_MODE': True})

    def test_query_ioctl(self):
        def fake_ioctl(fd, request, data, mutate):
            self.assertEqual(fd, 13)
            self.assertEqual(request, 0x8008451b)
            data[:] = array.array('L', [1]).tobytes().ljust(8, b'\0')
        with patch.object(probe.fcntl, 'ioctl', side_effect=fake_ioctl):
            self.assertEqual(probe.query_state(13, [0, 1]), {'SW_LID': True, 'SW_TABLET_MODE': False})

    def test_enumerate_excludes_keyboard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, ev, sw, name in [(0, '21', '1', 'Lid Switch'),
                                        (17, '21', '2', 'Intel HID switches'),
                                        (2, '120013', '0', 'Keyboard')]:
                dev = root / f'event{index}' / 'device'
                (dev / 'capabilities').mkdir(parents=True)
                (dev / 'capabilities/ev').write_text(ev)
                (dev / 'capabilities/sw').write_text(sw)
                (dev / 'name').write_text(name)
                (dev / 'phys').write_text('test/input0')
            rows = probe.enumerate_devices(root)
            self.assertEqual([d['path'] for d in rows], ['/dev/input/event0', '/dev/input/event17'])
            self.assertEqual(rows[1]['switches'], ['SW_TABLET_MODE'])

    def test_permission_error_is_not_open_lid(self):
        with patch.object(probe, 'enumerate_devices', return_value=[{'path': '/dev/input/event0'}]), \
                patch.object(probe.os, 'open', side_effect=PermissionError('denied')):
            row = probe.snapshot()['devices'][0]
        self.assertIsNone(row['state'])
        self.assertIn('denied', row['error'])

    def test_stream_without_lid_refuses_readiness(self):
        records = []
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / 'ready'
            with patch.object(probe, 'enumerate_devices', return_value=[]):
                with self.assertRaisesRegex(RuntimeError, 'No readable SW_LID'):
                    probe.stream(1, records.append, ready)
            self.assertFalse(ready.exists())
        self.assertFalse(any(r['kind'] == 'ready' for r in records))

    def test_losing_last_lid_device_refuses_continued_capture(self):
        probe.require_readable_lid({4: {'device': {'supported_codes': [0]}}})
        with self.assertRaisesRegex(RuntimeError, 'No readable SW_LID'):
            probe.require_readable_lid({17: {'device': {'supported_codes': [1]}}})

    def test_switch_names_distinct(self):
        self.assertNotEqual(probe.switch_name(0), probe.switch_name(1))
        self.assertEqual(probe.switch_name(99), 'SW_99')

    def test_native_event_roundtrip(self):
        data = probe.EVENT.pack(123, 456, probe.EV_SW, probe.SW_LID, 1)
        self.assertEqual(probe.EVENT.unpack(data), (123, 456, 5, 0, 1))


if __name__ == '__main__':
    unittest.main()
