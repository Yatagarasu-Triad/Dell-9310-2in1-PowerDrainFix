"""All hardware interaction is mocked or uses temporary regular files."""
import contextlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('diagnose', Path(__file__).with_name('xps-s0ix-diagnose.py'))
assert spec is not None and spec.loader is not None
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class CommandDeadlineTests(unittest.TestCase):
    def test_unset_deadline_preserves_requested_timeout(self):
        with patch.object(d, 'COMMAND_DEADLINE', None), \
                patch.object(d.time, 'clock_gettime') as clock, \
                patch.object(d.subprocess, 'run', return_value=d.subprocess.CompletedProcess(['capture'], 0, 'ok', '')) as run:
            result = d.command(['capture'], timeout=7)
        self.assertEqual(result['stdout'], 'ok')
        self.assertEqual(run.call_args.kwargs['timeout'], 7)
        clock.assert_not_called()

    def test_expired_deadline_skips_subprocess(self):
        for now in (100, 101):
            with self.subTest(now=now), patch.object(d, 'COMMAND_DEADLINE', 100), \
                    patch.object(d.time, 'clock_gettime', return_value=now) as clock, \
                    patch.object(d.subprocess, 'run') as run:
                result = d.command(['capture'])
                self.assertTrue(result['deadline_exceeded'])
                self.assertIn('deadline exceeded', result['error'])
                run.assert_not_called()
                clock.assert_called_once_with(d.time.CLOCK_BOOTTIME)

    def test_timeout_clamped_to_remaining_boottime_budget(self):
        for requested, expected in ((15, 2.5), (1, 1), (None, 2.5)):
            with self.subTest(requested=requested), patch.object(d, 'COMMAND_DEADLINE', 100), \
                    patch.object(d.time, 'clock_gettime', return_value=97.5), \
                    patch.object(d.subprocess, 'run', return_value=d.subprocess.CompletedProcess(['capture'], 0, '', '')) as run:
                d.command(['capture'], timeout=requested)
                self.assertEqual(run.call_args.kwargs['timeout'], expected)

    def test_deadline_is_recomputed_for_each_command(self):
        with patch.object(d, 'COMMAND_DEADLINE', 100), \
                patch.object(d.time, 'clock_gettime', side_effect=[99, 100]), \
                patch.object(d.subprocess, 'run', side_effect=d.subprocess.TimeoutExpired(['capture'], 1)) as run:
            self.assertIn('error', d.command(['capture']))
            self.assertTrue(d.command(['capture'])['deadline_exceeded'])
            run.assert_called_once()
            self.assertEqual(run.call_args.kwargs['timeout'], 1)


class DeltaTests(unittest.TestCase):
    def test_residency(self):
        self.assertEqual(d.counter_delta('3653015005', '4252656459', 600)['status'], 'ok')
        self.assertAlmostEqual(d.counter_delta(1, 600000001, 600)['percent'], 100)

    def test_reset_wrap_not_reported_as_residency(self):
        result = d.counter_delta(900, 5, 600)
        self.assertEqual(result['status'], 'reset_or_wrap')
        self.assertIsNone(result['seconds'])
        self.assertIsNone(result['percent'])

    def test_missing_and_invalid_elapsed(self):
        self.assertEqual(d.counter_delta({'error': 'permission'}, 5, 1)['status'], 'unavailable')
        self.assertIsNone(d.counter_delta(1, 2, 0)['percent'])
        self.assertEqual(d.counter_delta(1, 2000001, 1)['status'], 'implausible')

    def test_interrupt_reset(self):
        result = d.interrupt_deltas('1: 10 20 driver\n2: 5 0 timer', '1: 12 30 driver\n2: 1 0 timer')
        self.assertEqual(result['1']['delta'], 12)
        self.assertEqual(result['2']['status'], 'reset_or_wrap')
        self.assertIsNone(result['2']['delta'])

    def test_compare_uses_boottime_not_wall_or_monotonic(self):
        before = {'clock': {'wall': 100, 'boottime': 100, 'monotonic': 100},
                  's0ix_us': 1, 'pc10_us': 900, 'interrupts': '', 'runtime_pm': {},
                  'battery': {'BAT0': {'charge_now': '1000', 'energy_now': '10000', 'capacity': '80'}}}
        after = {'clock': {'wall': 2000, 'boottime': 700, 'monotonic': 102},
                 's0ix_us': 600000001, 'pc10_us': 10, 'interrupts': '', 'runtime_pm': {},
                 'battery': {'BAT0': {'charge_now': '900', 'energy_now': '9500', 'capacity': '79'}}}
        result = d.compare(before, after)
        self.assertEqual(result['elapsed_boottime_seconds'], 600)
        self.assertEqual(result['s0ix']['percent'], 100)
        self.assertEqual(result['pc10']['status'], 'reset_or_wrap')
        self.assertEqual(result['battery']['BAT0']['energy_now_delta_after_minus_before'], -500)
        before['acpi_interrupts'] = {'counts': {'sci': 10, 'gpe_all': 20}}
        after['acpi_interrupts'] = {'counts': {'sci': 15, 'gpe_all': 1}}
        result = d.compare(before, after)
        self.assertEqual(result['acpi_interrupts']['sci']['delta'], 5)
        self.assertEqual(result['acpi_interrupts']['gpe_all']['status'], 'reset_or_wrap')


