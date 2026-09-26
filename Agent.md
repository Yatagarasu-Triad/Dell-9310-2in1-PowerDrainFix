You are taking ownership of a Linux power-management debugging investigation on a Dell XPS 13 9310 2-in-1.

The user wants this to be as automated and hands-off as possible.

Your job is to diagnose and, if safely possible, fix severe battery drain during lid-closed suspend.

Do not treat this as generic Linux battery troubleshooting. We already have substantial experimental evidence. Start from that evidence, verify where useful, and design the smallest experiments needed to isolate the remaining fault.

## System

Laptop:
Dell XPS 13 9310 2-in-1

CPU:
Intel Core i7-1165G7 / Tiger Lake

OS:
Ubuntu 26.04.1 LTS

Kernel:
7.0.0-30-generic

BIOS:
2.19.0
dated 2023-07-13

Sleep modes:

`cat /sys/power/mem_sleep`

returns:

`[s2idle]`

There is no S3/deep mode exposed.

Storage:
KIOXIA BG4 NVMe
behind Intel VMD

Relevant PCI driver:
`vmd`

Dell BIOS storage mode is effectively RAID/VMD.

DO NOT change RAID/VMD/AHCI as part of initial debugging.

Battery:

design charge:
6,577,000

current full charge:
4,458,000

battery health:
approximately 67.8%

The battery is worn, but battery wear alone does not explain the measured suspend behavior.

## Known experimental results

### Short software-initiated suspend, lid OPEN

10-minute test.

BEFORE:

CPU PC10:
5293339252

PCH S0ix:
3653015005

AFTER:

CPU PC10:
5893866537

PCH S0ix:
4252656459

Deltas:

CPU PC10:
~600.53 seconds

PCH S0ix:
~599.64 seconds

Interpretation:

The laptop is fully capable of reaching essentially perfect PC10 and S0ix residency under the current BIOS, kernel, VMD, NVMe and general hardware configuration.

This is an extremely important control result.

### Normal lid-close suspend

Approximately 10-minute test.

BEFORE:

CPU PC10:
5894640474

PCH S0ix:
4252916066

AFTER:

CPU PC10:
6033405370

PCH S0ix:
4343057704

Approximate deltas:

CPU PC10:
~138.76 seconds

PCH S0ix:
~90.14 seconds

Interpretation:

Normal lid-triggered suspend dramatically reduces deep-idle residency.

### Overnight normal lid-close test

Start:
2026-09-21 22:04 approximately

End:
2026-09-22 09:29 approximately

Battery:

77% -> 65%

charge_now:

3,415,000 -> 2,892,000

S0ix counter:

119,378,431 -> 119,663,047

Delta:

~284,616 microseconds
~0.285 seconds

Despite spending roughly 11.4 hours in an apparent s2idle suspend session, the machine accumulated essentially zero deepest-platform S0ix residency.

Kernel log showed one continuous suspend session rather than frequent full resumes.

Measured battery consumption was roughly 3.9–4.0 Wh over the interval, around 0.35 W average.

This explains the user's real-world complaint that the laptop is frequently dead after being left unused for a few days.

### Manual suspend while lid was physically closed

A temporary systemd low-level inhibitor was used:

`--what=handle-lid-switch`

Then an RTC wake was scheduled and `systemctl suspend` was triggered by a timer.

PCH S0ix BEFORE:

4344712821

AFTER:

4950771135

Delta:

~606.06 seconds

That is essentially perfect S0ix residency again.

However:

`org.freedesktop.login1.Manager.LidClosed`

was captured as:

`b false`

So this test strongly suggests that the physical closed-lid condition may NOT itself block S0ix, but it did not definitively prove that systemd/logind had registered the lid as closed at the exact time suspend began.

The CPU PC10 counter wrapped/reset in this test and should not be used as the primary metric for that run.

PCH S0ix was internally consistent and should be treated as the stronger metric.

