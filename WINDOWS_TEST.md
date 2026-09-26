# One-shot Windows lid comparison

## Purpose and limits

Current handoff: Dell XPS 13 9310 2-in-1, BIOS **2.36.0**, Ubuntu kernel **7.0**.
Physical ten-second closures have left ACPI `_LID`, raw `SW_LID`, and logind stuck
open, including **before the first suspend in a fresh Ubuntu boot**. The next
comparison is the existing Windows installation, without changing firmware or
power policy. This document adds the Windows collector referenced in
[REBOOT_TEST_PLAN.md](REBOOT_TEST_PLAN.md); it does not change that plan's history.

`windows_lid_capture.ps1` uses Windows PowerShell 5.1 and Windows built-ins. It
queries state and generates local reports. It does **not** request or inhibit
suspend, reboot, set wake timers/RTC alarms, change power policy, registry,
hibernation, Fast Startup or UEFI settings, install anything, update drivers, or
upload data. **Existing Windows lid/idle policy remains active**, unlike the
protected Linux recorder. A physical closure can therefore cause ordinary sleep,
hibernation, or whatever that policy already specifies.

This is not direct Win32 lid-notification instrumentation and does not automatically
classify the lid as working or broken. It also does not measure overnight drain or
prove sustained S0ix residency.

**Validation status:** prepared and inspected on Linux, where `pwsh` was not
available. No PowerShell parser/runtime validation or Windows hardware test has
been performed. First-run Windows compatibility and behavior remain unverified.

## Before leaving Ubuntu

1. The user authorized publishing the source and curated findings to
   `https://github.com/travishriley/Dell-9310-2in1-PowerDrainFix`. Clone that repository
   in Windows; no shared partition or removable-media copy of the script is needed.
   **Raw captures are not published.** Plan a separate private transfer of reviewed
   Windows results and BIOS photos; do not commit them to this public repository.
   Do not mount Linux ZFS/NTFS, change partitions, or install filesystem drivers
   to make the original Ubuntu workspace accessible.
2. Save work. On the already-planned restart, take the **unchanged BIOS photos
   first**, following section 2 of [REBOOT_TEST_PLAN.md](REBOOT_TEST_PLAN.md):
   version, Lid Switch/help text, Block Sleep/wake settings, C-state control and
   Fastboot if available. Redact Service Tag and other identifiers. Exit without
   applying changes; do not load defaults or reset anything.
3. Boot the existing Windows installation through the existing boot selector.
   Keep charger, dock, USB devices, external displays and normal laptop orientation
   consistent. Do not fold into tablet mode or deliberately sleep first. Record
   any intervening automatic update, boot/version change or earlier sleep; do not
   install updates or change settings for this comparison.

## Get the Windows checkout

In a normal PowerShell window, from your preferred parent folder:

```powershell
git clone https://github.com/travishriley/Dell-9310-2in1-PowerDrainFix.git
Set-Location .\Dell-9310-2in1-PowerDrainFix
```

If Git is unavailable, the repository's **Code → Download ZIP** provides the same
source; extract it to a local folder before launch. Read `FINDINGS.md` for the
Ubuntu evidence and current hibernation limitations. The referenced `runs/` paths
are private Ubuntu artifacts, not missing files that Windows must recreate.

## One elevated launch, then one physical action

Save Windows work too. Stay beside the laptop and able to reopen/wake it normally.
Before launching, inspect Control Panel → Power Options → **Choose what closing
the lid does** and take one screenshot of the current battery and plugged-in
settings, without changing or applying them. If the applicable action is **Shut
down**, stop and report that rather than knowingly shutting down the machine and
losing the capture. **Hibernate** or **Do nothing** changes what this comparison
can establish; report that policy before proceeding. Hibernation may need an
ordinary manual wake, which must be reported as a confounder.

1. Open **Windows PowerShell → Run as administrator** once, preferably the normal
   64-bit Windows PowerShell, not PowerShell ISE. Approve that elevation. The script
   checks Windows and administrator status; it never unexpectedly self-elevates.
