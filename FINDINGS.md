# Investigation findings — updated 2026-09-26

## Status

**Windows lid recognition and lid-triggered sleep/wake are restored in the retest.**
With explicit user approval, BIOS `LidSwitch` was changed from `Disabled` to
`Enabled`. The setter returned success and fresh readback confirmed Enabled;
only that attribute changed among 104 captured enumeration/integer attributes.
`PowerOnLidOpen` remains Disabled, Fastboot Minimal, and CStatesCtrl Enabled.

Before the change, a confirmed Windows closure produced no sleep events. After
the change, in the same Windows boot, direct notifications recorded
**open → closed → open**, and Kernel-Power events recorded Modern Standby entry
and exit with **Reason: Lid**. SleepStudy independently shows Screen Off → Sleep
→ Active. The observed standby interval was about 14.9 seconds on AC.

**Linux lid reporting and overnight drain are not yet revalidated.** Earlier
Ubuntu tests found ACPI/raw/logind stuck open, including before the first suspend
after reboot. The next step is Windows Restart → Ubuntu, then the protected
ACPI/raw test before Ubuntu's first suspend. Restored Windows sleep entry is not
proof of sustained PC10/S0ix or a complete power-drain fix. No other settings,
firmware image, storage, boot configuration, drivers, or power policies were changed.

## Confirmed

### Historical experimental evidence supplied in `Agent.md`

These are supplied results, not newly reproduced measurements:

- s2idle is the only advertised mode; live sysfs also verifies `[s2idle]`.
- A 10-minute software-triggered, open-lid control achieved about 600 seconds each
  of CPU PC10 and PCH S0ix residency.
- The reported normal lid-suspend test achieved only about 139 seconds PC10 and
  90 seconds S0ix in approximately ten minutes.
- An overnight reported lid-suspend session lost 12 percentage points of battery
  charge with only ~0.285 seconds S0ix over ~11.4 hours; estimated average power
  was ~0.35 W. The journal showed a continuous suspend interval.
- Inhibited-lid/manual suspend again accumulated ~606 seconds S0ix, but logind
  reported the lid open; raw closed state was not established in that experiment.
- VMD did not universally prevent S0ix in the successful controls.
- Battery full/design charge is 4,458,000 / 6,577,000 uAh (~67.8% health), verified
  again in current captures.

### Live hardware and switch path

- Ubuntu 26.04.1, kernel `7.0.0-30-generic`, Dell XPS 13 9310 2-in-1.
- **Live BIOS is 2.36.0, dated 2025-11-26**, not the 2.19.0 / 2023-07-13 recorded
  in the brief. No firmware action was taken. Historical BIOS provenance needs
  reconciliation before claiming all tests used identical firmware.
- `/dev/input/event0`: `Lid Switch`, `SW_LID`, ACPI `PNP0C0D:00`,
  driver `acpi-button`, ACPI path `\_SB_.LID0`.
- `/dev/input/event17`: `Intel HID switches`, **SW_TABLET_MODE, not SW_LID**,
  parent `INTC1051:00`, driver `intel-hid`. Event numbers are boot-specific.
- Both have `ID_INPUT_SWITCH=1` and the `power-switch` udev tag. Logind explicitly
  watches the ACPI lid device. A missing udev lid tag is not supported.
- Live `HandleLidSwitch=suspend`, external-power override empty (falls back to
  base policy), docked action `ignore`. On-disk config and live daemon properties
  differ, so both are captured. No service was restarted or reconfigured.
- Holdoff and maximum delay-inhibitor time are each 30 seconds. These are test
  timing confounders, not explanations for overnight residency by themselves.
- Kernel logs associate `input28: PM: parent INTC1051:00 should not be sleeping`
  with late registration of `Intel HID switches` at 21:59:39 on September 21,
  immediately before resume completed. This does not identify it as the blocker.

### Raw observations performed in this session

1. `runs/2026-09-22T182844.896784Z-baseline/`: unprivileged baseline. Input and
   debugfs permissions were insufficient; those errors were recorded explicitly.
2. `runs/raw-session-b6k9vc30/2026-09-22T183829.754746Z-raw-lid/`: root recorder
   ran for 90 seconds. Desktop helper initially failed to read a private readiness
   marker; this was fixed and regression-tested. Recorder cleanup still completed.
   No switch transitions were observed, so this is not a successful physical test.
3. `runs/raw-session-5tk080_8/2026-09-22T190612.171970Z-raw-lid/`: corrected
   desktop readiness notification and 90-second capture completed. The user
   subsequently confirmed physically closing the lid for ten seconds.
   - Raw `SW_LID` remained false; no lid-close or lid-open events recorded.
   - `SW_TABLET_MODE` remained false; no tablet transitions recorded.
   - Logind and raw lid agreed in 176/176 comparable samples, always open.
   - Raw-open state verified before releasing the temporary inhibitor.
   - Result is `no_lid_cycle_observed`, not a passed test (intentional exit code 2).