## Lid-switch observations

Effective logind config includes:

`HandleLidSwitch=suspend`
`HandleLidSwitchExternalPower=suspend`

No `handle-lid-switch` inhibitor normally exists.

During a diagnostic where a temporary `handle-lid-switch` inhibitor WAS installed:

* physically closing the lid did not auto-suspend, which is expected
* `LidClosed` remained `b false` across repeated polling
* no lid-open/lid-close log messages appeared in the queried systemd-logind history

The inhibitor preventing auto-suspend is expected behavior.

The surprising part is that `LidClosed` never changed to true.

We therefore need to inspect the kernel's raw lid-switch reporting rather than inferring from logind.

## Additional observations

Wakeup source output has shown:

`i2c-DLL09FF:01`

with many events.

This corresponds to the Synaptics I2C touchpad on this machine.

During one overnight test:

event_count:
14009 -> 15939

but wakeup_count remained zero.

Do not conclude that the touchpad is the cause yet.

Kernel has also emitted:

`input input28: PM: parent INTC1051:00 should not be sleeping`

This may be relevant.

The platform contains Intel HID / convertible / tablet-mode hardware and an Intel Integrated Sensor Hub.

The 9310 2-in-1 has historically had Linux issues involving motion/rotation/tablet/lid behavior, but do not assume the current bug is identical.

## Critical conclusion so far

DO NOT spend time trying to force S3.

DO NOT assume Intel VMD is the root cause.

DO NOT change RAID -> AHCI at this stage.

The machine has already demonstrated near-perfect S0ix with the same VMD configuration.

The current highest-value hypothesis is:

Something about the NORMAL LID-CLOSE SUSPEND PATH causes the system to enter s2idle without maintaining proper PC10/S0ix residency.

Possible areas include:

* raw ACPI lid switch
* Linux EV_SW / SW_LID reporting
* systemd-logind lid handling
* GNOME power handling
* Intel HID / INTC1051
* tablet-mode state
* Intel Integrated Sensor Hub
* touchpad / I2C state transitions
* ordering differences between lid-triggered suspend and manually invoked suspend
* a driver remaining active because of the lid event
* firmware/ACPI behavior associated with the lid transition

## Safety requirements

Work autonomously on all safe, non-destructive diagnostics.

Do NOT:

* change RAID/VMD/AHCI
* flash BIOS
* change firmware
* modify UEFI variables
* disable Secure Boot settings
* change partitioning
* modify ZFS configuration
* delete or alter user data
* make persistent kernel module blacklists without explicit approval
* permanently change systemd/logind/GNOME configuration without explicit approval
* modify PAM
* touch the fingerprint reader project
* unload critical storage, graphics or input modules blindly
* perform persistent ACPI/DSDT hacks
* install experimental kernels unless explicitly approved

Temporary runtime-only configuration changes are acceptable if:

1. they are clearly documented,
2. reversible automatically,
3. cleanup is performed even after failure where possible,
4. they do not risk filesystem/data loss.

If a test could strand the user with no keyboard, no display, no storage or no ability to resume, ask before executing it.

## Automation objective

Minimize user involvement.

Build a diagnostic harness rather than repeatedly asking the user to manually run commands.

Create a working directory such as:

`~/xps9310-s0ix-debug/`

Use timestamped result directories such as:

`runs/2026-09-22T120000-test-name/`

Capture:

* test metadata
* before counters
* after counters
* elapsed wall time
* battery state
* PC10 residency
* S0ix residency
* wakeup_sources
* relevant sysfs state
* logind state
* kernel journal
* systemd-logind journal
* input-device state
* ACPI wake configuration
* interrupt deltas where useful
* relevant driver/device power-control state

Write one primary orchestration tool such as:

`xps-s0ix-diagnose.sh`

and small helper tools where appropriate.

Prefer Python for raw input-device ioctl work if it substantially improves reliability/readability.