2. In File Explorer use **Copy as path** on the local `windows_lid_capture.ps1`
   (Shift + right-click on older Windows). In the elevated shell type `& `, paste
   the quoted path, and press Enter. The resulting command has this form; use the
   **actual path you copied**, not the example:

   ```powershell
   & "C:\your-approved-local-folder\windows_lid_capture.ps1"
   ```

   No other input is required by the collector. If execution policy blocks the
   launch, use only the conditional instructions below; do not change settings.
3. Keep the lid open during **“Preparing read-only capture”**. At **“Lid recording
   ready / START”**, **close normally for about 10 seconds, then reopen promptly**.
   Do not leave it closed for the 90-second observation window. Do not issue a
   manual sleep command, press the power button or fold into tablet
   mode during the closure. If reopening does not wake it, use the normal manual
   wake method as needed for recovery and report exactly what was necessary.
4. Leave the shell running and the lid open. **“Observation window closed” is not
   the final notice**: report generation follows. Wait for **“Lid capture ENDED”**
   before confirming completion or copying the results. This message means the
   collector ended, **not that the lid test passed**. If the window closes or an
   unexpected error prevents that notice, preserve the partial folder and report
   the interruption; do not silently repeat the trial.

### Only if execution policy actually blocks launch

Inspect the effective policies in the **same elevated window**:

```powershell
Get-ExecutionPolicy -List
```

If `MachinePolicy` or `UserPolicy` is anything other than `Undefined`, **stop and
use the organization's approved execution/signing route**. Do not try to override
managed policy, AppLocker, application control or constrained language restrictions.