4. `runs/raw-session-yznabqe5/2026-09-22T193022.587840Z-raw-lid/`: repeated
   with `--acpi-lid`; the user again confirmed a full ten-second physical closure.
   - ACPI `/proc/acpi/button/lid/LID0/state` remained open in all 176 samples.
   - Raw lid, tablet mode, and logind still reported no transition.
   - Sample-window SCI and GPE totals increased by 2,683; full snapshot interval
     `gpe6E`/SCI deltas were 2,728. EC transactions caused by the measurement can
     contribute; this is **not proof of an interrupt storm or a lid notification**.
   - Raw-open state verified before releasing the inhibitor; cleanup verified.
   - No suspend occurred; awake counter deltas are not a suspend-residency test.
   - Battery charge increased during this run, so it is not a battery-drain control.

**Not suspending in these raw tests is expected:** a bounded
`sleep:handle-lid-switch` inhibitor deliberately prevents sleep. That inhibitor
should not suppress kernel EV_SW reporting. A physical closure with no raw state
change points upstream of the logind suspend action, not to the inhibitor proving
the suspend handler is broken.

Post-run inspection confirmed no diagnostic Python/probe/inhibitor remained and
`rtc0/wakealarm` was empty. The raw tests did not write any RTC alarm.

Current open-lid ACPI `/proc/acpi/button/lid/LID0/state` reports `open`.
`button.lid_init_state` is `[method]`, not disabled or forced-open; report interval
is 500 ms. The ACPI-aware closed-lid test therefore sampled firmware status,
not merely logind's cached property.

### Power context of the completed raw tests

Endpoint snapshots were reviewed after the user reported needing to charge during
troubleshooting. They establish the following context, not continuous monitoring
of every charger connection/disconnection:

| Capture | Initial and final power context | Interpretation |
| --- | --- | --- |
| `190612` confirmed closure, raw/logind | AC offline; battery Discharging, 40% → 39% | Raw lid reporting failed in a battery-powered observation. |
| `193022` confirmed closure, ACPI/raw/logind | AC online; battery Charging, 43% → 44% | Raw and ACPI reporting failed in a charging observation; not a battery-drain measurement. |
| `200439` interrupted fresh-boot capture | Initial AC online, Charging, 72%; no final snapshot | Incomplete observation, independently of charging. |
| `201515` completed fresh-boot capture, confirmed closure | AC offline; battery Discharging, 73% at both endpoints | ACPI/raw/logind remained open before this boot's first suspend. |

Thus plugging in does not invalidate the existing raw-lid finding; the completed
observations include both power contexts. ACPI-aware repeated-read failures have
now been observed on AC and battery, although these are not a matched
AC-versus-battery experiment (different boots/history).
AC policy and USB-C/dock/device PM state can affect automatic suspend and residency,
so future controlled comparisons must keep power/peripherals fixed. PC10/S0ix
counters remain meaningful on AC, but do not establish battery-only behavior.
Charge deltas while charging cannot quantify battery drain. The supplied historical
suspend/residency results have not been re-audited for charger transitions.
Keep AC attached while preparing and checking lid reporting if needed; use battery
only for coordinated discharge measurements after sufficient charging.

### Fresh-boot check and interrupted observation — 20:04 UTC

- User reported returning to Ubuntu without any suspend since restart.
- New boot ID `8ebb97d3-f7c0-4fd4-845e-3c6a90bcd837`, replacing
  `c72ec359-9f18-4fd6-aca3-67d38af4750e`; uptime was about 106 seconds at inspection.
- Same kernel `7.0.0-30-generic`, BIOS 2.36.0 / 2025-11-26. AC online and charging.
- Accessible current-boot kernel and logind/suspend-service journals showed no
  suspend/resume through the post-cancellation check at 20:05:47 UTC.
- `intel-hid` emitted `switch event received, enable switches supports` and
  registered the tablet switch at 20:04:09 UTC **before any observed suspend**.
  Late switch registration is therefore not uniquely a resume phenomenon. The
  separate historical `parent ... should not be sleeping` warning was not observed
  in this boot's inspected output; neither message proves the cause.
- Capture `runs/raw-session-r4vz_f6h/2026-09-22T200439.156073Z-raw-lid/` started;
  the tool then reported cancellation. The user confirmed a ten-second closure,
  but its overlap with the recorded interval cannot be established from artifacts.
- Recovered seven entries from the root-owned result tree under one authorization,
  returning only that tree to the desktop user. Raw JSONL contains seven initial
  device states (lid/tablet open/false) plus a readiness record at 20:04:39.955 UTC;
  no final states, sample series, after snapshot, or summary survived.
- **This interrupted attempt remains incomplete/unclassified**, not a reproduced
  stuck-open closure. A subsequent replacement completed; see below.
- Post-cancellation inspection found no diagnostic worker/inhibitor and an empty
  RTC alarm. No suspend or RTC alarm was requested.

