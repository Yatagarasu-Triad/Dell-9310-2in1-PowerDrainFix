# Fresh-boot, BIOS-photo, and cross-OS test plan

**Status:** the direct Ubuntu reboot baseline is complete. After an interrupted
first attempt, a replacement captured 174 ACPI/raw/logind samples, all open, during
a user-confirmed ten-second closure before the boot's first suspend, on battery.
Next is unchanged BIOS photos and the [Windows comparison](WINDOWS_TEST.md); no
Windows comparison or controlled suspend has run. Read [FINDINGS.md](FINDINGS.md)
for evidence. This document does not automatically initiate any power action.

Two confirmed ten-second lid closures established a failure of OS-visible lid
reporting in the original Ubuntu boot. The second test and the completed replacement
after reboot found ACPI `_LID`, raw `SW_LID`, and logind all stuck open. The deliberate inhibitor explains
why those protected tests did not suspend; it does not explain the missing raw
state changes. Do not repeat them in the same boot without a new hypothesis.

## 1. Completed baseline: direct Ubuntu-to-Ubuntu restart

Do this **before Windows, Pop!_OS, or a BIOS setup visit**. Windows or even entering
firmware setup could change initialization/runtime state. Collect BIOS photos on
a later restart already needed for the Windows comparison; no extra reboot is
needed just for the photos. If setup was visited first, record that fact rather
than describing the result as an otherwise identical direct reboot.

### User actions

1. Save work. Use Ubuntu's ordinary **Restart** and select the same Ubuntu/kernel.
   Do not use a long power-button hold, RTC reset, battery disconnect, or defaults
   reset. An ordinary restart is not guaranteed to reset the embedded controller.
2. Keep the lid open in normal laptop orientation. Keep charger, dock, displays,
   and USB devices as they were for the previous test; report any changes. Keep AC
   connected if needed; lid reporting can be tested while charging. Only coordinated
   discharge measurements require battery-only operation. Do not fold into tablet
   mode, deliberately suspend, or boot another OS first.
3. Return to the agent promptly after login and say **“Back in Ubuntu; no suspend
   since restart.”** Leave the lid open until the recorder announces readiness.

### Agent actions — batch privileged collection under one graphical authorization

- Verify a changed boot ID, kernel/BIOS version, power/peripheral context, uptime,
  current lid state, and the current boot's journal for any suspend/resume.
  The last observed pre-reboot ID was
  `c72ec359-9f18-4fd6-aca3-67d38af4750e`. Do not infer a clean boot solely from the
  user's login or treat an unreadable journal as proof of no suspend.
- Run the existing desktop-user wrapper:

  ```sh
  python3 -B raw_lid_session.py --acpi-lid
  ```

  This requests one graphical root authorization, uses a bounded 90-second
  `sleep:handle-lid-switch` inhibitor, records raw switches, ACPI lid state,
  logind, snapshots, and journals, and returns ownership of its private results.
- At **“Lid recording ready”**, request one physical action:
  **“Close normally for 10 seconds, then reopen.”** No commands during closure.
  This test intentionally does not suspend. Stay nearby and reopen within the
  recording window; normal lid handling resumes when the inhibitor is released.
  After reopening, wait for **“Lid recording ended”** before confirming in chat;
  interrupting the agent's active tool can cancel recording. The end notice reports
  worker exit, not a passed test. Incremental JSONL preserves completed samples
  from future interrupted runs, but incomplete coverage must remain explicit.
- Interpret the physical cycle against ACPI and raw state, confirm cleanup, and
  save a concise result. A stuck-open physical closure is a failed reporting test,
  not successful suspend and not proof of a broken sensor.

If an unplanned suspend happened first, label the run accordingly; it cannot
answer the pre-first-suspend question. Do not silently change idle timers to
obtain a clean run.

### Branch on the result

| Fresh-boot observation | Smallest useful next experiment |
| --- | --- |
| ACPI and raw lid transitions work | With separate coordinated approval, one short RTC-backed open-lid software suspend, followed by the same protected raw/ACPI test. This tests whether resume breaks reporting. |
| ACPI changes but raw input does not | Investigate ACPI notification/input-driver handling. Do not blame logind or replace its handler yet. |
| Raw input changes but logind does not | Investigate user-space event consumption, timing, and policy. |
| ACPI and raw input still remain open | The fault does not require a suspend in this boot. Proceed to the Windows comparison rather than adding an unnecessary suspend. |

Even if reboot restores reporting, that is a runtime-state clue, not a permanent
fix or proof of one defective driver. Recheck BIOS/kernel versions before attributing
any difference to the reboot alone.