class SafetyTests(unittest.TestCase):
    def test_raw_lid_is_strict_and_requires_all_devices(self):
        for states in ([], [{'SW_LID': False}], [{'SW_LID': 'true'}],
                       [{'SW_LID': True}, {'SW_LID': False}],
                       [{'SW_LID': True}, {'error': 'permission'}]):
            with self.subTest(states=states), self.assertRaises(d.SafetyError):
                d.require_lid(states, True)
        d.require_lid([{'SW_LID': True}], True)
        d.require_lid([{'SW_LID': False}], False)

    def test_missing_consent_has_no_side_effects(self):
        args = d.parser().parse_args(['open'])
        with patch.object(d, 'command') as cmd, patch.object(d, 'read') as read:
            with self.assertRaises(d.SafetyError):
                d.main(['open'])
            cmd.assert_not_called()
            read.assert_not_called()
        self.assertFalse(args.allow_suspend)

    def test_duration_bounds(self):
        for duration in ('0', '-1', '901'):
            args = d.parser().parse_args(['open', '--allow-suspend', '--duration', duration])
            with self.assertRaises(d.SafetyError):
                d.preflight(args)

    def test_preflight_refuses_nonroot(self):
        args = d.parser().parse_args(['open', '--allow-suspend'])
        with patch.object(d.os, 'geteuid', return_value=1000), self.assertRaises(d.SafetyError):
            d.preflight(args)

    def test_preflight_requires_s2idle_and_disabled_pm_test_and_wakeup(self):
        args = d.parser().parse_args(['open', '--allow-suspend'])
        for sleep_mode, pm_test, wakeup in (('[deep] s2idle', '[none]', 'enabled'),
                                           ('[s2idle]', 'none [devices]', 'enabled'),
                                           ('[s2idle]', '[none]', 'disabled')):
            with self.subTest(sleep_mode=sleep_mode, pm_test=pm_test, wakeup=wakeup):
                values = {'/sys/power/mem_sleep': sleep_mode, '/sys/power/pm_test': pm_test,
                          '/sys/class/rtc/rtc0/device/power/wakeup': wakeup}
                with patch.object(d.os, 'geteuid', return_value=0), \
                        patch.object(Path, 'glob', return_value=[]), \
                        patch.object(d, 'read', side_effect=lambda p: values.get(str(p), '')), \
                        patch.object(d, 'raw_lids', return_value=[{'SW_LID': False}]), \
                        self.assertRaises(d.SafetyError):
                    d.preflight(args)

    def test_existing_alarm_not_overwritten_including_past(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'wakealarm'
            for value in ('123', 'bogus'):
                path.write_text(value)
                alarm = d.RTCAlarm(path)
                with self.assertRaises(d.SafetyError):
                    alarm.arm(600)
                self.assertEqual(path.read_text(), value)
                self.assertEqual(alarm.cleanup(), 'not armed')

    def test_own_alarm_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'wakealarm'
            path.write_text('')
            (Path(temp) / 'since_epoch').write_text('1000')
            alarm = d.RTCAlarm(path)
            alarm.arm(600)
            self.assertEqual(path.read_text(), '1600')
            self.assertEqual(alarm.cleanup(), 'cleared owned alarm')
            self.assertEqual(path.read_text(), '0')

    def test_alarm_changed_by_other_process_is_not_cleared(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'wakealarm'
            path.write_text('')
            (Path(temp) / 'since_epoch').write_text('1000')
            alarm = d.RTCAlarm(path)
            alarm.arm(600)
            path.write_text('42')
            with self.assertRaises(d.SafetyError):
                alarm.cleanup()
            self.assertEqual(path.read_text(), '42')

    def test_expired_alarm_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'wakealarm'
            path.write_text('')
            (Path(temp) / 'since_epoch').write_text('1000')
            alarm = d.RTCAlarm(path)
            alarm.arm(600)
            path.write_text('')
            self.assertEqual(alarm.cleanup(), 'already expired/cleared')

    def test_alarm_readback_mismatch_blocks_suspend(self):
        alarm = d.RTCAlarm('/fake/wakealarm')
        with patch.object(d, 'read', side_effect=['', '1000', '42']), patch.object(Path, 'write_text'):
            with self.assertRaises(d.SafetyError):
                alarm.arm(600)

    def test_unknown_rtc_clock_prevents_arming(self):
        alarm = d.RTCAlarm('/fake/wakealarm')
        with patch.object(d, 'read', side_effect=['', {'error': 'unavailable'}]), \
                patch.object(Path, 'write_text') as write:
            with self.assertRaises(d.SafetyError):
                alarm.arm(600)
            write.assert_not_called()

    def test_late_resume_still_queries_journal(self):
        rows = self.rows(['PM: suspend entry (s2idle)', 'PM: suspend exit'])
        with patch.object(d, 'journal', return_value=rows), \
                patch.object(d.time, 'clock_gettime', return_value=1000):
            self.assertEqual(d.await_suspend('cursor', 600)['status'], 'verified_entry_exit')

    def test_no_journal_is_unverified_even_after_elapsed_time(self):
        with patch.object(d.time, 'clock_gettime', return_value=700):
            self.assertEqual(d.await_suspend(None, 600)['status'], 'unverified')

    def test_entry_only_or_exit_only_not_verification(self):
        for messages in (['PM: suspend entry (s2idle)'], ['PM: suspend exit'],
                         ['PM: suspend exit', 'PM: suspend entry (s2idle)'],
                         ['PM: suspend entry (deep)', 'PM: suspend exit']):
            self.assertIsNone(d.suspend_evidence(self.rows(messages)))

    @staticmethod
    def rows(messages):
        return {'returncode': 0, 'stdout': '\n'.join(json.dumps({'MESSAGE': m}) for m in messages)}

    def test_journal_pair_required(self):
        rows = self.rows(['PM: suspend entry (s2idle)', 'noise', 'PM: suspend exit'])
        self.assertIsNotNone(d.suspend_evidence(rows))
        rows['returncode'] = 1
        self.assertIsNone(d.suspend_evidence(rows))

    def run_experiment(self, mode, states, command_result=None):
        args = d.parser().parse_args([mode, '--allow-suspend'])
        report = {}
        alarm = Mock()
        guard = Mock()
        guard.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(d, 'preflight'))
            stack.enter_context(patch.object(d, 'RTCAlarm', return_value=alarm))
            inhibit = stack.enter_context(patch.object(d, 'inhibitor', return_value=contextlib.nullcontext(guard)))
            stack.enter_context(patch.object(d, 'snapshot', return_value={}))
            stack.enter_context(patch.object(d, 'journal_cursor', return_value='fresh-cursor'))
            stack.enter_context(patch.object(d, 'raw_lids', side_effect=states))
            stack.enter_context(patch.object(d, 'await_suspend', return_value={'status': 'verified_entry_exit'}))
            cmd = stack.enter_context(patch.object(d, 'command', return_value=command_result or {'returncode': 0}))
            try:
                d.experiment(args, Path(temp), report)
            except d.SafetyError:
                pass
        return alarm, cmd, inhibit, report

    def test_closed_manual_rechecks_true_immediately_before_suspend(self):
        # Lid opens after before-capture but before the suspend command.
        alarm, cmd, _, _ = self.run_experiment('closed-manual',
                                              [[{'SW_LID': True}], [{'SW_LID': True}], [{'SW_LID': False}]])
        alarm.arm.assert_called_once_with(600)
        cmd.assert_not_called()
        alarm.cleanup.assert_called_once()

    def test_closed_manual_suspends_only_with_raw_true(self):
        alarm, cmd, inhibit, report = self.run_experiment('closed-manual',
                                                        [[{'SW_LID': True}]] * 3)
        self.assertEqual(cmd.call_args.args[0], ['systemctl', '--no-ask-password', 'suspend'])
        inhibit.assert_called_once_with('handle-lid-switch')
        alarm.cleanup.assert_called_once()
        self.assertEqual(report['suspend']['status'], 'verified_entry_exit')

    def test_normal_lid_never_calls_suspend_or_inhibits(self):
        alarm, cmd, inhibit, _ = self.run_experiment('normal-lid', [[{'SW_LID': False}]] * 2)
        cmd.assert_not_called()
        inhibit.assert_not_called()
        alarm.arm.assert_called_once_with(660)
        alarm.cleanup.assert_called_once()

    def test_alarm_cleaned_if_suspend_command_fails(self):
        alarm, cmd, _, _ = self.run_experiment('open', [[{'SW_LID': False}]] * 2,
                                              command_result={'returncode': 1})
        cmd.assert_called_once()
        alarm.cleanup.assert_called_once()

    def test_closed_manual_times_out_without_issuing_suspend(self):
        args = d.parser().parse_args(['closed-manual', '--allow-suspend'])
        guard = Mock()
        guard.poll.return_value = None
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(d, 'preflight'), \
                patch.object(d, 'RTCAlarm') as rtc, \
                patch.object(d, 'inhibitor', return_value=contextlib.nullcontext(guard)), \
                patch.object(d, 'raw_lids', return_value=[{'SW_LID': False}]), \
                patch.object(d.time, 'clock_gettime', side_effect=[0, 1000]), \
                patch.object(d, 'command') as cmd, self.assertRaises(d.SafetyError):
            try:
                d.experiment(args, Path(temp), {})
            finally:
                cmd.assert_not_called()
                rtc.return_value.arm.assert_not_called()
                rtc.return_value.cleanup.assert_called_once()

    def test_baseline_only_writes_artifacts(self):
        sample = {'clock': {'wall': 100, 'boottime': 100, 'monotonic': 100},
                  's0ix_us': 1, 'pc10_us': 1, 'battery': {}, 'runtime_pm': {}, 'interrupts': ''}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(d, 'metadata', return_value={}), \
                patch.object(d, 'snapshot', return_value=sample), \
                patch.object(d, 'journal_cursor', return_value=None), \
                patch.object(d, 'inhibitor') as inhibit, \
                patch.object(d, 'RTCAlarm') as rtc, \
                patch.object(d, 'command') as cmd, patch('builtins.print'):
            self.assertEqual(d.main(['baseline', '--output-root', temp]), 0)
            report = json.loads(next(Path(temp).glob('*/summary.json')).read_text())
            self.assertEqual(report['status'], 'captured')
            self.assertEqual(len(list(Path(temp).glob('*/after.json'))), 1)
            inhibit.assert_not_called()
            rtc.assert_not_called()
            cmd.assert_not_called()

    def test_inhibitor_release_on_exception(self):
        child = Mock()
        child.stdout.readline.return_value = b'READY\n'
        child.poll.return_value = None
        with patch.object(d.subprocess, 'Popen', return_value=child), \
                patch.object(d.select, 'select', return_value=([child.stdout], [], [])):
            with self.assertRaises(ValueError):
                with d.inhibitor('handle-lid-switch'):
                    raise ValueError('exercise finally')
        child.stdin.close.assert_called_once()
        child.wait.assert_called_once()
        child.stdout.close.assert_called_once()


class RawReportTests(unittest.TestCase):
    @staticmethod
    def initial(lid=False, tablet=False, path='/dev/input/event0'):
        return {'kind': 'initial', 'device': {'path': path},
                'state': {'SW_LID': lid, 'SW_TABLET_MODE': tablet}}

    @staticmethod
    def event(switch, value, path='/dev/input/event0'):
        return {'kind': 'event', 'path': path, 'switch': switch, 'value': value}

    @staticmethod
    def sample(raw=False, logged=False):
        return {'clock': {'boottime': 10}, 'raw_lids': [{'device': '/dev/input/event0', 'SW_LID': raw}],
                'LidClosed': {'returncode': 0, 'stdout': 'b ' + str(logged).lower()}}

    @staticmethod
    def analyze(rows, samples=None):
        return d.interpret_raw('\n'.join(json.dumps(row) for row in rows), samples or [])

    def test_static_snapshot_does_not_claim_physical_cycle(self):
        report = self.analyze([self.initial()], [self.sample(), self.sample()])
        self.assertEqual(report['status'], 'no_lid_cycle_observed')
        self.assertFalse(report['complete_open_close_open_cycle'])

    def test_cycle_and_tablet_toggles_deduplicate_events_and_states(self):
        rows = [self.initial(), self.event('SW_LID', 1),
                {'kind': 'state_change', 'path': '/dev/input/event0', 'state': {'SW_LID': True}},
                self.event('SW_TABLET_MODE', 1), self.event('SW_LID', 0), self.event('SW_TABLET_MODE', 0)]
        report = self.analyze(rows)
        self.assertEqual(report['status'], 'raw_cycle_observed')
        self.assertEqual(report['probe']['close_transitions'], 1)
        self.assertEqual(report['probe']['open_transitions'], 1)
        self.assertEqual(report['probe']['tablet_toggles'], 2)

    def test_initial_closed_then_open_is_incomplete_not_a_full_cycle(self):
        report = self.analyze([self.initial(True), self.event('SW_LID', 0)])
        self.assertEqual(report['status'], 'incomplete_lid_cycle')

    def test_transitions_from_different_devices_do_not_form_cycle(self):
        report = self.analyze([self.initial(), self.initial(True, path='/dev/input/event1'),
                               self.event('SW_LID', 1), self.event('SW_LID', 0, '/dev/input/event1')])
        self.assertEqual(report['status'], 'incomplete_lid_cycle')

    def test_dropped_events_do_not_bridge_cycle(self):
        rows = [self.initial(), self.event('SW_LID', 1),
                {'kind': 'syn_dropped', 'path': '/dev/input/event0'},
                {'kind': 'resync', 'path': '/dev/input/event0', 'state': {'SW_LID': False}}]
        report = self.analyze(rows)
        self.assertEqual(report['status'], 'incomplete_lid_cycle')
        self.assertEqual(report['syn_dropped'], 1)

    def test_samples_can_establish_cycle_and_logind_mismatch(self):
        report = self.analyze([], [self.sample(False, False), self.sample(True, False), self.sample(False, False)])
        self.assertEqual(report['status'], 'raw_cycle_observed')
        self.assertEqual(report['samples']['cycle_devices'], ['/dev/input/event0'])
        self.assertEqual(report['logind_comparison']['compared_samples'], 3)
        self.assertEqual(report['logind_comparison']['mismatch_samples'], 1)

    def test_unreadable_or_conflicting_lids_not_counted_as_logind_mismatch(self):
        sample = self.sample(True, False)
        sample['raw_lids'].append({'device': '/dev/input/event1', 'SW_LID': False})
        report = self.analyze([], [sample, {'raw_lids': [{'error': 'permission'}]}])
        self.assertEqual(report['logind_comparison']['mismatch_samples'], 0)
        self.assertEqual(report['logind_comparison']['unavailable_or_ambiguous_samples'], 2)

    def test_disappearing_sampled_device_does_not_bridge_cycle(self):
        missing = {'raw_lids': [{'device': '/dev/input/event1', 'SW_LID': False}]}
        report = self.analyze([], [self.sample(False), self.sample(True), missing, self.sample(False)])
        self.assertEqual(report['status'], 'incomplete_lid_cycle')

    def test_invalid_or_missing_data_cannot_claim_observed(self):
        for data in ('not JSON\n[]', {'error': 'missing'}):
            report = d.interpret_raw(data, [])
            self.assertEqual(report['status'], 'raw_data_unavailable')


class RawReadinessTests(unittest.TestCase):
    def setUp(self):
        # Any newly added cleanup path must remain hardware-independent as well.
        self.raw = self.enterContext(patch.object(d, 'raw_lids', return_value=[{'SW_LID': False}]))

    def test_probe_readiness_waits_for_complete_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'probe-ready'
            child, guard = Mock(), Mock()
            child.poll.return_value = guard.poll.return_value = None
            with patch.object(d.time, 'sleep', side_effect=lambda _: path.write_text('ready\n')) as sleep:
                d.wait_probe_ready(child, guard, path)
            sleep.assert_called_once()

    def test_probe_readiness_timeout_and_early_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            child, guard = Mock(), Mock()
            child.poll.return_value = guard.poll.return_value = None
            with patch.object(d.time, 'clock_gettime', side_effect=[0, 11]), self.assertRaises(d.SafetyError):
                d.wait_probe_ready(child, guard, Path(temp) / 'missing')
            child.poll.return_value = 2
            with self.assertRaises(d.SafetyError):
                d.wait_probe_ready(child, guard, Path(temp) / 'missing')

    def test_ready_marker_is_exclusive_and_cleaned_even_after_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ready'
            with self.assertRaises(ValueError):
                with d.ready_marker(path, {'status': 'ready'}):
                    self.assertEqual(json.loads(path.read_text())['status'], 'ready')
                    raise ValueError('interrupted')
            self.assertFalse(path.exists())
            path.write_text('belongs to someone else')
            with self.assertRaises(FileExistsError), d.ready_marker(path, {}):
                pass
            self.assertEqual(path.read_text(), 'belongs to someone else')

    def test_ready_marker_does_not_delete_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path, replacement = Path(temp) / 'ready', Path(temp) / 'replacement'
            replacement.write_text('other')
            with d.ready_marker(path, {}):
                replacement.replace(path)
            self.assertEqual(path.read_text(), 'other')

    def test_prompt_only_after_probe_and_inhibitor_ready_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            directory, ready_file = Path(temp), Path(temp) / 'worker-ready'
            child, guard = Mock(), Mock()
            child.poll.side_effect = [None, None, None, 0, 0]
            child.returncode = 0
            guard.poll.return_value = None

            def spawn(argv, **kwargs):
                self.assertFalse(ready_file.exists())
                self.assertIn('--ready-file', argv)
                Path(argv[argv.index('--ready-file') + 1]).write_text('ready\n')
                rows = [RawReportTests.initial(), RawReportTests.event('SW_LID', 1),
                        RawReportTests.event('SW_LID', 0)]
                (directory / 'raw-lid.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
                return child

            def prompt(*args, **kwargs):
                self.assertTrue((directory / 'probe-ready').exists())
                self.assertTrue(ready_file.exists())
                self.assertEqual(json.loads(ready_file.read_text())['status'], 'ready')
                self.assertGreater(guard.poll.call_count, 0)

            with patch.object(d.subprocess, 'Popen', side_effect=spawn), \
                    patch.object(d, 'raw_lids', return_value=[]), \
                    patch.object(d, 'command', return_value={}), \
                    patch.object(d.time, 'sleep'), patch('builtins.print', side_effect=prompt) as printed:
                report = d.observe_raw(30, directory, guard, ready_file)
            printed.assert_called_once()
            self.assertEqual(report['status'], 'raw_cycle_observed')
            self.assertFalse(ready_file.exists())
            self.assertFalse((directory / 'probe-ready').exists())
            child.terminate.assert_not_called()

    def test_samples_are_flushed_before_interruption_and_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            child, guard = Mock(), Mock()
            child.poll.return_value = guard.poll.return_value = None
            child.returncode = -15
            sample = {'raw_lids': [{'SW_LID': False}], 'acpi_lid': {'files': {'lid/state': 'state: open'}}}

            def interrupt_after_sample(_):
                saved = (directory / 'raw-logind-samples.jsonl').read_text().splitlines()
                self.assertEqual([json.loads(row) for row in saved], [sample])
                self.assertFalse((directory / 'raw-logind-samples.json').exists())
                raise KeyboardInterrupt('simulated cancellation')

            with patch.object(d.subprocess, 'Popen', return_value=child), \
                    patch.object(d, 'wait_probe_ready'), \
                    patch.object(d, 'raw_sample', return_value=sample), \
                    patch.object(d.time, 'sleep', side_effect=interrupt_after_sample), \
                    patch('builtins.print'), self.assertRaises(KeyboardInterrupt):
                d.observe_raw(30, directory, guard, acpi_lid=True)
            self.assertEqual(json.loads((directory / 'raw-logind-samples.json').read_text()), [sample])
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=3)

    def test_sample_stream_never_overwrites_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'raw-logind-samples.jsonl'
            path.write_text('existing evidence\n')
            with patch.object(d.subprocess, 'Popen') as spawn, self.assertRaises(FileExistsError):
                d.observe_raw(30, Path(temp), Mock())
            spawn.assert_not_called()
            self.assertEqual(path.read_text(), 'existing evidence\n')

    def test_lost_inhibitor_before_publication_never_prompts(self):
        with tempfile.TemporaryDirectory() as temp:
            child, guard = Mock(), Mock()
            child.poll.return_value = None
            child.returncode = 2
            guard.poll.return_value = 1
            ready_file = Path(temp) / 'worker-ready'
            with patch.object(d.subprocess, 'Popen', return_value=child), \
                    patch.object(d, 'wait_probe_ready'), \
                    patch('builtins.print') as printed, self.assertRaises(d.SafetyError):
                d.observe_raw(30, Path(temp), guard, ready_file)
            printed.assert_not_called()
            child.terminate.assert_called_once()
            self.assertFalse(ready_file.exists())

    def test_overlong_child_clears_marker_and_is_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            child, guard = Mock(), Mock()
            child.poll.return_value = guard.poll.return_value = None
            child.returncode = 2
            ready_file = Path(temp) / 'worker-ready'
            with patch.object(d.subprocess, 'Popen', return_value=child), \
                    patch.object(d, 'wait_probe_ready'), \
                    patch.object(d, 'clocks', return_value={}), \
                    patch.object(d.time, 'clock_gettime', side_effect=[0, 41]), \
                    patch('builtins.print'), self.assertRaises(d.SafetyError):
                d.observe_raw(30, Path(temp), guard, ready_file)
            self.assertFalse(ready_file.exists())
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=3)

    def test_failed_readiness_never_prompts_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as temp:
            child, guard = Mock(), Mock()
            child.poll.return_value = None
            child.returncode = 2
            ready_file = Path(temp) / 'worker-ready'
            with patch.object(d.subprocess, 'Popen', return_value=child), \
                    patch.object(d, 'wait_probe_ready', side_effect=d.SafetyError('not ready')), \
                    patch('builtins.print') as printed, self.assertRaises(d.SafetyError):
                d.observe_raw(30, Path(temp), guard, ready_file)
            printed.assert_not_called()
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=3)
            self.assertFalse(ready_file.exists())
            self.assertTrue((Path(temp) / 'raw-lid-report.json').exists())

    def test_main_raw_without_cycle_has_nonzero_status_and_sufficient_inhibitor_lifetime(self):
        sample = {'clock': {'wall': 100, 'boottime': 100, 'monotonic': 100},
                  's0ix_us': 1, 'pc10_us': 1, 'battery': {}, 'runtime_pm': {}, 'interrupts': ''}
        result = RawReportTests.analyze([RawReportTests.initial()])
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'lid_probe.py').write_text('')
            with patch.object(d, 'ROOT', Path(temp)), \
                    patch.object(d, 'metadata', return_value={}), \
                    patch.object(d, 'snapshot', return_value=sample), \
                    patch.object(d, 'journal_cursor', return_value=None), \
                    patch.object(d, 'inhibitor', return_value=contextlib.nullcontext(Mock())) as inhibit, \
                    patch.object(d, 'observe_raw', return_value=result) as observe, \
                    patch.object(d, 'command') as cmd, patch('builtins.print'):
                ready = Path(temp) / 'worker-ready'
                self.assertEqual(d.main(['raw-lid', '--output-root', temp, '--ready-file', str(ready), '--acpi-lid']), 2)
                self.assertEqual(observe.call_args.args[3], ready)
                self.assertTrue(observe.call_args.kwargs['acpi_lid'])
                inhibit.assert_called_once_with('sleep:handle-lid-switch',
                                                lifetime=30 + d.PROBE_READY_TIMEOUT + d.RAW_CLEANUP_MARGIN)
                cmd.assert_not_called()
            report = json.loads(next(Path(temp).glob('*/summary.json')).read_text())
            self.assertEqual(report['status'], 'no_lid_cycle_observed')
            self.assertEqual(report['raw_reopen']['status'], 'open_verified')
            self.raw.assert_called_once()

    def test_probe_failure_or_interruption_reopens_before_inhibitor_release(self):
        sample = {'clock': {'wall': 100, 'boottime': 100, 'monotonic': 100},
                  's0ix_us': 1, 'pc10_us': 1, 'battery': {}, 'runtime_pm': {}, 'interrupts': ''}
        for error in (d.SafetyError('probe failed'), OSError('spawn failed'), KeyboardInterrupt('stopped')):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as temp:
                (Path(temp) / 'lid_probe.py').write_text('')
                events = []
                held = [False]
                states = iter([[{'SW_LID': True}], [{'SW_LID': False}]])
                guard = Mock()
                guard.poll.return_value = None

                @contextlib.contextmanager
                def guarded(*args, held=held, events=events, guard=guard, **kwargs):
                    held[0] = True
                    events.append('inhibit')
                    try:
                        yield guard
                    finally:
                        held[0] = False
                        events.append('release')

                def fresh_lids(held=held, events=events, states=states):
                    self.assertTrue(held[0], 'Lid check must precede inhibitor release')
                    events.append('lid-check')
                    return next(states)

                with patch.object(d, 'ROOT', Path(temp)), \
                        patch.object(d, 'metadata', return_value={}), \
                        patch.object(d, 'snapshot', return_value=sample), \
                        patch.object(d, 'journal_cursor', return_value=None), \
                        patch.object(d, 'inhibitor', side_effect=guarded), \
                        patch.object(d, 'observe_raw', side_effect=error), \
                        patch.object(d, 'raw_lids', side_effect=fresh_lids), \
                        patch.object(d.time, 'sleep'), \
                        patch.object(d, 'command') as command, patch('builtins.print'):
                    self.assertEqual(d.main(['raw-lid', '--output-root', temp]), 2)
                    command.assert_not_called()
                self.assertEqual(events, ['inhibit', 'lid-check', 'lid-check', 'release'])
                report = json.loads(next(Path(temp).glob('*/summary.json')).read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['raw_reopen']['status'], 'open_verified')