To retain evidence during future interruptions, completed raw/logind/optional ACPI
samples now flush incrementally to `raw-logind-samples.jsonl`, while normal JSON
summaries remain unchanged. The desktop wrapper announces worker exit separately
from readiness and asks for chat confirmation after recording ends. Cancellation
is still honored; nothing is detached or made to continue against a stop request.
A partial stream does not certify completed observation or physical-cycle coverage.

### Completed fresh-boot replacement — 20:15 UTC

Capture: `runs/raw-session-ht9b38sa/2026-09-22T201515.259838Z-raw-lid/`.

- The user explicitly confirmed following the readiness instruction, closing
  normally for ten seconds, reopening, and waiting for the recording-ended notice
  before reporting completion. Saved separately as `physical-confirmation.json`.
- Same new boot `8ebb97d3-f7c0-4fd4-845e-3c6a90bcd837`, kernel and BIOS. The preflight
  and post-run kernel/sleep-service journal checks showed **no suspend/resume since
  boot**. Post-run checks are saved in `post-run-check.json`.
- The 90-second observation sampled boottime 765.65–855.40 seconds (approximately
  12.8–14.3 minutes after boot), before this boot's first suspend, not immediately
  at login. AC was offline; battery Discharging and 73% at both snapshot endpoints.
- **174/174 ACPI readings reported open; raw SW_LID and logind also stayed open in
  174/174 comparable samples.** No lid-close/open events, no tablet-mode toggles,
  and no missing/ambiguous comparison samples were recorded.
- Probe JSONL contains seven initial device states, a readiness record, and seven
  final states. All 174 incrementally flushed samples exactly match the final JSON
  array. Worker completed with the expected `no_lid_cycle_observed` / exit code 2,
  not a crash or interrupted capture.
- Sample-window SCI/GPE delta was +2,669; this is not proof of a lid event or
  interrupt storm because polling `_LID` can itself generate EC activity.
- The protected awake test did not suspend. Its zero PC10/S0ix deltas and battery
  charge delta of -26,000 uAh are **not suspend-residency or suspend-drain results**.
- Cleanup verified: no diagnostic workers or inhibitor, empty RTC alarm, all
  capture files returned to UID 1000. No RTC alarm was written. An empty temporary
  directory from the earlier interrupted run is not an active inhibitor.

**Conclusion:** ordinary reboot did not restore OS-visible lid reporting. A
suspend/resume in the current Ubuntu boot is **not required** to reproduce the
failure. This does not prove a defective sensor, exclude EC state retained across
reboot, or establish the cause of the historical low-S0ix suspend intervals.
Skip the proposed deliberate suspend/resume experiment for now; the fault already
exists before that transition. Proceed to unchanged BIOS photos and the Windows
comparison rather than repeating this same Linux observation.

### Offline firmware trace

`collect_acpi.py` copied 21 DSDT/SSDT tables into
`runs/2026-09-22T193948Z-acpi-hfz5fib3/`, validating lengths/checksums and recording
SHA256 hashes. Licensing/UEFI tables were excluded. Ubuntu's `acpica-tools`
20251212-1 package was downloaded, verified against local apt SHA256 metadata,
and unpacked only under `.tools/acpica/`; nothing was installed system-wide.

The DSDT disassembled successfully into `00-DSDT.dsl`. Relevant implementation:

```text
_SB.LID0._LID()                    (lines 79923–79930)
  -> ECG3()                       (79173–79176)
  -> ECBT(0, 0x10)                (79120–79129)
  -> _SB.PC00.LPCB.ECDV.ECR1(0)   (78022–78034)
```

This masks bit 4 (`0x10`) of EC byte zero and returns 0/1. With `ECRD != 0`,
`ECR1` reads `EC00` in an EmbeddedControl OperationRegion; otherwise it uses
firmware method `EISC(0x80, 0, 0)`. `_LID` zero means closed; raw `SW_LID` uses
the inverse convention (one means closed). This is not just a stale logind cache.
Live `ECRD` was not read, and no direct EC access or arbitrary AML evaluation
was performed. The static trace does **not** establish a defective physical sensor.
The EC `_GPE` method returns `0x6E` when `ESPI` is true, otherwise zero.

An initial combined static+dynamic table disassembly failed with duplicate-object
errors (`AE_ALREADY_EXISTS`); DSDT-only disassembly succeeded. These are offline
namespace collisions, not evidence of a newly occurring live firmware error.
PMC debugfs was also located under `pmc_core` (rather than `intel_pmc_core`), and
its existing residency exports are now captured.

### Read-only hibernation feasibility check

The user asked whether `HandleLidSwitch=hibernate` could mitigate drain. No sleep
or configuration change was attempted. Current results:

- `/sys/power/state`: `freeze mem` (no `disk`).
- `/sys/power/disk`: `[disabled]`.
- logind `CanHibernate` and `CanSuspendThenHibernate`: both `"na"`.
- Kernel config has `CONFIG_HIBERNATION=y`; the precise reason for runtime
  unavailability is **not yet identified**. Boot-journal inspection did not explain
  it. Secure Boot is disabled and kernel lockdown reports `[none]`, so those are
  not supported explanations in this boot. `CONFIG_SECRETMEM=y` alone does not
  establish whether secret memory is active or responsible.
- `/sys/power/resume`: `0:0`, resume offset 0, and no explicit resume/hibernate
  arguments were found in the current kernel command line. These are current
  configuration observations, not by themselves proof that automatic resume
  discovery could never work.
- Root remains ZFS. RAM is approximately 32 GB (30.6 GiB); active swap is 8 GiB at
  `/dev/dm-1`, mapped as `dm_crypt-0`.
- The matching `/etc/crypttab` entry uses **`/dev/urandom`** with plain encrypted
  swap (`cipher=aes-cbc-essiv:sha256,initramfs,plain,size=256,swap`). Its random key
  is recreated on activation; this is not a persistent cross-boot hibernation
  resume target. No encryption-key contents were read. This is a **separate resume
  design problem**, not an established cause of `/sys/power/disk` being disabled.
- Required hibernation image space depends on workload and compression; 8 GiB
  versus approximately 32 GB RAM is a capacity constraint, not alone a proof that
  every possible image must fail to fit.

Changing logind's lid action cannot solve an absent input event: logind currently
never observes a closed lid. A custom handler consuming the same stuck-open signal
would face the same problem. Once hibernation is made available and a safe persistent
resume target is configured/tested, **manual hibernate** can mitigate drain without
a working lid signal. Suspend-then-hibernate can cap drain after an independently
successful suspend request, but cannot rescue a lid closure that never initiated
sleep. Hibernate/resume setup requires separate approval for any boot, encryption,
swap, or storage changes; do not disable encryption or alter ZFS to make a quick fix.

The traced firmware lid path reads an EC bit via ACPI `LID0._LID`. The separately
enumerated `intel-hid` device reports tablet mode. No evidence yet establishes a
**gyroscope-based lid detector**, a defective gyro, or an unfixable hardware limit;
the EC's underlying physical sensing implementation remains unverified.

## Likely

- The disabled BIOS LidSwitch explains the current Windows lid failure: enabling
  only that attribute restored lid-triggered sleep/wake without a reboot. It is
  also the leading explanation for the earlier Linux stuck-open state, pending
  the corresponding Linux retest. Historical setting provenance remains unknown.
- The lid signal/reporting issue is upstream of logind: confirmed physical
  closures remain invisible to raw input and ACPI, including the completed
  pre-first-suspend observation after reboot.
- The original good/bad suspend-path distinction remains important, but identifying
  an interval as “normal lid-triggered” now requires raw-event and journal evidence;
  a manual or idle-timer suspend should not be mislabeled.
- The initial Windows failure contradicted a Linux-only explanation. Windows
  notifications and sleep now work after the approved firmware-setting change;
  Linux reporting must be checked with that same Enabled setting.

## Possible

- The EC/sensor reporting path or kernel/firmware EC access returns a stale or
  incorrect lid bit. Physical sensor failure is only one possibility.
- EC/firmware state retained across ordinary reboot could still matter. A suspend
  in the current Ubuntu boot is not necessary: pre-first-suspend failure is now
  reproduced. An ordinary reboot is not a guaranteed EC reset.
- Any remaining Linux-specific firmware/EC/driver behavior becomes relevant only
  if the Linux retest still fails with LidSwitch Enabled. The successful Windows
  cycle argues against a permanently failed lid sensor, not all intermittent faults.
- A notification failure alone is insufficient to explain the ACPI-aware result,
  because a direct `_LID` query also continued to report open.
- The Intel HID late-registration/resume warning and convertible/sensor state may
  be related to the drain, but causality is not established.
- The reported lid-path S0ix difference could involve event ordering, device PM,
  or firmware state; no device has been isolated.

## Ruled out / not supported

- S3 is not advertised; forcing it is outside this investigation.
- A universal VMD S0ix blocker is contradicted by supplied successful controls.
- Battery wear alone does not explain the supplied residency difference.
- Missing logind udev tagging is not supported by current enumeration.
- Charging is not necessary for the lid-reporting failure; it was reproduced on
  battery, including repeated ACPI queries after reboot.
- Suspend/resume within the current Ubuntu boot is not a necessary trigger.
- Current raw/logind disagreement is not observed: both remain open. This does not
  prove correct physical reporting, because the user confirms the lid was closed.
- Touchpad event counts without wakeup counts do not establish causation.
- No reason yet to flash BIOS, blacklist modules, unbind input hardware, or change
  storage or permanent logind configuration.

## BIOS and cross-OS comparison preparation

### Windows preflight and supplied BIOS photos — 2026-09-26

