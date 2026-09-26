# Investigation findings — 2026-09-22

## Status

**Power-drain root cause and permanent fix are not yet established.** The current
investigation has reproduced an OS-visible lid-reporting failure upstream of
logind: the user confirmed ten-second physical closures in two protected tests,
but raw input remained open; in the second, ACPI `_LID` also remained open.
A third completed and user-confirmed closure after reboot reproduced the failure
before this Ubuntu boot's first suspend, on battery power. Do not install a custom
lid handler until its input signal is reliable.

No storage, firmware, boot, kernel-module, desktop/logind, PAM, filesystem, or
hibernation settings have been changed. No new suspend experiment has been executed.

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

- The lid signal/reporting issue is upstream of logind: confirmed physical
  closures remain invisible to raw input and ACPI, including the completed
  pre-first-suspend observation after reboot.
- The original good/bad suspend-path distinction remains important, but identifying
  an interval as “normal lid-triggered” now requires raw-event and journal evidence;
  a manual or idle-timer suspend should not be mislabeled.

## Possible

- The EC/sensor reporting path or kernel/firmware EC access returns a stale or
  incorrect lid bit. Physical sensor failure is only one possibility.
- EC/firmware state retained across ordinary reboot could still matter. A suspend
  in the current Ubuntu boot is not necessary: pre-first-suspend failure is now
  reproduced. An ordinary reboot is not a guaranteed EC reset.
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

**The direct Ubuntu reboot baseline is complete and the reporting fault persists.**
No additional same-boot lid closure or deliberate Linux suspend is needed now.
Next: unchanged BIOS photographs, then Windows, then Windows **Restart → Ubuntu**.
Do not restart or shut down automatically while work may be unsaved.

Hypothesis: Windows recognizes the same physical closure, or initializes firmware
state differently enough to change subsequent Ubuntu reporting. This distinguishes
OS-dependent behavior from a permanently failed sensor without assuming either.

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
   actions. Its Windows execution remains unverified; PowerShell is unavailable
   on this Linux host. Reports may contain identifiers and prior-session activity.
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
- Windows collector reviewed on Linux only; no PowerShell parser/runtime or Windows
  hardware validation was possible locally (`pwsh` unavailable).
- No real A/B/C suspend or RTC wake test has run in this session.
- Zed's terminal sandbox cannot start because its running executable was deleted;
  explicit unsandboxed approvals were needed even for read-only commands.
- The provided folder initially contained only `Agent.md` and no `.git` metadata.
  The user subsequently authorized GitHub publication for the Windows handoff.
  Remote inspection found the public repository empty with default branch `main`.
  Only source, tests, the historical brief, and curated documentation are intended
  for Git; `.gitignore` excludes raw captures, downloaded tools, and session files.