class ReopenGraceTests(unittest.TestCase):
    def setUp(self):
        self.raw = self.enterContext(patch.object(d, 'raw_lids', return_value=[{'SW_LID': False}]))
        self.now = 0.0
        self.enterContext(patch.object(d.time, 'clock_gettime', side_effect=lambda _: self.now))
        self.sleep = self.enterContext(patch.object(d.time, 'sleep', side_effect=self.advance))
        self.printed = self.enterContext(patch('builtins.print'))
        self.guard = Mock()
        self.guard.poll.return_value = None

    def advance(self, seconds):
        self.now += seconds

    def test_open_lid_releases_without_wait_or_warning(self):
        result = d.reopen_before_release(self.guard, 60)
        self.assertEqual(result['status'], 'open_verified')
        self.raw.assert_called_once()
        self.sleep.assert_not_called()
        self.printed.assert_not_called()

    def test_closed_lid_can_reopen_during_grace(self):
        self.raw.side_effect = [[{'SW_LID': True}], [{'SW_LID': False}]]
        result = d.reopen_before_release(self.guard, 60)
        self.assertEqual(result['status'], 'open_verified')
        self.assertEqual(result['elapsed_seconds'], 0.5)
        self.assertIn('Reopen the lid now', self.printed.call_args.args[0])

    def test_closed_or_unknown_lid_warns_and_waits_at_most_ten_seconds(self):
        for states in ([{'SW_LID': True}], [], [{'error': 'unreadable'}],
                       [{'SW_LID': False}, {'SW_LID': True}]):
            with self.subTest(states=states):
                self.now = 0
                self.raw.return_value = states
                self.printed.reset_mock()
                result = d.reopen_before_release(self.guard, 60)
                self.assertEqual(result['status'], 'released_unverified')
                self.assertEqual(self.now, 10)
                self.assertEqual(self.printed.call_count, 2)
                self.assertIn('Normal lid policy may suspend', self.printed.call_args.args[0])

    def test_remaining_inhibitor_budget_shortens_grace(self):
        self.raw.return_value = [{'SW_LID': True}]
        result = d.reopen_before_release(self.guard, 2.25)
        self.assertEqual(result['status'], 'released_unverified')
        self.assertEqual(self.now, 2.25)
        self.assertEqual(self.sleep.call_args.args[0], 0.25)

    def test_exhausted_budget_still_checks_fresh_state_without_sleeping(self):
        self.raw.return_value = []
        result = d.reopen_before_release(self.guard, -1)
        self.assertEqual(result['status'], 'released_unverified')
        self.raw.assert_called_once()
        self.sleep.assert_not_called()

    def test_lost_guard_warns_without_waiting(self):
        self.raw.return_value = [{'SW_LID': True}]
        self.guard.poll.return_value = 1
        result = d.reopen_before_release(self.guard, 60)
        self.assertEqual(result['status'], 'inhibitor_lost')
        self.sleep.assert_not_called()
        self.assertIn('Inhibitor already ended', self.printed.call_args.args[0])

    def test_read_error_is_unknown_not_permission_to_release_immediately(self):
        self.raw.side_effect = [OSError('device unavailable'), [{'SW_LID': False}]]
        result = d.reopen_before_release(self.guard, 60)
        self.assertEqual(result['status'], 'open_verified')
        self.assertEqual(self.now, 0.5)