The existing Windows checkout is now available on the laptop. Four supplied
photos in ignored `screenshots/` were reviewed locally; the overview images
contain machine identifiers and must remain private. Only the relevant settings
are transcribed here:

| Photo | Visible evidence |
| --- | --- |
| `1000020428.jpg` | XPS 13 9310 2-in-1; BIOS **2.36.0**; battery 100%, Idle, firmware health label Good; 45 W adapter. Manufacture/ownership dates are not BIOS release dates. |
| `1000020429.jpg` | Continuation of hardware overview; no lid or sleep settings. |
| `1000020434.jpg` | All CPU cores selected; Intel SpeedStep **ON**; Enable C-State Control **ON**. |
| `1000020436.jpg` | Firmware Fastboot **Minimal**; extended BIOS POST time 0 seconds; dock warning messages ON. |

All four photos show zero pending changes at capture time. This is not proof of
the full BIOS-session history. **Lid Switch, Block Sleep, and wake-policy controls
are not pictured**; their values were subsequently read through Dell WMI below.
No extra BIOS visit was needed
before the Windows baseline. Fastboot Minimal is a possible initialization
variable because its displayed help describes skipping some initialization; it
is not an established cause, and no setting was changed. Enabled C-states rule
out that visible toggle being off, not firmware/runtime power-state failures.
The firmware's qualitative battery-health label does not supersede the Linux
full/design capacity measurements.

Read-only Windows preflight found:

- Windows 11 Pro, build **26200**, BIOS **2.36.0** (release date 2025-11-26).
- Last boot **2026-09-26 09:04:16.500 UTC** (03:04:16.500 MDT).
- AC online and battery 100% at preflight, unlike the battery-only Ubuntu baseline.
- `powercfg /a`: S0 Low Power Idle, Network Connected; Hibernate and Fast Startup
  available; S3 unavailable. These are advertised capabilities, not residency data.
- Active Dell scheme: `powercfg /qh SCHEME_CURRENT SUB_BUTTONS` exposes hidden
  **Lid close action = Sleep (1) on both AC and DC**. Lid open action is Turn on
  the display (1) on both. No Control Panel change is needed for this comparison.
- Idle sleep timeout is five hours on AC and three minutes on DC. Keep AC/devices
  unchanged during capture; a coincident idle action remains a timing confounder.
- The successful System Kernel-Power/Power-Troubleshooter query since boot returned
  two battery-count events (521), with no sleep/resume event at preflight time.
  This does not replace the user's physical-action and boot-history report.
- The terminal is not an administrator. WMI initially failed inside the sandbox;
  the same read-only queries succeeded outside it. The collector still requires
  a Windows Administrator token for its reports.

### First Windows capture — confirmed closure, no recorded sleep

Private run: `runs/windows/lid-20260926T091933Z-8ec5dbe66ca04e00b85795762cb66c28/`.
The collector ran under one UAC administrator launch and exited normally. The
agent relayed readiness and the ten-second close/reopen instruction, then the
completion notice. No sleep request, inhibitor, wake timer, or policy change was
issued by the agent or collector.

- Observation: **09:19:35.981–09:21:06.117 UTC** (03:19:35.981–03:21:06.117 MDT).
  Collection finished at **09:21:24.350 UTC**.
- Both context snapshots succeeded with empty `Errors`; OS, BIOS, and boot time
  matched. AC remained online at both endpoints, battery 100%.
- Full active-policy and hidden button/lid-policy outputs matched before/after.
  This does not exclude transient changes between endpoints.
- The event query succeeded and returned **zero matching events** in
  09:19:20.981–09:21:21.117 UTC. This was not a timeout or event-log permission error.
- SleepStudy was generated successfully. Its embedded scenario data shows an
  **Active** interval from 09:04:44 through 09:21:22 UTC spanning the observation,
  with no recorded Screen Off/Sleep interval for the trial. The report contains
  earlier history too; those earlier sessions are not this physical test.
- Ten commands completed normally. `/systemsleepdiagnostics` exited zero but
  produced only a deprecation message directing callers to `/systempowerreport`;
  its absent report was correctly flagged `report_missing_or_empty`. PowerShell
  child stderr contains module-loading progress in CLIXML, not a snapshot error.
- The collector now includes a conditional `/systempowerreport` fallback, retaining
  the legacy status and using a separate log/report within the original deadline.
  This was added after the run and validated in isolation; it was not retroactively
  run or inserted into this capture. Hidden lid-policy snapshots and comparisons
  were added before the run and were exercised successfully on the laptop.

The user subsequently confirmed closing the lid for **ten seconds** after the
readiness instruction. Reopening showed the already logged-in Windows desktop,
without a password prompt or logout/login behavior. That visible behavior alone
is not a sleep measurement; the stronger evidence is the successful empty event
query and SleepStudy Active interval with unchanged Sleep lid policy.

