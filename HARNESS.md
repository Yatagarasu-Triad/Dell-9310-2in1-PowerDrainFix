# Diagnostic harness

`xps-s0ix-diagnose.py` uses Python's standard library and existing Linux/systemd
commands. It installs nothing, mounts nothing, and changes no persistent settings.
`Agent.md`, `README.md`, `FINDINGS.md`, and the separately owned `lid_probe.py` are
not modified by this implementation.

## CLI

Run these commands in the project directory. **The suspend commands below really
suspend the machine when explicitly opted in. Do not run them unattended or with
unsaved work.** No physical suspend tests were run during implementation.

```sh
# Safe system-read-only collection (writes only its result directory).
python3 xps-s0ix-diagnose.py baseline

# Observe raw switches while temporarily blocking sleep and lid handling.
# Close for about 10 seconds and reopen only AFTER the readiness prompt.
python3 xps-s0ix-diagnose.py raw-lid --duration 30

# Optional worker handshake for an external orchestrator (foreground, not a daemon).
# The orchestrator asks for physical action only while this NEW marker is present.
python3 xps-s0ix-diagnose.py raw-lid --duration 60 --ready-file ./raw-lid.ready

# Separate experiment: compare ACPI _LID with raw switches and logind.
# Repeated _LID reads may perturb firmware; not enabled in ordinary raw-lid runs.
python3 xps-s0ix-diagnose.py raw-lid --duration 90 --acpi-lid --ready-file ./acpi-lid.ready

# A: software suspend; leave the lid open throughout.
sudo python3 xps-s0ix-diagnose.py open --duration 600 --allow-suspend

# B: leave open until prompted, then close normally; no test inhibitor is installed.
sudo python3 xps-s0ix-diagnose.py normal-lid --duration 600 --entry-timeout 60 --allow-suspend

# C: leave open until prompted, then close; raw SW_LID must become true.
sudo python3 xps-s0ix-diagnose.py closed-manual --duration 600 --entry-timeout 60 --allow-suspend

# Mocked, hardware-independent tests; no suspend or system changes.
python3 -B -m unittest -v test_xps_s0ix_diagnose.py
```

All subcommands accept `--output-root PATH` (default: `runs/` beside the script).
Each run creates an exclusive UTC-timestamped, mode-0700 directory. Results can
contain host/device information and journal messages: review them before sharing.
Baseline works without root, explicitly recording permission errors, unavailable
commands, and missing files; running it as root captures more information.

Suspend experiments require root, `--allow-suspend`, only `[s2idle]` advertised,
`pm_test` disabled, battery capacity at least 15% when available, no existing alarm
on any enumerated RTC, and an enabled RTC wakeup device. They require unambiguous,
readable **raw open SW_LID on every lid device initially**, not logind's LidClosed.
Unknown/conflicting states cause refusal. The built-in safety query uses Linux
EVIOCGSW directly and does not depend on a particular probe JSON schema.

`--duration` is 30–900 seconds for suspend and 1–120 seconds for observation.
`--entry-timeout` is 5–120 seconds:

- A and C arm the RTC for `duration` seconds immediately before issuing suspend.
- C waits at most `entry-timeout` seconds for raw lid closure; it rechecks closed
  state after capture and immediately before issuing suspend. A runtime
  `handle-lid-switch` inhibitor prevents ordinary lid handling in A and C.
- B arms once for `duration + entry-timeout` seconds (660 by default), then prompts
  for closure and polls the journal for actual suspend entry and exit. Thus the
  allowance is part of a fixed total RTC budget, **not a promise of exactly 600
  seconds asleep**. It never invokes `systemctl suspend` itself. If no suspend
  occurs, it still finishes as unverified at the bounded deadline; it does not
  keep extending or resetting the alarm.
- All tests allow ten extra seconds for journal propagation after the RTC budget.
  Individual external commands have timeouts. Capture time is additional.