The subsequent A/B/C residency tests remain gated on trustworthy raw lid state.
A short suspend isolates a reporting regression; it does not validate overnight
battery drain. Later ten-minute A/B/C controls compare PC10/S0ix and actual journal
sleep intervals, not just a dark display or successful suspend call. RTC wake is
best effort: save work and remain able to wake the machine manually.

## 2. BIOS photos — on the next restart, before Windows

Press **F2 at the Dell logo** to enter setup. Phone photos are sufficient. Capture
current values, page titles, and descriptive help text; do not change settings.
Redact Service Tag, Express Service Code, asset/ownership tags, or other personal
identifiers before sharing. Keep BIOS version and relevant setting values visible.

The labels below are verified against Dell's model-specific service manual,
System setup, Tables 3 and 10–13, printed pages 39 and 44–46. The manual explicitly
warns that displayed options vary with installed hardware. Current BIOS 2.36.0 may
also differ from the manual. If a setting is absent, photograph the relevant menu
and report it absent; do not enable hidden/advanced settings to find it.

| Priority / photo | What to include | What it can tell us |
| --- | --- | --- |
| 1. Overview / System information | Model if shown, **BIOS Version**, BIOS date if shown. Manufacture/ownership dates are not BIOS dates. | Confirms firmware provenance. Live Linux captured 2.36.0, dated 2025-11-26; the historical brief says 2.19.0. |
| 2. Power Management → **Lid Switch** | Entire expanded section and help text. If there are separate **Enable Lid Switch** and **Power On Lid Open** controls, capture both. | Distinguishes a true reporting-disable control from lid-open power-on behavior. The manual's single “Lid Switch” entry describes powering up **from off when opened**, default ON. It does not document a separate reporting-enable control. |
| 3. Power Management → **Block Sleep** and wake settings | Block Sleep plus **Wake on AC**, **Wake on Dell USB-C Dock**, **Wake on LAN**, and **Auto on Time** if visible; a few page photos are fine. | Records visible sleep/wake configuration. Dell describes Block Sleep specifically as blocking **S3**, default OFF. This machine exposes s2idle only; that checkbox alone does not prove or disprove S0ix operation. Wake-source settings likewise do not establish a cause. |
| 4. Performance → **Enable C-State Control** | Current value and help text; adjacent SpeedStep/Speed Shift settings if readily visible. | Checks for an obvious CPU low-power restriction; manual default for C-State Control is ON. Prior near-full PC10/S0ix controls already argue against a universal restriction in those runs. |
| 5. POST Behavior → **Fastboot** | Selected mode and help text. | Records firmware initialization policy; manual default is Thorough. Dell BIOS Fastboot is not Windows Fast Startup. Do not change either for this baseline. |

Optional only if readily exposed: a clearly named lid/sensor/convertible/ISH
control, or existing BIOS/Power/Thermal event-log errors. These are **not claimed
to be available sensor menus on this model**. Do not clear logs or hunt through
unrelated security/storage pages.

If a genuine lid-enable control is disabled, photograph it and leave it unchanged
for review. Merely having **Power On Lid Open** disabled is not proof of a broken
lid-close path. Exit without applying changes. Do not load defaults, flash BIOS,
change RAID/VMD/AHCI, Secure Boot, or boot order, or perform a reset. Use an existing
boot selector for the next OS rather than changing persistent boot configuration.

**Limits:** these photos can rule out particular visible settings as explanations;
they cannot prove absence of BIOS/EC bugs, hidden firmware state, sensor failure,
or kernel/firmware incompatibility. They also cannot establish sustained S0ix.

## 3. Windows comparison, then Windows-to-Ubuntu

The user recalls normal lid behavior in Windows and failures only under Linux.
That is useful history, but a new time-correlated measurement is needed.

Keep charger/battery operation, dock/external displays, USB devices, and normal
laptop orientation consistent across the comparison. Capture rather than silently
change existing power policy. Do not install updates or change drivers as part of
the comparison; record any automatic update/version change that intervenes.

### Minimum Windows evidence

Use one grouped read-only capture before/after a coordinated closure, rather than
multiple unrelated screenshots or repeated manual commands:

- OS/build, last boot time, BIOS version, and AC/battery context.
- Existing **“Choose what closing the lid does”** policy for both battery and
  plugged-in operation. A screenshot is useful if automated policy output is
  incomplete. “Do nothing” or “Hibernate” is not a failed sleep test; it changes
  the expected action. Do not change it silently.
- `powercfg /a` for advertised sleep modes, and `powercfg /query` for the active
  power scheme. These commands query rather than modify configuration.
- A time-marked normal closure for about 10 seconds and reopening, with no manual
  sleep command, power-button press, or tablet folding during that interval.
- System **Kernel-Power** and **Power-Troubleshooter** event XML/messages covering
  the interval, including available entry/exit reasons. Event IDs and wording
  vary between traditional sleep and Modern Standby; do not require one fixed ID
  or interpret a generic reason as definitive raw lid telemetry.