**Interpretation:** this trial did not demonstrate Windows lid-triggered sleep.
It does not establish that Windows received no lid notification, or prove a
defective sensor. The historical recollection that Windows worked is not
reproduced in this trial. Do not repeat the same report-only closure without a
new measurement. No overnight drain or sustained S0ix measurement was made.

Read-only follow-up after confirmation found:

- ACPI Lid (`PNP0C0D`) is present, status OK, PnP problem code 0; its reported
  driver version is 10.0.26100.1150. Successful enumeration is not proof that
  physical transitions reach Windows.
- Intel HID Event Filter (`INTC1051`, 2.2.2.9), Intel Integrated Sensor Solution
  (3.1.0.4477), and the enumerated HID Sensor Collection V2 devices also report
  OK with problem code 0. These statuses do not establish correct runtime input.
- A later successful event query covering the original interval still returns
  no Kernel-Power/Power-Troubleshooter events, reducing concern about delayed
  event delivery. AC/DC lid-close actions remain Sleep.
- The read-only request-override query lists no overrides. The live `/requests`
  query requires an administrator token and was unavailable in this follow-up;
  there is no supported claim that all power requests are absent.
- A later open-lid context check reports laptop mode (`SM_CONVERTIBLESLATEMODE=1`),
  undocked (`SM_SYSTEMDOCKED=0`), one desktop monitor, and a local session. WMI
  reports one active internal display. These are post-trial observations, not
  measurements of tablet/dock state during the closure. Metric meanings follow
  [Microsoft's GetSystemMetrics documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getsystemmetrics).

### Dell BIOS WMI: lid switch disabled

Private follow-up: `runs/windows/context-20260926T093247Z-51f0a84b13c14bf5ba78d716625765c8/`,
collected at 09:32:48–09:32:54 UTC under a grouped administrator launch. The earlier
nonadministrator query returned Access denied; the elevated query succeeded.
Only CIM reads and power-report queries were used; no BIOS methods were invoked.

Before the approved change, at 09:32 UTC,
`root\dcim\sysman\biosattributes:EnumerationAttribute` returned these values:

| Attribute | Pre-change value |
| --- | --- |
| **LidSwitch** | **Disabled** |
| PowerOnLidOpen | Disabled |
| BlockSleep | Disabled |
| CStatesCtrl | Enabled |
| Fastboot | Minimal |
| WakeOnDock, WakeOnAc, WakeOnLan, AutoOn | Disabled |

Dell documents `LidSwitch` as the lid-function enable/disable control: when
enabled the OS determines lid-close display behavior, while disabling it prevents
closure from affecting the display. The separate `PowerOnLidOpen` attribute
controls starting from the powered-off state. The current machine exposes both
attributes, resolving the ambiguity in the older model-specific service manual.
Sources: [Dell LidSwitch reference](https://www.dell.com/support/manuals/en-us/command-configure/dcc_cli_4.2/--lidswitch?guid=guid-bd4e530d-a232-4d34-8707-6484deaa5877&lang=en-us),
[Dell PowerOnLidOpen reference](https://www.dell.com/support/manuals/en-tt/command-powershell-provider/dellcommandpowershellprovider_rg_2.3/powermanagement?guid=guid-18b6dc2e-6af2-4537-9cc2-4abc5bf94e6c&lang=en-us).

**Inference:** the disabled lid-function control is now the leading explanation
for current ignored closures, and is more actionable than assuming a faulty
sensor or Linux driver. It does not establish when the setting changed, explain
all historical S0ix measurements, or prove that enabling it fixes overnight drain.
Fastboot is not the next variable to change.

All four follow-up commands completed successfully, including a nonempty
`system-power-report.html`. `/requests` showed a SYSTEM request from Zed
(Agent thread in progress) and an EXECUTION request from the Windows Update
worker; DISPLAY and AWAYMODE were empty. `/requestsoverride` showed no overrides.
These are post-trial snapshots, not evidence of their exact state during closure.
System requests concern inactivity sleep; Microsoft documents that power requests
are normally terminated at user-initiated sleep entry. Do not conclude that the
Zed request caused the missing lid response or clear it as an unmeasured fix.
Source: [Microsoft PowerSetRequest](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-powersetrequest).

### Direct Windows notification helper — passive baseline only

`windows_lid_notifications.ps1` was prepared for follow-up while the firmware
query was pending. A nonadministrator, five-second **open-lid-only** validation
completed at 09:35:35 UTC in
`runs/windows/notifications-20260926T093529Z-4d0d085f4739401aa5995f7137c64a45/`.
Both lid and AC-source registrations/unregistrations returned zero. Initial
notifications reported lid **open** and power source **AC**; no further changes
were observed while the lid stayed open. This validates callback delivery and
cleanup, not a physical closure or sensor correctness. At that point, a second
physical trial was deferred until the user authorized the LidSwitch change.

PowerShell parsing, C# compilation, native layout checks, 13 synthetic payload
cases, managed callback lifetime/worker-thread queue checks, and idempotent
unregistered cleanup pass in `test_windows_lid_notifications.ps1`. The listener
was later used in the authorized post-change retest below. It requests
no power action and keeps initial/absent notifications distinct from transitions.

### Authorized LidSwitch enable and successful Windows retest

User authorization: **"Yes, enable LidSwitch then retest"**. The one-time local
helper `.tools/enable_lidswitch_retest.ps1` issued exactly one
`BIOSAttributeInterface.SetAttribute` call, targeting only `LidSwitch=Enabled`.
It used Dell's native WMI interface, with local method-schema checks, saved
before/after snapshots, and no-password authentication. No packages, firmware
flash, BIOS defaults/reset, or reboot were involved.

Private parent run:
`runs/windows/enable-lidswitch-20260926T094727Z-2204ea965d804fa3896260affde06906/`.
Its `change-and-retest.json` records the authorization, successful `SetStatus=0`,
`PreviousValue=Disabled`, `VerifiedValue=Enabled`, no other enumeration/integer
changes, and both recorder exits as zero. All 98 enumeration and six integer
values were compared. String/security/boot-order classes were not part of that
comparison, and no writes targeting them were issued.

The existing report collector and direct notification listener then ran together
under the same administrator launch. Their files are in the parent's `reports/`
and `notifications/` children. Both were ready by **09:47:39.217 UTC**; the shared
physical window ended **09:49:08.413 UTC**. Collection ended **09:49:25.279 UTC**.

| Evidence | UTC time / result |
| --- | --- |
| Initial lid notification | 09:47:39.129 — open |
| Lid-close notification | 09:47:54.171 — closed |
| Kernel-Power 506 | 09:47:55.063 — entering Modern Standby, **Reason: Lid** |
| Lid-open notification | 09:48:09.577 — open |
| Kernel-Power 507 | 09:48:09.951 — exiting Modern Standby, **Reason: Lid** |
| SleepStudy | Screen Off 09:47:55–09:47:56; Sleep 09:47:56–09:48:09; then Active; exit reason Lid |

The callback close/open interval was about **15.4 seconds**, and the Modern
Standby entry/exit interval about **14.9 seconds**. These are measured receipt/event
intervals, not an assumed exact ten-second physical duration. Five power events
were captured in total; the others were session transitions. No invalid/unknown
notification payloads occurred. Both native subscriptions unregistered successfully.

OS build 26200, BIOS 2.36.0, and last boot 09:04:16.500 UTC matched before/after;
the change therefore took effect for this trial without restarting. Context
snapshots had no errors. AC remained Online, battery 100%, and full/lid power-policy
outputs matched at both endpoints; the listener reported AC with no change event.
The old `/systemsleepdiagnostics` command again reported deprecation/no file;
the new `/systempowerreport` fallback and SleepStudy both generated nonempty
reports successfully. No additional sleep command or inhibitor was used.

**Conclusion:** the current Windows lid-recognition and lid-triggered Modern
Standby failure is resolved by the approved single-setting change in this trial.
The user's description of the reopening experience is still pending, but OS
entry/exit reasons and direct state transitions are recorded. This short AC trial
does not establish sustained deep residency, battery savings, reboot persistence,
or restored Linux input. SleepStudy marks these short Screen Off/Sleep segments
without detailed trace data, so no S0ix-residency percentage is claimed.

### Earlier preparation (Ubuntu handoff, historical)

- The user recalls normal lid behavior in Windows and failures only in Linux.
  No fresh Windows or Pop!_OS comparison has been run; this remains reported history.
- Dell's model-specific service manual (Tables 10–13, pages 44–46) documents
  **Lid Switch** as powering up from off when the lid opens. It does not separately
  document **Enable Lid Switch**. Capture the actual BIOS page/help text rather
  than assuming a lid-open power-on setting disables OS lid reporting.
- The manual describes **Block Sleep** as blocking S3, **Enable C-State Control**
  as allowing CPU low-power states, and **Fastboot** as firmware initialization
  policy. Visible settings cannot rule out firmware bugs or sensor faults.
- [REBOOT_TEST_PLAN.md](REBOOT_TEST_PLAN.md) contains the verified photo checklist,
  fresh-boot decision tree, and Windows/Pop!_OS evidence/interpretation plan.
  The official PDF is saved only under ignored `.tools/dell-manual/`.
- Latest pre-reboot identity: `c72ec359-9f18-4fd6-aca3-67d38af4750e`.
  No reboot or suspend was initiated during this preparation.

## Next experiment

**The authorized LidSwitch enable and Windows retest are complete.** The earlier
Ubuntu fault was measured before this change. A post-change Ubuntu baseline is
next; no additional Windows closure is needed.

1. Save work and use Windows **Restart**, selecting Ubuntu through the existing
   boot selector. Keep the lid open and return before any Ubuntu suspend. No
   automatic reboot, hybrid shutdown, extra BIOS change, or EC reset is requested.
2. Verify the new Ubuntu boot, BIOS/power context, and absence of prior suspend.
   Run the existing protected `python3 -B raw_lid_session.py --acpi-lid` capture;
   after readiness, coordinate one ten-second closure/reopen. It should now
   register ACPI/raw/logind transitions if the BIOS setting explains Linux too.
3. If raw reporting works, proceed to controlled PC10/S0ix and drain measurements
   before claiming an overnight fix. If it still fails, preserve the boot's
   evidence and investigate the remaining Linux path with the Enabled setting.

**Comparison caveat:** this Ubuntu boot follows both Windows and an intentional
LidSwitch change. Improvement cannot be attributed solely to Windows initializing
firmware. Record the changed setting in every subsequent result.

Keep `LidSwitch=Enabled`. Its saved rollback value is Disabled, but do not undo
the successful change automatically. `PowerOnLidOpen` stays Disabled, Fastboot
Minimal, and other settings remain as captured. The user's approval covered this
single-setting change and retest, not unrelated firmware changes.

The original handoff sequence is retained for context:

1. The user authorized publishing the source and curated findings to the public
   GitHub repository for cloning in Windows. Use that checkout for the collector;
   raw captures/downloads remain excluded. Arrange private transfer of reviewed
   Windows results and BIOS photos separately; do not alter or mount partitions
   to create a shared path.
2. Save work, photograph current BIOS settings per `REBOOT_TEST_PLAN.md`, exit
   without changes, and boot the existing Windows installation. Do not reset or
   flash firmware, change storage/Secure Boot, or alter boot order.
3. Inspect and photograph existing Windows lid-close policy. If the applicable
   action is Shut down, Hibernate, or Do nothing, report it before proceeding;
   absence of ordinary sleep under those policies would not prove sensor failure.
4. Run the prepared `windows_lid_capture.ps1` under one Administrator PowerShell
   launch, following `WINDOWS_TEST.md`. It captures context/policy, marks a bounded
   90-second closure window, then collects event evidence and bounded power reports.
   It changes no power policy, requests no sleep itself, and makes no automatic
   lid pass/fail claim. Unlike the Linux test, it does not inhibit existing lid
   actions. Windows execution was initially unverified on Linux; September 26
   validation and the first Windows capture are recorded above. Reports may
   contain identifiers and prior-session activity.
5. Transfer reviewed evidence through the agreed location. Use Windows **Restart**,
   not hybrid shutdown, to boot Ubuntu; capture ACPI/raw/logind before the first
   Ubuntu suspend again. Keep power/peripherals fixed where possible and record
   any deviations, updates, BIOS visit, or earlier sleep.

A verified Windows response makes permanent sensor failure less likely, not
impossible. Ubuntu working only after Windows would support an initialization/
retained-state hypothesis, not identify one driver. Pop!_OS is an optional second
Linux control with actual kernel and boot history recorded. A normal reboot is
not a guaranteed EC reset; no power-button/RTC reset or EC writes are authorized.

Then perform RTC-backed A/B/C tests only when raw lid state is trustworthy. C must
verify raw closed state, not infer it from timing. Compare platform residency, not
merely suspend success. Hibernation remains a possible fallback, but the read-only
check above found current runtime unavailability and random-key encrypted swap.
Resolve those separately and review a safe persistent resume design before seeking
approval for any configuration changes, especially with this ZFS root.

## Validation and operational notes

- 93 hardware-independent tests passed after incremental sample saving and end
  notification changes (previous suite: 88). Added coverage for pre-finalization
  flushing, evidence-file non-overwrite, nonzero/early worker exit notifications,
  and an unavailable desktop-notification helper.
- Real privileged raw reads, bounded inhibitors, result ownership handback, and
  corrected desktop readiness flow exercised on this host.
- Replacement live capture completed with all 174 incremental samples matching
  the final JSON, correct end notification, and verified ownership/inhibitor cleanup.
- Windows PowerShell 5.1.26100.7920 parser and 14 isolated checks
  passed on September 26. The first elevated Windows capture completed; see the
  missing legacy report and lack of direct lid telemetry above. The runner
  checks cover success, nonzero exit, timeout, skipped deadline, and report
  presence/empty/missing/partial handling; six further cases check legacy-report
  fallback decisions and preservation of an expired deadline. Raw synthetic
  artifacts remain ignored.
- No real A/B/C suspend or RTC wake test has run in this session.
- Zed's terminal sandbox cannot start because its running executable was deleted;
  explicit unsandboxed approvals were needed even for read-only commands.
- The provided folder initially contained only `Agent.md` and no `.git` metadata.
  The user subsequently authorized GitHub publication for the Windows handoff.
  Remote inspection found the public repository empty with default branch `main`.
  Only source, tests, the historical brief, and curated documentation are intended
  for Git; `.gitignore` excludes raw captures, downloaded tools, and session files.