The deliberately restricted RTC selector is
`--rtc /sys/class/rtc/rtc0/wakealarm` (also the default). The harness does not try
other RTCs, enable wakeup, or automatically change power settings. Absolute alarm
values are calculated from `rtc0/since_epoch`, not from an assumed UTC wall clock.
An existing alarm, even one apparently in the past, is never intentionally cleared
or replaced. The alarm is read back before suspend and cleared in `finally` only
if it is still the harness's alarm. If it changed externally, the harness reports
that condition and leaves it alone.

Inhibitors are live child processes, not daemons, services, or scheduled jobs.
Readiness is acknowledged by the protected child after acquisition. Closing the
parent pipe releases them, including after exceptions and handled SIGINT/SIGTERM;
they also have independent CLOCK_BOOTTIME lifetime limits (25 minutes for manual
suspend modes, observation duration plus 40 seconds for raw-lid). No persistent
inhibitor or logind configuration is created.

### Raw-lid readiness and orchestration

Raw observation acquires `sleep:handle-lid-switch` first, then starts the probe with
its own private `--ready-file`. The probe promises readiness only after opening a
readable SW_LID descriptor and emitting its initial state. The harness waits at
most **10 seconds** for this signal, checking that both child processes are alive.
Only then does it publish the optional harness `--ready-file` and print the close
instruction. No close instruction or harness marker is produced on failed
readiness; a reopening safety warning may still appear during cleanup. Input
access or inhibitor permission failures are reported; the harness
does not invoke sudo or request elevation itself.

The external marker's parent must already exist and the marker must **not** exist
(including dangling symlinks). Complete mode-0600 JSON is published atomically,
without replacement, and includes the worker PID, result directory, readiness
clocks, requested observation duration, and worker deadline in CLOCK_BOOTTIME.
The orchestrator should check both the marker and the worker's liveness, ask the
user to close/reopen promptly, and wait for the worker's exit and summary. There is
no acknowledgement wait: the probe's duration starts at probe readiness, not when
the human sees the instruction. The worker deadline includes ten seconds of exit
grace and is **not** extra physical observation time.

The harness removes its external marker before releasing the inhibitor on normal
completion, failure, or handled interruption, and never deletes a replacement
with another inode. Its private probe marker is also removed, and a stuck probe
is terminated/reaped (killed after a three-second termination grace). The inhibitor
lifetime includes the readiness allowance and 30 seconds of acquisition/exit/
cleanup margin. Before releasing an acquired raw-observation inhibitor, including
on probe startup/recording failure or handled interruption, the harness checks
fresh raw SW_LID state. If every readable lid is unambiguously open, it releases
without waiting. Closed, missing, conflicting, or unreadable state prints a
**reopen now** warning and gets at most **10 seconds** of polling while the guard
remains active. The grace is shortened to the remaining original `duration + 40`
budget, conservatively measured before acquisition with one second reserved for
release; it never extends the inhibitor lifetime. If open state is still unknown
at expiry, the harness warns explicitly and releases anyway. If the guard has
already exited, it warns without waiting. The result appears as `raw_reopen` in
`summary.json` and in the text summary, separately from the recorded cycle result.
The readiness marker is already removed during this reopening grace.

SIGKILL can bypass marker removal, summary generation, and ownership handback;
never treat a leftover marker without a live worker as readiness. Filesystems
that cannot publish the marker via a hard link cause a safe failure before the
physical-action prompt.

The desktop wrapper emits **RECORDING ENDED** / **Lid recording ended** only after
the worker exits following readiness. This is not a passed-test verdict: the exit
status and saved summary still require review. After reopening, wait for this notice
before confirming the physical action in chat; interrupting the agent's active tool
can cancel the capture. If the worker exits before readiness, the wrapper explicitly
says not to close. Notification failure does not abort recording, and the terminal
messages remain available. Neither process is detached to evade cancellation.

### Optional ACPI lid vs notification/input experiment