Automatically calculate deltas and residency percentages.

Generate a human-readable summary after each test.

## Phase 1 — Enumerate the raw lid/input path

Before doing another suspend test, identify all devices capable of reporting:

EV_SW / SW_LID
SW_TABLET_MODE

Inspect:

`/proc/bus/input/devices`

`/sys/class/input/event*/device/name`

`/sys/class/input/event*/device/capabilities/sw`

`udevadm info`

and other relevant input metadata.

Determine which `/dev/input/eventX` exposes SW_LID.

Do not rely solely on `LidClosed`.

Build a small probe, preferably:

`lid_probe.py`

that can:

1. enumerate EV_SW devices,
2. show their device names and physical paths,
3. query CURRENT SW_LID and SW_TABLET_MODE state using EVIOCGSW,
4. stream EV_SW events with timestamps,
5. distinguish SW_LID from SW_TABLET_MODE,
6. log events to a file.

Avoid requiring evtest if we can implement this using Python stdlib + ioctl.

If permission prevents reading `/dev/input/eventX`, use sudo only for the probe.

Do not install packages unless necessary.

## Physical-interaction rule

The agent cannot physically close/open the lid.

Only ask the user to intervene when the experiment literally requires a physical action.

When needed, give one compact instruction such as:

"Close the lid now for 10 seconds, then reopen it."

Do not make the user run additional commands during that interval.

The harness should already be running and recording everything.

## Phase 2 — Raw lid-state test

With suspend temporarily inhibited, record raw EV_SW state.

Have the user:

1. leave lid open
2. close lid for ~10 seconds
3. reopen lid

Record:

SW_LID before
SW_LID during closure
SW_LID after
SW_TABLET_MODE changes
systemd `LidClosed`
logind journal
kernel/input events

Answer these questions:

A. Does Linux input report SW_LID correctly?

B. Does systemd-logind receive/update the state correctly?

C. Does closing the lid also toggle tablet mode or another switch unexpectedly?

D. Does the lid event originate from ACPI button, Intel HID, sensor hub, or another device?

## Phase 3 — Reproduce good vs bad suspend automatically

Build an automated A/B harness.

Test A:

software-initiated suspend with lid open

Test B:

normal lid-triggered suspend

Test C:

lid physically closed, lid action inhibited, then software-initiated suspend

Each test should use approximately 10 minutes and RTC wake.

Automatically capture PCH S0ix residency before and after.

Treat:

`/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us`

as the primary platform metric.

Capture PC10 as a secondary metric but tolerate counter reset/wrap.

For each run compute:

elapsed_seconds
s0ix_delta_seconds
s0ix_residency_percent
battery_delta
battery_energy delta if available

Do not ask the user to manually transcribe values.

## Phase 4 — Compare system state before good vs bad suspend

The core debugging goal is now:

What is different immediately before entering s2idle in the GOOD software-triggered path versus the BAD lid-triggered path?

Capture and diff:

`/proc/interrupts`

`/proc/acpi/wakeup`

`/sys/kernel/debug/wakeup_sources`

runtime PM states:

`power/control`
`power/runtime_status`
`power/runtime_active_time`
`power/runtime_suspended_time`

for relevant devices including:

* Intel HID / INTC1051
* Intel ISH
* Synaptics I2C touchpad DLL09FF
* USB controllers
* Thunderbolt/USB-C
* NVMe/VMD
* audio
* Wi-Fi
* Bluetooth
* HID sensor devices

Also inspect driver bindings and ACPI/device ancestry.

Focus on DIFFERENCES between the working and failing state.

Do not collect huge dumps without interpreting them.

## Phase 5 — Isolate the offending path

Once a likely blocker is found, prefer temporary runtime experiments.

Examples of acceptable controlled tests, depending on evidence:

* disable wakeup for a specific non-critical input device
* change a specific device's runtime PM control temporarily
* temporarily inhibit logind lid handling
* manually invoke suspend after observing a lid event
* temporarily stop GNOME power handling
* temporarily stop or mask a non-critical user-space power service
* temporarily unbind a non-critical input/sensor device IF it is safe and automatically rebound afterward

Every experiment must include:

hypothesis
specific change
expected result
rollback
measurement
interpretation

Do NOT shotgun-disable multiple devices at once.

One variable per test.

## Phase 6 — Identify a practical fix

Possible acceptable outcomes include:

1. a kernel/driver bug identified and worked around safely;
2. logind/GNOME lid-suspend ordering identified and replaced with a reliable custom lid action;
3. one device preventing S0ix identified and configured correctly;
4. a kernel regression identified with a known-good/newer upstream fix;
5. a firmware limitation identified and mitigated by suspend-then-hibernate.

The preferred fix should:

* survive reboot cleanly
* require minimal custom hacks
* avoid disabling meaningful hardware
* preserve normal lid behavior
* preserve instant resume if possible
* keep overnight battery drain low
* be reversible
* be documented

If the root cause cannot be fully fixed, implement or propose:

`suspend-then-hibernate`

as a pragmatic fallback so the machine cannot silently drain to zero over several days.

But do not jump to hibernation before attempting to understand the reproducible lid-triggered S0ix failure.

## BIOS note

The machine is currently BIOS 2.19.0.

A newer Dell BIOS may exist outside LVFS.

`fwupdmgr` currently reports System Firmware as up to date through its configured remote, so LVFS is not offering the newer Dell release.

Do not flash BIOS automatically.

If evidence suggests firmware is likely relevant, report that recommendation and explain exactly why before asking for approval.

## Battery note

Battery health is approximately 67.8%.

A replacement battery would materially improve runtime but would NOT fix the phantom drain if suspend continues consuming approximately 0.35 W.

Do not recommend buying a battery until power-state behavior is understood.

## Evidence discipline

Maintain a file such as:

`FINDINGS.md`

Use sections:

Confirmed
Likely
Possible
Ruled out
Next experiment

Current confirmed items should initially include:

* s2idle is the only advertised suspend mode
* software-triggered open-lid suspend can achieve ~100% PCH S0ix
* VMD does not universally prevent S0ix on this machine
* normal lid-triggered suspend can dramatically reduce S0ix residency
* overnight normal lid suspend produced effectively zero deep S0ix and ~12% battery loss
* manually invoked suspend can again produce ~600 sec PCH S0ix
* battery capacity is degraded to ~67.8%

Do not overstate hypotheses as facts.

## User interaction

The user is technically advanced but does not want to manually babysit another project.

Do the work yourself wherever tools permit.

When physical interaction is required, pause only at that exact point and ask for one action.

Examples:

"Please close the lid now and leave it closed until I tell you to reopen it."

or:

"Please reopen the lid now."

Once the action is done, resume automation.

Do not make the user copy/paste repetitive commands.

## First task

Start by:

1. creating the diagnostic workspace;
2. capturing baseline system metadata;
3. enumerating all EV_SW devices;
4. building and validating `lid_probe.py`;
5. identifying the source of SW_LID and SW_TABLET_MODE;
6. comparing raw input state against systemd-logind `LidClosed`;
7. preparing an automated 10-minute A/B suspend harness.

Do NOT change permanent configuration yet.

Once the raw lid-event path is understood, proceed autonomously through the smallest safe experiments needed to isolate why normal lid-triggered suspend destroys S0ix residency.

At meaningful milestones, summarize:

* what was tested
* what changed
* what the evidence proves
* what is ruled out
* current leading hypothesis
* next autonomous test

The ultimate goal is:

Close the lid normally
→ enter s2idle correctly
→ sustain PC10/S0ix
→ lose only a small amount of battery overnight
→ reopen and resume normally

Do not settle for "the laptop suspended successfully" if platform residency remains broken.