- `powercfg /systemsleepdiagnostics` and, where supported, `powercfg /sleepstudy`
  generated after the trial into a new results folder. Reports may need an
  administrator shell. Batch them with the other collection under one elevation.
  Very short sessions may be omitted or have too little information in SleepStudy;
  absence there alone is not proof that Windows missed the lid.

Do not judge by the display turning off, a fan stopping, or the lock screen on
reopen. A matching sleep/standby entry reason and timing with no competing manual
or idle action are substantially better evidence. If evidence is ambiguous, do
not immediately request an overnight trial. A focused follow-up can collect direct
Windows lid notifications or a longer sleep interval.

The Linux recorder does **not** run on Windows. `windows_lid_capture.ps1` now
provides a one-shot Windows collection; see [WINDOWS_TEST.md](WINDOWS_TEST.md) for
launch, existing-policy checks, limits, and report interpretation. It has been
reviewed on Linux but not parser/runtime-validated in PowerShell. The user has
now authorized publishing source and curated findings to GitHub for a Windows
clone; see `WINDOWS_TEST.md` for the exact URL and launch instructions. Raw captures
are excluded from Git. Arrange a separate private transfer of reviewed Windows
reports and BIOS photos. The current agent cannot operate another OS automatically
or assume this Ubuntu workspace is accessible there; do not mount or alter
ZFS/partitions to share files.

After Windows, choose Windows **Restart**, not a normal shutdown that may use Fast
Startup/hybrid shutdown, and select Ubuntu through the existing boot selector.
Do not disable Fast Startup or hibernation persistently. Again capture Ubuntu
before its first suspend and repeat the same protected raw/ACPI test.

| Result | Supported interpretation, not a definitive diagnosis |
| --- | --- |
| Windows responds to the lid; Ubuntu does not | Permanent sensor failure and a globally disabled switch become much less likely. Linux driver/ACPI handling or OS-dependent firmware initialization deserves priority. Intermittent hardware remains possible. |
| Ubuntu starts working only after visiting Windows | Supports an initialization/EC/firmware-state dependency; it does not prove which Windows driver or firmware method caused it. A controlled repeat may be needed to separate boot-order and BIOS-visit effects. |
| Both OSs fail | Check policy and measurement first; firmware/EC/sensor hardware becomes a stronger candidate, not a proven failed part. |
| Ubuntu works before suspend but fails after resume | Supports a resume-dependent reporting fault. Preserve the journal/driver state around that transition. |

Successful Windows lid recognition does not prove Windows achieves low overnight
drain, nor identify the cause of Linux's historical S0ix-residency failure. Treat
lid recognition and low-power residency as separate outcomes.

## 4. Pop!_OS — optional second Linux control

Use the same Python raw/ACPI recorder, without installing a replacement kernel.
Record the actual kernel, OS version, desktop/logind versions, previous OS, firmware
version, power/peripheral context, and whether any suspend already occurred.
Check that Python/systemd/polkit prerequisites exist before attempting a capture;
do not assume the Ubuntu graphical wrapper is usable without checking.

- Pop!_OS working while Ubuntu fails narrows the issue toward differing Linux
  kernels, drivers, ACPI behavior, configuration, or boot history. It is not proof
  of a kernel regression until versions and other differences are controlled.
- Both Linux installations failing while Windows works strengthens the case for
  a shared Linux/firmware interaction rather than a permanently dead sensor.
- If kernels are the same, the distribution comparison is less independent than
  its name suggests. Tests performed after Windows may also inherit changed state.

## Approval and rollback boundaries

Group read-only capture and temporary inhibitor work into one elevation per
session. Coordinate any real suspend separately, with all intended steps described
up front; technical authentication may still recur after reboot/OS changes. No
blanket approval is assumed for permanent configuration or firmware changes.

Only physical lid actions, reboot/boot selection, BIOS photos, and any necessary
other-OS collector launch require the user. Raw Linux capture cleans up its bounded
inhibitor automatically. No persistent configuration is modified by this plan;
report generation writes only local result files. Firmware changes and hardware
reset procedures are outside this plan.

## Sources

- [Dell XPS 13 9310 2-in-1 Service Manual](https://dl.dell.com/topicspdf/xps-13-9310-2-in-1-laptop_service-manual_en-us.pdf), System setup, Tables 3 and 10–13.
  Downloaded 2026-09-22 into ignored `.tools/dell-manual/`; SHA256
  `29720264343e0f3354e0fc795d74332a6492729ef970f3927486ec4d388a679e`.
- [Microsoft: powercfg command-line options](https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options), query, available sleep states, SleepStudy, and system sleep diagnostics.
