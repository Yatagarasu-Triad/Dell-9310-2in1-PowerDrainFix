# Dell XPS 13 9310 2-in-1: lid-suspend S0ix investigation

Read [FINDINGS.md](FINDINGS.md) for evidence and current status, and [Agent.md](Agent.md)
for the original investigation brief and safety constraints. **A power-drain fix is
not yet established.** These tools distinguish suspend entry from actual low-power
platform residency; a successful `systemctl suspend` is not the success criterion.

The coordinated plan and model-specific BIOS photo checklist are in
[REBOOT_TEST_PLAN.md](REBOOT_TEST_PLAN.md). **The direct Ubuntu reboot baseline is
complete:** a confirmed closure still produced no ACPI/raw lid transition before
that boot's first suspend. The [Windows comparison](WINDOWS_TEST.md) also recorded
no sleep during a confirmed closure despite Sleep lid policy. With user approval,
**LidSwitch was changed from Disabled to Enabled**. The same-boot Windows retest
then recorded a full lid cycle and Modern Standby entry/exit with reason Lid.
Next is Windows Restart → Ubuntu and the protected ACPI/raw comparison before
Ubuntu's first suspend. Linux reporting and overnight drain remain unverified
after the change. See [FINDINGS.md](FINDINGS.md). No reboot is automated.

## Windows handoff

Clone the public source repository in Windows (raw Linux captures are deliberately
not published):

```powershell
git clone https://github.com/Yatagarasu-Triad/Dell-9310-2in1-PowerDrainFix.git
Set-Location .\Dell-9310-2in1-PowerDrainFix
```

Read [WINDOWS_TEST.md](WINDOWS_TEST.md) before launching the collector. It covers
one Administrator PowerShell session, the existing lid-policy check, the readiness
and completion messages, and where private results are saved. Take the unchanged
[BIOS photos](REBOOT_TEST_PLAN.md#2-bios-photos--on-the-next-restart-before-windows)
on the restart toward Windows. **Do not commit raw Windows reports or BIOS photos**;
review and transfer those privately. Windows preflight and PowerShell validation
are recorded in [FINDINGS.md](FINDINGS.md).

## Tools

Linux tools require Python 3 standard library and the system's existing systemd
tools. The separate Windows collector requires Administrator Windows PowerShell
5.1. No packages or persistent system changes are installed.

- `lid_probe.py`: enumerates **EV_SW only**, identifies driver ancestry, reads current
  switches through `EVIOCGSW`, and records timestamped switch events. Does not grab
  input devices or record keystrokes. Permission errors are explicit, not “lid open.”
- `xps-s0ix-diagnose.py`: baseline, protected raw-lid observation, and opt-in RTC-backed
  A/B/C experiments. Full CLI, safety limits, and artifact descriptions: [HARNESS.md](HARNESS.md).
- `raw_lid_session.py`: desktop-user wrapper that requests **one graphical root
  authorization** for a 90-second protected observation, notifies when recording is
  ready, and returns the new private results to the invoking user's ownership.
  The user only needs to close normally for 10 seconds and reopen after readiness.
  `--acpi-lid` also correlates firmware lid status with input and logind.
- `collect_acpi.py`: privileged, read-only copy of DSDT/SSDT tables for offline
  inspection, with checksum/hash recording and ownership handback. Does not copy
  licensing/UEFI tables, evaluate arbitrary AML, or install table overrides.
- `windows_lid_capture.ps1`: one elevated Windows capture of context/policy,
  time-correlated power events, and power reports around a 90-second observation.
  Does not change policy, request/inhibit sleep, or infer a lid verdict. Existing
  Windows lid actions remain active. **PowerShell parser and isolated runner checks pass**;
  launch, privacy, and transfer instructions: [WINDOWS_TEST.md](WINDOWS_TEST.md).
- `windows_lid_notifications.ps1`: unprivileged, bounded Windows lid/AC-source
  notification listener for follow-up. Passive startup validation and synthetic
  tests pass; the post-change physical test captured open/closed/open. See the optional
  follow-up in [WINDOWS_TEST.md](WINDOWS_TEST.md).

```sh
# Read-only baseline; unavailable privileged data is recorded as such.
python3 -B xps-s0ix-diagnose.py baseline

# Current switches (enumeration works unprivileged; raw reads may need sudo).
python3 -B lid_probe.py

# Graphically authorized, bounded physical lid test; does NOT suspend.
python3 -B raw_lid_session.py

# Add firmware ACPI lid-state correlation (reads _LID; may generate EC traffic).
python3 -B raw_lid_session.py --acpi-lid

# Hardware-independent mocked tests; no root or suspend.
python3 -B -m unittest discover -v
```

Wait for **“Lid recording ready”** before closing the lid. Reopen within the recording
window: ordinary lid handling resumes when the inhibitor is released. Do not fold
into tablet mode during the normal-closure control. If no complete open→closed→open
cycle is observed, the result is explicitly inconclusive, not a passed lid test.
After reopening, wait for **“Lid recording ended”** before confirming the closure
in chat, since interrupting the agent can cancel its active tool. The end notice
reports worker exit, not a passed test. Samples are now also flushed incrementally
to `raw-logind-samples.jsonl` so an interrupted run retains completed samples.

A/B/C tests are deliberately separate and require `--allow-suspend`. Save work and
remain able to reopen/wake the laptop. They do not modify storage mode, firmware,
modules, wake-device settings, or permanent desktop/logind configuration. An RTC
alarm is a best-effort wake mechanism, not a guarantee against firmware failure.

## Evidence and privacy

Timestamped captures live under `runs/`, ignored by Git. Raw sessions use a private
`runs/raw-session-*/` parent. Review artifacts before sharing; they contain hardware
identifiers, paths, and journal messages. Keep [FINDINGS.md](FINDINGS.md) as the curated
interpretation rather than committing entire raw dumps. The optional offline
ACPICA tool is unpacked under `.tools/`, also ignored, not installed system-wide.
Its package version/checksum provenance and the firmware method trace are recorded
in `FINDINGS.md` and the capture manifests.

Residency uses the primary PCH S0ix sysfs counter and CLOCK_BOOTTIME elapsed time.
Counter decreases are reported as reset/wrap without guessing. Percentages include
awake capture/setup overhead. Battery changes are signed and units are preserved;
charge alone is not reported as measured energy.

## Rollback

There is no persistent installation to undo. Raw observation uses a process-held,
bounded `sleep:handle-lid-switch` inhibitor. Suspend tests use temporary inhibitors
where needed and clean up only their own RTC alarm in `finally`. Termination/crash
limitations are documented in [HARNESS.md](HARNESS.md). No automatic permanent
workaround, hibernation configuration, or module blacklist is applied.