`raw-lid --acpi-lid` adds discovered `/proc/acpi/button/lid/*/state` reads and
`/sys/firmware/acpi/interrupts/sci` / `gpe_all` totals to the existing raw/logind
sampling loop. The existing 0.5-second sleep per iteration is unchanged; reads
and the bounded logind query add time, so this is not a guaranteed 2 Hz sampler.
Each sample records start/finish clocks and separate ACPI read-window clocks.
`--acpi-lid` is a harness option, not a probe option; an importing worker or wrapper
must forward it to the harness CLI. The readiness JSON records
`acpi_lid_polling`, and `summary.json.arguments.acpi_lid` identifies the experiment.
All duration limits, command timeouts, readiness checks, and inhibitor/cleanup
budgets are unchanged.

**Treat this as a separate observation condition.** Reading the ACPI lid state
can evaluate firmware `_LID`; repeated reads may perturb firmware behavior. Without
this flag, there are no repeated ACPI lid/IRQ reads in the raw sampling loop.
Snapshots always read ACPI lid state once, along with button metadata and the full
ACPI IRQ counter set, so even an ordinary run includes those endpoint reads.
No ACPI methods are invoked through a write interface and no IRQ counters,
button parameters, or device settings are changed.

`raw-logind-samples.json` stores the optional `acpi_lid` and `acpi_interrupts`
fields. Each completed sample is also written immediately to the exclusively
created **`raw-logind-samples.jsonl`** and flushed before the next polling sleep.
This preserves a timestamped prefix if cancellation bypasses finalization; the
existing JSON array and reports remain unchanged. Flush is not an fsync or a
power-loss durability guarantee. An interrupted stream can end mid-line; use only
complete valid JSON lines, retain its incomplete status, and never assume the
physical closure occurred inside the recorded interval. Root-only results may
still require ownership recovery after an uncatchable termination.

`raw-lid-report.json.acpi_correlation` (also in `summary.json.raw_lid`)
and the text summary report:

- `closed_samples`: samples in which any ACPI lid reported closed;
  `closed_readings`: individual closed reports across all ACPI lids.
- Comparable ACPI/raw sample counts and disagreement counts, including direction
  (`acpi_closed_raw_open_samples` / `acpi_open_raw_closed_samples`). Missing,
  unreadable, or conflicting states are counted separately rather than treated
  as agreement or disagreement. Up to five disagreement examples retain clocks
  and SCI/GPE totals.
- First-to-last sampled SCI/GPE deltas, retaining reset/unavailable status.

ACPI-closed with raw-open directs attention toward notification/button-to-input
handling, but does not identify which component failed. SCI/GPE totals include
unrelated activity and do **not** prove a lid-specific notification occurred.
ACPI/raw/logind reads are sequential and non-atomic; transitional disagreement
or polling-induced behavior must not be mistaken for a proven persistent fault.

Absence of a closed report or raw cycle cannot establish that no physical action occurred.
In particular, a human-confirmed closure with all software states stuck open is
not a successful no-change control. ACPI observations do not upgrade the existing
raw-cycle status: absent a complete raw cycle, exit status remains 2. Likewise,
cleanup's `open_verified` checks the fresh **raw** reading, not physical geometry;
a stuck-false SW_LID cannot certify an open lid. Physically reopen before the
observation window ends.

### Imported-worker external-command deadline

An importing worker can set the module-level `COMMAND_DEADLINE` to an absolute
`time.clock_gettime(time.CLOCK_BOOTTIME)` value before calling `main(argv)`.
It defaults to `None`, and `main()` does not overwrite it. There is **no new CLI
option**; a worker reusing the imported module should set/reset this value per run.

Every `command()` call recomputes the remaining budget. With no time remaining,
it returns an explicit deadline error (`deadline_exceeded: true`) without starting
a subprocess. Otherwise its timeout is the smaller of the requested timeout and
the remaining budget. This applies to command-based metadata and final capture
as well as observation samples; cleanup still runs when that command budget ends.
Directly managed probe/inhibitor children retain their separate bounded lifetimes.

