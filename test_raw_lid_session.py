import contextlib
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import raw_lid_session as session


class SessionTests(unittest.TestCase):
    def run_parent(self, notice_error=None, exit_code=0, ready=True):
        child = MagicMock()
        child.poll.side_effect = [None, exit_code, exit_code]
        child.returncode = exit_code
        output = io.StringIO()
        with patch.object(session.sys, 'argv', ['raw_lid_session.py']), \
                patch.object(session.os, 'geteuid', return_value=1000), \
                patch.object(session.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
                patch.object(session.subprocess, 'Popen', return_value=child), \
                patch.object(session.subprocess, 'run', side_effect=notice_error) as notify, \
                patch.object(session.time, 'sleep'), \
                patch.object(Path, 'is_file', return_value=ready), \
                patch.object(Path, 'read_text', side_effect=PermissionError('root-only marker')) as read, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(session.main(), exit_code)
        read.assert_not_called()
        child.terminate.assert_not_called()
        self.assertEqual(notify.call_count, 2 if ready else 1)
        if ready:
            self.assertIn('RECORDING READY', output.getvalue())
            self.assertIn('RECORDING ENDED', output.getvalue())
            self.assertIn('Wait for RECORDING ENDED', output.getvalue())
            self.assertEqual(notify.call_args.args[0][-2], 'Lid recording ended')
        else:
            self.assertNotIn('RECORDING READY', output.getvalue())
            self.assertNotIn('RECORDING ENDED', output.getvalue())
            self.assertEqual(notify.call_args.args[0][-2], 'Lid recorder did not become ready')
        return output.getvalue()

    def test_root_only_ready_marker_does_not_require_read_access(self):
        self.run_parent()

    def test_notification_timeout_does_not_abort_capture(self):
        output = self.run_parent(subprocess.TimeoutExpired('notify-send', 5))
        self.assertIn('Desktop notice unavailable', output)

    def test_notification_oserror_does_not_abort_capture(self):
        self.run_parent(OSError('no desktop bus'))

    def test_nonzero_exit_notifies_without_claiming_a_pass(self):
        output = self.run_parent(exit_code=2)
        self.assertIn('status 2; results need review', output)

    def test_exit_before_readiness_does_not_request_closure(self):
        output = self.run_parent(exit_code=126, ready=False)
        self.assertIn('Do not close the lid', output)

    def test_missing_notification_helper_is_optional(self):
        with patch.object(session.shutil, 'which', return_value=None), \
                patch.object(session.subprocess, 'run') as notify:
            session.desktop_notice('title', 'message')
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