class AcpiCaptureTests(unittest.TestCase):
    def test_acpi_lid_state_parser(self):
        for text, expected in (('state:      closed', True), ('state: open\n', False),
                               ('state: CLOSED', True), ('closed', None),
                               ('state: closed extra', None), ({'error': 'denied'}, None)):
            with self.subTest(text=text):
                self.assertIs(d.acpi_lid_closed(text), expected)

    def test_lid_state_and_button_metadata_from_existing_exports_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lid = root / 'lid' / 'LID0'
            params = root / 'parameters'
            devices = root / 'devices'
            acpi_device = devices / 'PNP0C0D:00'
            unrelated = devices / 'PNP0C0E:00'
            for directory in (lid, params, acpi_device, unrelated):
                directory.mkdir(parents=True)
            (lid / 'state').write_text('state: closed')
            (lid / 'ignored').write_text('not a lid state')
            (params / 'lid_init_state').write_text('method')
            (params / 'lid_report_interval').write_text('500')
            (acpi_device / 'path').write_text('\\_SB.LID0')
            (acpi_device / 'hid').write_text('PNP0C0D')
            (unrelated / 'path').write_text('not a lid')
            with patch.object(Path, 'write_text', side_effect=AssertionError('capture must not write')):
                states = d.acpi_lid_states(root / 'lid')
                metadata = d.acpi_button_metadata(params, devices)
            self.assertEqual(states['files'], {str(lid / 'state'): 'state: closed'})
            self.assertEqual(len(metadata['parameters']['files']), 2)
            self.assertEqual(list(metadata['lid_devices']), [str(acpi_device)])
            self.assertEqual(metadata['lid_devices'][str(acpi_device)]['files'][str(acpi_device / 'path')], '\\_SB.LID0')

    def test_missing_acpi_interfaces_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / 'missing'
            self.assertIn(str(missing), d.acpi_lid_states(missing)['unavailable'])
            self.assertIn(str(missing), d.acpi_irq_snapshot(root=missing)['unavailable'])

    def test_irq_snapshot_full_vs_selected_and_count_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, value in {'sci': '123', 'gpe_all': '  0042', 'gpe01': ' 9 enabled unmasked',
                                'gpe02': '-1 disabled', 'unparseable': 'not a counter'}.items():
                (root / name).write_text(value)
            with patch.object(d, 'read', wraps=d.read) as read:
                selected = d.acpi_irq_snapshot(selected=True, root=root)
                self.assertEqual(read.call_count, 2)
            full = d.acpi_irq_snapshot(root=root)
            self.assertEqual(selected['counts'], {str(root / 'sci'): 123, str(root / 'gpe_all'): 42})
            self.assertEqual(full['counts'][str(root / 'gpe01')], 9)
            self.assertIsNone(full['counts'][str(root / 'gpe02')])
            self.assertIsNone(full['counts'][str(root / 'unparseable')])

    def test_irq_deltas_are_nonnegative_or_explicitly_unavailable(self):
        before = {'counts': {'sci': 10, 'gpe_all': 100, 'denied': None, 'invalid': -1, 'gone': 2}}
        after = {'counts': {'sci': 15, 'gpe_all': 1, 'denied': 3, 'invalid': 0, 'new': 0}}
        result = d.acpi_irq_deltas(before, after)
        self.assertEqual(result['sci']['delta'], 5)
        self.assertEqual(result['gpe_all']['status'], 'reset_or_wrap')
        for key in ('gpe_all', 'denied', 'invalid', 'gone', 'new'):
            self.assertIsNone(result[key]['delta'])

    def test_snapshot_always_captures_acpi_metadata_and_full_irq_state(self):
        with patch.object(d, 'read', return_value=''), \
                patch.object(Path, 'glob', return_value=[]), \
                patch.object(d, 'runtime_pm', return_value={}), \
                patch.object(d, 'logind', return_value={}), \
                patch.object(d, 'raw_lids', return_value=[]), \
                patch.object(d, 'probe_snapshot', return_value={}), \
                patch.object(d, 'pmc_debugfs', return_value={}), \
                patch.object(d, 'acpi_lid_states', return_value={'files': {'lid/state': 'state: open'}}) as lids, \
                patch.object(d, 'acpi_button_metadata', return_value={'parameters': {}}) as button, \
                patch.object(d, 'acpi_irq_snapshot', return_value={'counts': {'sci': 10}}) as irqs:
            result = d.snapshot()
        self.assertEqual(result['acpi_lid']['files']['lid/state'], 'state: open')
        self.assertEqual(result['acpi_interrupts']['counts']['sci'], 10)
        self.assertIn('parameters', result['acpi_button'])
        lids.assert_called_once_with()
        button.assert_called_once_with()
        irqs.assert_called_once_with()

    def test_acpi_sampling_is_opt_in_and_keeps_busctl_timeout(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), \
                    patch.object(d, 'raw_lids', return_value=[{'SW_LID': False}]), \
                    patch.object(d, 'command', return_value={'returncode': 0, 'stdout': 'b false'}) as command, \
                    patch.object(d, 'acpi_lid_states', return_value={'files': {}}) as lids, \
                    patch.object(d, 'acpi_irq_snapshot', return_value={'counts': {}}) as irq:
                sample = d.raw_sample(acpi_lid=enabled)
                self.assertEqual(command.call_args.kwargs['timeout'], 2)
                if enabled:
                    lids.assert_called_once_with()
                    irq.assert_called_once_with(selected=True)
                    self.assertIn('acpi_clock', sample)
                    self.assertIn('acpi_finished_clock', sample)
                else:
                    lids.assert_not_called()
                    irq.assert_not_called()
                    self.assertNotIn('acpi_lid', sample)
        self.assertFalse(d.parser().parse_args(['raw-lid']).acpi_lid)
        self.assertTrue(d.parser().parse_args(['raw-lid', '--acpi-lid']).acpi_lid)