**This is not a hard deadline for all kernel activity.** Direct sysfs/debugfs reads,
input-device opens/ioctls, other filesystem operations, and stuck hardware/kernel
calls cannot be hard-time bounded by these Python deadlines. Subprocess creation
or reaping can also be delayed by kernel behavior. An external supervisor cannot
reliably interrupt uninterruptible kernel I/O either; do not promise a guaranteed
end-to-end wall-time bound or guaranteed protection after an inhibitor expires.

## Artifacts and interpretation

- `metadata.json`: kernel/OS/boot ID, BIOS/DMI, command line, effective logind
  configuration, inhibitor list, PCI/USB topology, input udev metadata, RTC state,
  recent kernel/logind history.
- `initial.json`, `before.json` (suspend modes), `after.json`: primary S0ix and
  secondary CPU/PC10 counters; CLOCK_BOOTTIME, wall and monotonic timestamps;
  battery charge/energy/capacity and power data; interrupts; ACPI wake config;
  wakeup_sources; relevant power state; bus-device driver bindings, sysfs ancestry,
  runtime PM controls/status/times; logind properties; raw lids; probe output;
  `/proc/bus/input/devices`. ACPI endpoint data is always included: `acpi_lid`
  holds discovered lid state files; `acpi_button` holds existing button module
  parameters and `PNP0C0D:*` lid-device ACPI `path`/identity exports and resolved
  sysfs locations; `acpi_interrupts` holds all discovered IRQ counter files plus
  parsed counts. Directory/read errors are retained instead of fabricated values.
  Snapshots (including baseline) also discover existing debugfs `pmc_core` and
  `intel_pmc_core*` trees, including `pmc_core/pmc0`, and read only `lpm_status`,
  `lpm_requirements`, `substate_residencies`, and `slp_s0_residency_usec` exports.
  The stable artifact key remains `intel_pmc_core.files` for either layout;
  unreadable files/directories are under `unavailable`. Missing/denied debugfs
  directory access is explicitly recorded, as is an accessible root with no PMC
  directory. There are no mounts, writes, permission changes, or reads of
  fabricated fallback file paths.
- `pre_suspend_raw_lid.json`: fresh, timestamped safety state after RTC arming.
- `kernel-journal.json`, `logind-journal.json`: cursor-scoped experiment journal
  output, including stderr/return codes. The suspend verifier uses a fresh cursor
  immediately before arming, separate from the broader run capture.
- `summary.json`, `summary.txt`: status, elapsed wall/boottime/monotonic time,
  S0ix/PC10 deltas and residency percentages, signed battery deltas, interrupt
  deltas and runtime PM differences. Negative battery deltas mean consumption;
  charge is uAh and energy is uWh. `comparison.acpi_interrupts` contains full
  before/after IRQ deltas: the leading decimal count is parsed from each export,
  increases produce nonnegative deltas, decreasing counters are `reset_or_wrap`,
  and missing/unreadable/negative/unparseable values yield `unavailable` with a
  null delta. No reset width is guessed. JSON includes raw journal evidence when
  verified.
- Raw observation additionally creates `raw-lid.jsonl`, probe stdout/stderr and
  invocation details, and `raw-logind-samples.json` with repeated raw lid and logind
  LidClosed comparisons during the observation. `raw-lid-report.json` provides
  interpretation, also included under `raw_lid` in `summary.json` and as compact
  text in `summary.txt`.

`lid_probe.py` is invoked without arguments for snapshot JSON. Observation invokes
`lid_probe.py --duration SECONDS --output FILE --ready-file PRIVATE_PATH`. Its
stdout is preserved verbatim. Interpretation consumes the agreed JSONL `initial`,
`event`, `state_change`, `resync`, `final`, `error`, and `syn_dropped` records.
Raw observation requires the helper to exist; baseline records its absence as an
ordinary capture error.