Otherwise, after reviewing/trusting the transferred script, a one-process option
is available **only if needed**. Use the same actual script path:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\your-approved-local-folder\windows_lid_capture.ps1"
```

This child inherits the existing elevation; no second elevation is needed. The
option expires when that process ends. Do **not** run persistent `Set-ExecutionPolicy`,
change policy in the registry, or unblock/change files as a workaround for managed
restrictions. There is no execution-policy option in the collector itself.

## Timing and local results

- Preparation has a 75-second budget. Observation uses a **90-second UTC wall-clock
  deadline from readiness**, not 90 seconds of awake polling. There is no keypress
  prompt. After that deadline, collection has a further **180-second budget**.
- Each before/after context child is limited to 30 seconds; each `powercfg /a` or
  `/query` to 15 seconds; event extraction to 20 seconds; each report to 45 seconds.
  Commands also respect the remaining phase budget. An overdue owned process is
  terminated; commands with no budget left are recorded as skipped. Timeouts,
  nonzero exits, unsupported-command messages, missing/empty reports and partial
  reports are retained in the command log without discarding other evidence.
- These are software deadlines, not a hardware wake guarantee. **No program can
  finish while Windows is asleep, hibernating or off.** It checks elapsed UTC time
  when execution resumes rather than starting another 90-second window. A late
  resume can consume the collection budget and leave skipped evidence. Ordinary
  process termination/file I/O adds small overhead; a hung OS/storage device can
  defeat software deadlines. System clock changes can affect UTC deadlines.
- The default root is runtime-resolved
  `[Environment]::GetFolderPath('LocalApplicationData')` plus `Dell9310-LidCapture`.
  Each run gets a timestamp/GUID directory; **the exact path is printed**. It belongs
  to the Windows account running the elevated shell, which may differ if different
  administrator credentials were used. Do not assume a cross-OS mounted path.
- Optional `-OutputRoot "C:\actual\private-local-folder"` selects another absolute
  local root. UNC and mapped network paths are rejected. Choose a non-synced local
  filesystem with Windows ACL support; the script restricts its new run directory
  to the launching account and SYSTEM before writing evidence. Failure to apply
  that ACL stops launch; it does not fall back to public permissions. It does not
  change the parent directory's permissions. Local administrators can still gain
  access; this is not encryption. Avoid cloud-sync folders and redirected network
  locations, including ones exposed through filesystem links.

### Files to retain

| File | Evidence / limitations |
| --- | --- |
| `manifest.json` | Collector state; UTC preparation, ready/start, planned deadline, actual observation end, collection deadline and finish times; event interval; AC/policy endpoint comparison flags. Saved at phase boundaries so interrupted runs remain identifiable. The physical close/reopen times are **not** measured directly. |
| `commands.json` | Incrementally saved per-command deadlines, actual start/end, status, exit code, errors, stdout/stderr filenames and report presence. `ok` is command completion, not a sensor verdict. |
| `before-context.stdout.txt`, `after-context.stdout.txt` | JSON with OS caption/version/build, last boot time, BIOS version/date and AC/battery context. BIOS serials are not selected. Inspect `Errors` even if the child exited normally; killed children may leave no usable JSON. Battery fraction is 0–1 when known; charge/status flags and unknown values must be respected, and -1 remaining seconds means unknown. |
| `before-sleep-states.stdout.txt`, `after-sleep-states.stdout.txt` | `powercfg /a`: advertised sleep states and availability reasons. |
| `before-policy.stdout.txt`, `after-policy.stdout.txt` | Full `powercfg /query` for the active scheme, including the displayed AC/DC values. Different output is flagged as a possible policy confounder, never repaired. |
| `events.xml`, `event-messages.txt`, `events.stdout.txt` | System Kernel-Power and Power-Troubleshooter events, raw XML plus rendered messages, with query status/count. All IDs are eligible. Interval is readiness minus 15 seconds through actual observation end plus 15 seconds; the collector waits for the end margin before querying if time permits. A killed query can leave truncated XML/messages. |
| `system-sleep-diagnostics.html`, `sleepstudy.html` | Generated by `powercfg /systemsleepdiagnostics` and `/sleepstudy`. May be unsupported, incomplete or absent; consult statuses and accompanying stdout/stderr, not file existence alone. |
| `*.stderr.txt`, other `*.stdout.txt` | Command diagnostics, including localized unsupported/error text. Empty stderr alone does not prove success. |

**Privacy:** snapshots deliberately avoid requesting serial numbers, but native
reports and raw events are **not anonymized**. They may contain computer/user
identifiers, paths, devices, applications, timestamps, and activity from earlier
sessions (reports use their normal historical range, not just this trial). Keep
results private. Review/redact copies before any approved transfer or sharing;
copying elsewhere may lose the private ACL. No upload, archive or transfer is
automated, and raw reports should not be committed to Git.

## Interpreting the comparison

- Confirm the user actually completed the normal ten-second closure after readiness.
  A dark display, quiet fan or lock screen alone is **not evidence of recognized
  lid closure or sleep**.
- Correlate the applicable **AC or DC lid policy**, the physical action/timing, and
  Kernel-Power/Power-Troubleshooter entry/exit reasons. IDs and wording differ
  between Modern Standby and traditional sleep; a generic sleep event is not
  direct lid telemetry. Competing idle sleep, manual wake or another power action
  makes interpretation less certain.
- If `/query` does not expose the lid action clearly, retain a screenshot of the
  existing Control Panel **“Choose what closing the lid does”** page showing both
  battery and plugged-in actions, without editing/applying anything. **“Do nothing”
  or “Hibernate” means absence of sleep is not a failed sensor test.** Do not adjust
  the policy merely to obtain a desired result.
- **No events is not an automatic pass or fail.** Distinguish a successful empty
  query from errors/timeouts. Short sessions may be omitted from SleepStudy, and
  event delivery/reporting varies. Ambiguous evidence stays inconclusive; direct
  lid notifications or a longer trial are separate follow-ups, not automated here.
- AC or policy changes between snapshots are confounders. Matching endpoints do
  not exclude transient changes; report any cable/device/policy changes yourself.
  Neither endpoint flags nor missing reports trigger an automatic fix or verdict.

When finished, report just: closure completed after readiness, roughly ten seconds;
reopening/wake behavior; any deviations or AC/device changes; the **ENDED** message
and private result location. Transfer reviewed evidence through the agreed location.

Then use Windows **Restart**, **not Shut down / hybrid shutdown**, and select Ubuntu
through the existing boot selector. Do not disable Fast Startup or hibernation.
Return promptly with the lid open and say **“Back in Ubuntu after Windows Restart;
no suspend since restart”** if true. The main agent will verify the boot and arrange
the existing protected raw/ACPI comparison before the first Ubuntu suspend. Windows
success would weaken permanent sensor-failure explanations, not establish a power-
drain fix or identify a particular Linux driver.