class AcpiCorrelationTests(unittest.TestCase):
    @staticmethod
    def sample(state: str | dict[str, str] = 'state: closed', raw=False, count=10):
        sample = RawReportTests.sample(raw=raw)
        sample['acpi_lid'] = {'files': {'lid/state': state}, 'unavailable': {}}
        sample['acpi_interrupts'] = {'counts': {'sci': count, 'gpe_all': count}}
        return sample

    def test_acpi_closure_with_raw_and_logind_false_is_not_raw_cycle_success(self):
        samples = [self.sample('state: open', count=10), self.sample(count=15),
                   self.sample('state: open', count=20)]
        report = RawReportTests.analyze([RawReportTests.initial()], samples)
        acpi = report['acpi_correlation']
        self.assertEqual(report['status'], 'no_lid_cycle_observed')
        self.assertEqual(acpi['closed_samples'], 1)
        self.assertEqual(acpi['closed_readings'], 1)
        self.assertEqual(acpi['compared_samples'], 3)
        self.assertEqual(acpi['disagreement_samples'], 1)
        self.assertEqual(acpi['acpi_closed_raw_open_samples'], 1)
        self.assertEqual(acpi['sampled_irq_deltas']['sci']['delta'], 10)
        self.assertIn('notification/button-to-input', acpi['interpretation'])
        self.assertIn('non-atomic', acpi['caveat'])
        self.assertTrue(any('ACPI closed samples: 1/3' in line for line in d.raw_report_lines(report)))

    def test_all_open_cannot_rule_out_physical_closure(self):
        report = d.interpret_acpi_samples([self.sample('state: open') for _ in range(176)])
        self.assertEqual(report['closed_samples'], 0)
        self.assertEqual(report['disagreement_samples'], 0)
        self.assertIn('physical closure is not ruled out', report['interpretation'])
        self.assertIn('may perturb firmware', report['caveat'])

    def test_partial_unreadable_and_conflicting_states_are_not_mismatches(self):
        missing = self.sample({'error': 'denied'})
        conflict = self.sample()
        conflict['acpi_lid']['files']['lid2/state'] = 'state: open'
        unknown_raw = self.sample()
        unknown_raw['raw_lids'] = []
        inaccessible_directory = self.sample()
        inaccessible_directory['acpi_lid']['unavailable']['lid2'] = {'error': 'denied'}
        report = d.interpret_acpi_samples([missing, conflict, unknown_raw, inaccessible_directory])
        self.assertEqual(report['unavailable_or_ambiguous_samples'], 4)
        self.assertEqual(report['disagreement_samples'], 0)
        self.assertEqual(report['closed_samples'], 3)

    def test_reverse_disagreement_and_examples_are_bounded(self):
        report = d.interpret_acpi_samples([self.sample('state: open', raw=True) for _ in range(20)])
        self.assertEqual(report['acpi_open_raw_closed_samples'], 20)
        self.assertEqual(len(report['examples']), 5)

    def test_no_samples_distinguishes_disabled_from_unavailable(self):
        self.assertFalse(d.interpret_acpi_samples([])['enabled'])
        report = d.interpret_acpi_samples([], enabled=True)
        self.assertTrue(report['enabled'])
        self.assertEqual(report['sample_count'], 0)
        self.assertIn('unverified', report['interpretation'])

    def test_observation_passes_flag_to_sampler_not_probe_and_retains_correlation(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'raw-lid.jsonl').write_text(json.dumps(RawReportTests.initial()) + '\n')
            child, guard = Mock(), Mock()
            child.poll.side_effect = [None, None, 0, 0]
            child.returncode = 0
            guard.poll.return_value = None
            with patch.object(d.subprocess, 'Popen', return_value=child) as popen, \
                    patch.object(d, 'wait_probe_ready'), \
                    patch.object(d, 'raw_sample', return_value=self.sample()) as sample, \
                    patch.object(d, 'raw_lids', side_effect=AssertionError('no actual input access')), \
                    patch.object(d.time, 'sleep'), patch('builtins.print'):
                report = d.observe_raw(30, directory, guard, acpi_lid=True)
            sample.assert_called_once_with(acpi_lid=True)
            self.assertNotIn('--acpi-lid', popen.call_args.args[0])
            self.assertEqual(report['acpi_correlation']['closed_samples'], 1)
            saved = json.loads((directory / 'raw-logind-samples.json').read_text())
            self.assertEqual(saved[0]['acpi_lid']['files']['lid/state'], 'state: closed')