The raw report counts close/open transitions and tablet toggles, deduplicating
matching event and ioctl-state records. It tracks devices and sources separately;
transitions on different lids or across dropped-event/readability gaps are not
joined to invent a cycle. Independent sampled raw state can also establish a
cycle. The status is:

- `raw_cycle_observed`: at least one device has an observed **open → closed → open**
  cycle in either the probe stream or sampled raw readings. This means a cycle
  was recorded, **not** that logind or tablet behavior passed a correctness test.
- `incomplete_lid_cycle`: transitions occurred, but no full cycle was established
  (initially closed then opened does not count as a complete cycle).
- `no_lid_cycle_observed`: readable lid state, but no transition. This does not
  prove the user did not move the lid; a stuck raw switch is another possibility.
- `raw_data_unavailable`: no interpretable raw lid state.

The report counts comparable samples with raw/logind disagreements and includes
up to five examples. Unreadable, missing, or mutually conflicting raw lids and
unavailable logind properties are counted separately, never as mismatches.
Sampling is not atomic: a mismatch can reflect transition timing or logind lag;
one sample does not establish a persistent fault. Probe errors/dropped events and
malformed record counts are retained as evidence-quality warnings.

S0ix uses
`/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us` as the primary
metric. The secondary CPU low-power counter is
`low_power_idle_cpu_residency_us`; unavailable exports are not replaced with
fabricated values. A falling counter is `reset_or_wrap`, with no invented delta
or percentage. Rates over 105% are flagged `implausible`, not clamped to 100%.
Denominators use **capture-to-capture CLOCK_BOOTTIME**, including awake collection
and setup, not an assumed test duration or an NTP-adjustable wall interval.

`verified_entry_exit` means the kernel journal contains an ordered s2idle entry
and exit pair after the verification cursor. It does **not** mean 600 seconds of
sleep, successful deep residency, or proof that a particular lid event caused
entry. Aborted/brief suspend attempts can also have such a pair; inspect counters
and elapsed times. Lack of journal access/evidence yields `unverified` even when
elapsed time suggests sleep. Exit status is 0 for `captured`, `raw_cycle_observed`,
or `verified_entry_exit`, and 2 for refusal, failure, unverified suspend, or raw
observation without an established complete cycle. An empty physical test can no
longer report a generic successful `observed` status.

## Limits and physical safety

- RTC wake is a best-effort escape, not a hard guarantee against firmware failure,
  battery loss, broken hardware or blocked kernel calls. The harness cannot run
  a watchdog while the machine is asleep. The human must be able to reopen/wake it.
- Reopen after RTC wake. Normal lid policy remains active in B; it can re-suspend
  the machine if the lid stays closed. C restores ordinary lid handling after
  capturing the after-state. Raw observation also restores normal policy at its
  bounded deadline; reopen within the observation window.
- There is no atomic transaction between the final raw ioctl and systemd suspend;
  do not move the lid after the final check. Other processes must not manipulate
  the RTC or initiate unrelated suspends during an experiment. Alarm ownership
  checks reduce interference but cannot eliminate concurrent-writer races.
- No existing inhibitors are overridden. Display-server/desktop policy, external
  monitors, docking and other inhibitors may prevent normal lid entry; inspect
  captured state. Missing journal access is not treated as success.
- Killing the process with SIGKILL, machine crashes, or losing power can bypass
  Python cleanup. Pipe closure/lifetime limits release runtime inhibitors, but an
  already armed RTC alarm may remain until it fires. Interrupting an already
  queued systemd sleep request cannot reliably cancel that system job.
- Missing tools, debugfs permissions, and unavailable sysfs exports are recorded,
  not repaired by mounting debugfs, loading modules, or changing permissions.
- These tests do not alter storage, firmware, modules, device wake settings,
  runtime PM controls, GNOME/logind configuration, or hibernation policy.