class DebugfsTests(unittest.TestCase):
    def test_discovers_only_existing_requested_files_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'intel_pmc_core' / 'pmc0'
            directory.mkdir(parents=True)
            for name in ('lpm_status', 'lpm_requirements', 'substate_residencies', 'slp_s0_residency_usec', 'unrelated'):
                (directory / name).write_text(name)
            with patch.object(d, 'read', wraps=d.read) as read, \
                    patch.object(Path, 'write_text', side_effect=AssertionError('must not write')):
                result = d.pmc_debugfs(root)
            self.assertEqual(len(result['files']), 4)
            self.assertEqual(read.call_count, 4)
            self.assertNotIn(str(directory / 'unrelated'), result['files'])
            self.assertFalse(result['unavailable'])

    def test_both_pmc_layouts_and_nested_exports_are_discovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for layout in ('pmc_core', 'pmc_core/pmc0', 'intel_pmc_core'):
                directory = root / layout
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / 'slp_s0_residency_usec'
                path.write_text('123')
                paths.append(str(path))
            with patch.object(d, 'read', wraps=d.read) as read:
                result = d.pmc_debugfs(root)
            self.assertEqual(set(result['files']), set(paths))
            self.assertEqual(read.call_count, 3)
            self.assertFalse(result['unavailable'])

    def test_absent_or_denied_debugfs_root_reports_directory_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'missing'
            self.assertIn(str(root), d.pmc_debugfs(root)['unavailable'])
            with patch.object(Path, 'iterdir', side_effect=PermissionError(13, 'Permission denied', str(root))):
                result = d.pmc_debugfs(root)
            self.assertIn('Permission denied', result['unavailable'][str(root)]['error'])

    def test_inaccessible_pmc_subdirectory_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'pmc_core'
            directory.mkdir()

            def denied_walk(path, onerror, **kwargs):
                onerror(PermissionError(13, 'Permission denied', str(directory)))
                return []

            with patch.object(d.os, 'walk', side_effect=denied_walk):
                result = d.pmc_debugfs(root)
            self.assertIn(str(directory), result['unavailable'])

    def test_missing_and_unreadable_debugfs_are_optional(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(d.pmc_debugfs(root)['files'], {})
            directory = root / 'intel_pmc_core'
            directory.mkdir()
            (directory / 'lpm_status').write_text('')
            with patch.object(d, 'read', return_value={'error': 'permission denied'}):
                result = d.pmc_debugfs(root)
            self.assertEqual(result['files'], {})
            self.assertIn(str(directory / 'lpm_status'), result['unavailable'])


if __name__ == '__main__':
    unittest.main()
