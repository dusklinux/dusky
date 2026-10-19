# Runner lifecycle audit — 2026-09-30

## Current audit status — 2026-10-19

**No full Green Light.** Non-graphical runner verification passed, but the
NVIDIA gamescope probes coincided with two Hyprland crashes. All graphics
testing stopped when the user reported them. No compositor settings, GPU
driver packages, or installed game prefixes were changed. No test workloads
or `master-runner-*.scope` units remained after the tests.

Evidence: `${XDG_STATE_HOME:-$HOME/.local/state}/master-runner/audits/2026-10-19/`.
The older audit below describes its own historical environment and results.

### Phase 1: structure and logic

Read the entire runner and reviewed directly related configuration, tests,
the I/O shim and relevant Lutris source. The Lutris comparison covered
`monitored_command.py`, its wrapper and process watcher, Wine prefix and
architecture detection, DLL managers, Proton/UMU environment construction,
GPU/ICD detection, XDG settings and runtime discovery. Translations, assets,
unrelated services and emulator implementations were outside this comparison.

Changes:

- Validate section shapes before accessing their nested fields, and reject
  incorrectly typed argument/hook/provisioning arrays and negative dimensions,
  audio parameters, workers and cache sizes.
- Parse CLI arrays with TOML rather than stripping quotes and splitting commas.
  Strings such as `"true"`, `"12"` and `"a,b"` retain their types and contents.
- Prepare replacement DLL links before moving originals, preserve originals
  when publication fails, persist completed links before attempting another
  component, and restore originals even if a broken managed link was removed
  already. Missing requested translation runtimes now produce an explicit error.
- Use existing registry architecture to reject conflicting Wine prefix settings,
  following Lutris's architecture-aware approach. UMU/Proton require win64;
  provisioning arrays that this path previously ignored now report that they
  require separate provisioning instead of silently succeeding.
- Keep UMU's shared runtime path independent of game-specific HOME/data paths,
  and propagate the host locale. Proton still owns its prefix and translators.
- Make Winetricks use the selected Wine and wineserver, with one documented
  unattended option instead of duplicate aliases.
- Stop forcing Wine to X11 under gamescope. Native Wayland backends are used
  consistently, and gamescope exposes native Wayland clients. Existing profiles
  requesting X11 presentation are reported as unavailable; no fallback was added.
- Wait directly on pidfd/signal events while the original leader is running
  once its scope is known. Descendant observation and scope discovery retain
  their necessary polling. Large affinity ranges are bounded by usable CPUs.
- Apply a total D-Bus reply deadline, including fragmented reads; close received
  descriptors on failure or when no caller claims them. Notification helpers
  are now bounded and reaped. Empty commands return an error result, supplied
  child PATH is honored, and zero MemAvailable no longer becomes half of RAM.

### Phase 2: modern interfaces

The actual shebang now selects Python 3.15. Adopted explicit lazy stdlib imports,
immutable `frozendict` tables, a modern type alias, `re.prefixmatch`, consuming
private bytearrays with `take_bytes`, and explicit UTF-8 for defined text formats.
Optional Rich imports retain their existing local ImportError handling.

Checked installed manuals/help for DwarFS, mkdwarfs, fuse-overlayfs, fusermount3,
umount, stat, systemd-run, systemctl, bubblewrap, taskset, Bash, GCC, notify-send,
fzf, MangoHud, GameMode, desktop-database updating, sudo, sysctl and modprobe.
Gamescope and vulkaninfo lack installed manual pages; their installed help was
used. MangoHud/GameMode wrappers execute their arguments rather than interpreting
`--help`, so their installed manuals supply their interface documentation.
Wine, wineserver, UMU and Winetricks are absent: their relevant interfaces were
checked against upstream source/docs, without claiming installed-runtime tests.

Observed: Python 3.15.0rc3, kernel 7.3.0-rc7, systemd 262.1, Hyprland
19fb395d4, DwarFS 0.15.8, FUSE 3.18.3, fuse-overlayfs 1.18, bubblewrap 0.13.1,
gamescope 3.16.31, util-linux 2.42.4, Bash 5.3.20, fzf 0.74.5. The final ISO
package manifest is unavailable; these are development-system observations.

References: [Python 3.15](https://docs.python.org/3.15/whatsnew/3.15.html),
[D-Bus protocol](https://dbus.freedesktop.org/doc/dbus-specification.html),
[UMU manual](https://github.com/Open-Wine-Components/umu-launcher/blob/main/docs/umu.1.scd),
[Winetricks options and Wine selection](https://github.com/Winetricks/winetricks/blob/master/src/winetricks).

### Phase 3: empirical results and unresolved failure

- Initial new regression group: 2/19 tests passed against the original script;
  all 19 pass after fixes. Additional regressions were added during review.
- Final non-graphical suite: **43 passed**, 22 opt-in integration cases skipped.
  Includes 25 audit tests for configuration, protocols, FD ownership, DLL failure
  recovery, architecture, PATH, Winetricks, affinity and event-driven supervision.
- Real FUSE/systemd lifecycle run: **62 passed**, one live Wine case skipped
  (63 tests before the final PATH and Winetricks regression additions).
  Includes mount rollback/recovery, busy stacks, persistence, detached children,
  all four fatal signals, stubborn/stopped processes, concurrent launches,
  sandbox cleanup, unusual paths and 20 repeated complete launches.
- Additional stress: **100/100** full disposable DwarFS/overlay/systemd launch
  cycles completed with verified unmounts (109.106 seconds).
- Shared reliability suite: **24 passed**, including 21 runner checks and
  three retained installer checks. Obsolete X11 compatibility tests were removed.
- Twelve CLI scenarios passed, including scaffold/init, JSON list/status/
  validation/environment/dry-run, desktop-file validation, native/script launch,
  and malformed configuration errors. UTF-8 warning checks found no runner warnings.
- All 50 existing profiles parse without configuration errors. Availability:
  13 valid, 37 unavailable due to current missing assets/runtimes or unsupported
  presentation choices. Doctor returned 28 OK and 16 informational checks;
  it is not a substitute for live graphics testing.
- Native Wayland Vulkan cube rendering returned 0 on both Intel and NVIDIA.
  Intel gamescope rendering also returned 0. These successful probes do not
  establish session stability: see the crashes below.
- **NVIDIA gamescope failed twice**, returning 134 with `IWaitable hung up`.
  Disabling the gamescope WSI layer did not resolve it. Hyprland crashed at
  14:26:31 and 14:27:14 local time, coinciding with those probes. Both crash
  reports show Mesa's `dri_create_fence_fd` and Hyprland's
  `Render::GL::CEGLSync::create()` in their abort stack. This implicates the
  graphics synchronization path; it does not establish the root cause or prove
  that a runner change fixes it. Crash reports and graphics logs are preserved.
  No further live graphics probes were run after the user reported the crashes.
- Compilation and scoped Git whitespace/diff review passed. No commits/staging
  were performed. Live Wine/UMU, AMD hardware, HDR, every game, and arbitrary
  future package versions remain unverified.

Scores are observed regression pass percentages, not whole-program ratings:

| Area / measured probe | Before | After | Result |
|---|---:|---:|---|
| Reliability: initial 19 regression tests | 11 | 100 | Better; 2/19 → 19/19 |
| Efficiency: event-driven leader wait | 0 | 100 | Better; blocking-wait regression passes |
| Gameplay performance / graphics stability | N/A | N/A | No FPS claim; graphics failure unresolved |

Paired warm-process startup measurements (20 samples per version on the same
interpreter) observed median `--version` 239.2 → 226.7 ms and an empty JSON
catalogue 221.0 → 212.2 ms. These runs overlapped other audit work, so the small
differences are not treated as a controlled performance result.

For non-graphical regression checks:

```sh
python3.15 -X dev -m unittest discover -s tests -v
```

Do not repeat the NVIDIA gamescope probes on the active desktop while the
compositor crash remains unresolved. The full Green Light is withheld.

### Follow-up non-graphical recheck

Revisited Lutris's DLL architecture/restoration, Proton ownership, UMU path and
locale handling, and process watcher. Retained the runner's owned-cgroup model;
Lutris's process-name exclusions and automatic downloads do not fit this runner's
supervision and offline setup goals.

Found and reproduced an additional provisioning-cache error: the stamp sorted
redistributable and Winetricks lists, so reordering either list incorrectly
skipped provisioning. The fingerprint now preserves execution order. Both
failure cases now pass; unchanged lists still reuse the stamp. Existing profiles
with unsorted lists may provision once again because their old stamp discarded
order information.

Recheck results: **44 passed**, 22 opt-in integration cases skipped (66 tests),
and **24/24** shared reliability checks passed. Compilation and Git whitespace
checks passed. No live graphics tests were repeated. The earlier 100 successful
launch cycles remain historical evidence; compositor stability and live Wine/UMU
remain unverified or unresolved.

| Added regression group | Before | After | Result |
|---|---:|---:|---|
| Provisioning order: two failure cases | 0 | 100 | Better; 0/2 → 2/2 |
| Performance | N/A | N/A | No new performance measurement |

### Compositor crash investigation and mitigation

Inspected the saved Hyprland core with GDB and downloaded the matching official
[Mesa 26.2.5 source archive](https://archive.mesa3d.org/mesa-26.2.5.tar.xz).
This investigation did not launch any additional graphics workloads, restart the
compositor, change compositor configuration, or replace graphics packages.

Confirmed immediate failure in the second core:

1. Hyprland's `CEGLSync::create()` called EGL native-fence creation.
2. Mesa reached `iris_fence_flush()` and `_iris_batch_flush()`; the return address
   identifies `iris_fence.c:247` in the matching archive.
3. The batch submission return value, retained in `r13d`, is **-12 (-ENOMEM)**.
   Disassembly confirms that register receives `batch_submit()`'s result and
   carries it to the fatal branch. The i915 backend converts an unsuccessful
   `i915_gem_execbuf_ioctl()` into `-errno`.
4. Mesa's fatal negative-result branch calls `abort()`. Hyprland's own crash
   handler then runs, explaining the two abort frames in its backtrace.

Both compositor crash reports share this Intel Mesa submission/fence path.
Only the second core's errno was decoded; do not assume the first had the same
errno. The journal contains no OOM-killer or GPU-reset report in this interval.
That absence does not prove memory availability, and ENOMEM does not establish
which GPU, kernel or imported-buffer allocation failed.

The triggering launch configuration rendered gamescope on NVIDIA and presented
to the Intel-hosted desktop. Its Wayland socket closes when Hyprland dies.
Gamescope's `IWaitable::OnPollHangUp()` calls abort on EPOLLHUP, so the later
`IWaitable hung up` log is consistent with compositor loss, not proof of the
original failure. The two `vulkaninfo` segfaults logged just after the crashes
are additional Wayland-client failures; they do not establish the cause of
Hyprland's abort. Disabling gamescope WSI did not prevent the second crash.

The runner previously forced a discrete GPU even for automatic gamescope
selection. Automatic gamescope launches now prefer the primary GPU, with one
shared selection policy used by GPU environment construction, gamescope's
`--prefer-vk-device`, and config patches. On this machine that selects Intel,
matching the earlier successful Intel gamescope probe. **Both gamescope and its
game use this GPU.** Direct automatic launches still prefer a known discrete
GPU. Explicit `--gpu nvidia`, `--gpu discrete` and PCI selectors are preserved.
Primary uses the existing boot-display heuristic; it is not a newly verified
probe of every compositor's rendering device.

This is a mitigation of the reproduced launch configuration, **not a repair of
Mesa/i915 or a verified guarantee against compositor crashes**. Explicit NVIDIA
gamescope remains affected on this host; use `--no-gamescope` for that GPU until
the graphics failure is fixed and verified in an isolated session. No automatic
retry or silent fallback after a graphics crash was introduced. The automatic
policy can reduce game performance on hybrid systems compared with the dGPU.

Verification: a new automatic-gamescope selection regression failed before the
change and passed after it. Additional tests preserve explicit GPU choices and
the direct-launch default. **47 tests passed**, 22 opt-in integration cases
skipped (69 total), plus **24/24** shared reliability checks. Compilation and
whitespace checks passed. These are fixture/protocol checks; no new live
graphics A/B test was attempted on the user's active desktop.

GDB register/disassembly proof, matching source excerpts, kernel log and test
logs are saved in the `crash-investigation` subdirectory of the evidence path
above. The original compressed core remains managed by systemd-coredump.

| New change / measured probe | Before | After | Result |
|---|---:|---:|---|
| Automatic gamescope primary-GPU regression | 0 | 100 | Better; reproduced policy failure corrected |
| Compositor stability / game performance | N/A | N/A | Live mitigation unverified; driver failure unresolved |

The runner now removes reused and newly created game mount stacks after the
owned workload ends when `runner.auto_unmount_on_exit = true`. This applies
with `--no-mount` and `auto_mount = false` as well: those control setup, while
`--keep-mounted` or `auto_unmount_on_exit = false` controls retention.
Persistent upper-layer files and saves remain on disk.

## Confirmed faults and fixes

- A pre-mounted stack survived a successful game exit. A real fixture reproduced
  this before editing; automatic teardown now includes reused mounts.
- Mount helpers could publish a layer before failing or being interrupted, before
  the session registered cleanup. Mount setup now rolls back layers created by
  the attempt, and sessions register teardown before setup.
- Scope discovery began after launcher exit, potentially missing descendants.
  The supervisor observes the scope during launch and resolves its named unit
  if the leader has already disappeared. Scope names use a profile-path hash,
  allowing arbitrary profile names without invalid systemd unit characters.
- SIGTERM/HUP/QUIT during startup bypassed Python teardown. Session signal
  handlers now unwind startup; spawning defers interruption until the process
  cleanup callback exists. Repeated signals cannot interrupt teardown.
- Failed unmounts were ignored. Kernel mountinfo now verifies removal after
  each helper. A busy union keeps its lower mounted and returns cleanup failure
  (74 for an otherwise successful game), including an error in quiet mode.
- Mount locks covered only the union path. Launch, mount, unmount and sweep now
  lock lower, union, upper and work resources in a consistent order.
- A retained lower skipped cleaning the overlay work directory. Partial-stack
  recovery cleans the scratch directory before creating a new union.
- Lower-layer recovery could leave a union attached to the old lower. Recovery
  now removes the union first and checks detach results.
- Colon/backslash directory names broke fuse-overlayfs option parsing. Relative
  temporary symlinks avoid its parser, and canonical mount paths make symlinked
  game directories observable through mountinfo.
- fuse-overlayfs 1.18 ignores the supplied `auto_unmount` and `clone_fd` options.
  They were removed; upstream already initializes clone_fd. DwarFS supports
  these options and retains them.
- systemd expanded literal dollar tokens; CLI forwarding removed literal `--`.
  `--expand-environment=no` and unchanged forwarding preserve game arguments.
- Wine shutdown sent `-k` without waiting. It now follows with `-w`; the wait
  result is authoritative because `-k` returns 1 when no server is running.
- Environment setup registers Wine cleanup before prefix provisioning, covering
  interruptions while building the environment. Mount probes use the shared
  bounded subprocess helper rather than leaking a probe on interruption.
- Nonfinite timeouts and overlapping storage directories are rejected. Zero
  kill grace is honored. Log-lock timeouts cannot mask a completed launch.
- Removed KWin/wlr and old Hyprland socket discovery branches. Sandbox sockets
  use XDG runtime paths. Mesa chooses its hardware driver, and the runner no
  longer invents the NVIDIA provider name `NVIDIA-G0`.

## Verification

Installed environment: kernel `7.3.0-rc4-dusky-battery`, Python 3.14.7,
systemd 262, Hyprland 0.56.2, Bash 5.3.20, fzf 0.74.4, DwarFS 0.15.7,
fuse3 3.18.3, fuse-overlayfs 1.18, Wine-Staging 11.18, gamescope 3.16.31,
bubblewrap 0.13.0, util-linux 2.42.4, coreutils 9.12.
These are development-system observations; the final ISO manifest is not
available and must still be checked before shipping.

- Full lifecycle suite: **29 tests passed in 158.684 seconds**, including 20
  repeated real FUSE/systemd launches, normal/error exits, missing executables,
  pre-mounted stacks, SIGINT/TERM/HUP/QUIT, ignored TERM escalation, startup
  interruption, detached descendants, concurrent launches, scratch cleanup,
  persistent saves, daemon-crash recovery, helper/readiness failures, sandbox
  cleanup, and unusual/symlinked directory names.
- Final unit run: **12 passed**, with 18 opt-in integration tests skipped.
  This includes the subsequently added repeated-signal teardown test.
  The startup-hook integration test was rerun after final callback changes.
- **43/43 installed DwarFS profiles** mounted, resolved a readable executable,
  and unmounted. This inspected game assets without running all 43 games.
- Actual window-close tests: The Roottrees are Dead (native Wayland), Mesmalie
  (Wine Wayland), and Roottrees inside gamescope each exited **0**, with both
  mount layers absent afterward. Idle inhibition worked in their logs.
- A disposable Wine `cmd /c start /b ...` fixture completed its delayed child
  before session teardown and returned 0.
- The original runner changed `$HOME` and `${USER}` and discarded literal `--`.
  The edited runner preserved all five supplied argument tokens exactly.
- Doctor returned no FAIL/WARN checks on this development system.
- All 50 profiles parsed without configuration errors. Eight were unavailable:
  `fatal_claw`, `portal_collection`, and `portal_collection_install` have missing
  game directories; `blasphemous`, `bloodthief`, `dicey_dungeons`, `portal`,
  `portal_2`, and `portal_collection` request host Xwayland, which is absent.
  The existing profile selections were preserved; those selections need
  revision for a strictly Wayland-only ISO.
- Python compilation and Git whitespace checks passed. Final mountinfo and
  systemd checks found no fixture mounts or `master-runner-*.scope` workloads.

Run the tests from this directory:

```sh
python3.14 -m unittest discover -s tests -v
RUNNER_INTEGRATION=1 python3.14 -m unittest discover -s tests -v
```

Evidence is retained under
`${XDG_STATE_HOME:-$HOME/.local/state}/master-runner/audits/2026-09-30/`.
The real game test initialized Mesmalie's previously uninitialized Wine prefix
and wrote normal runtime/game configuration; it did not delete game saves.

## Scope and limits

The measured improvements concern lifecycle correctness, not frame rate.
No FPS or memory-performance gain is claimed. This is not proof that every
engine, installer, GPU, HDR setup, or future package release works. UMU is not
installed here, and its runtime was not exercised. The tests ran on the
available Intel development system, not separate AMD/NVIDIA hardware.

SIGKILL, power loss, and kernel failure cannot execute Python cleanup.
A later mount attempt recovers stale endpoints; `unmount`/`unmount-all` can
remove retained stacks. Busy mounts are reported and retained until their
users release them. With `runner.use_systemd_scope = false`, a descendant
that deliberately leaves the process group is outside group supervision;
keep the default scope enabled for daemonizing workloads.

## Before/after scores

Scores below are observed pass percentages for the named probes, not an
overall quality rating. N/A means no defensible numerical measurement.

| Area / probe | Before | After | Evidence | Result |
|---|---:|---:|---|---|
| Reused-mount automatic cleanup | 0 | 100 | One real premounted-exit reproduction | Better |
| Path handling: space/comma/colon/backslash | 50 | 100 | Two of four versus four of four real mounts | Better |
| Literal game tokens preserved | 40 | 100 | Two of five versus five of five supplied tokens | Better |
| FPS / performance | N/A | N/A | No gameplay benchmark | Unverified |

## Interface references

Used installed help/man pages and exercised affected flags against the versions
above. The corresponding upstream sources explain the parser and expansion:

- [systemd-run arguments and environment expansion](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd-run.xml)
- [fuse-overlayfs 1.18 parser and initialization](https://raw.githubusercontent.com/containers/fuse-overlayfs/v1.18/main.c)
- [fuse-overlayfs mount interface](https://github.com/containers/fuse-overlayfs/blob/main/fuse-overlayfs.1.md)
- [Mesa driver selection environment](https://docs.mesa3d.org/envvars.html)
- [Hyprland window-close dispatcher](https://wiki.hypr.land/Configuring/Basics/Dispatchers/)

## Second pass and Lutris comparison

Reviewed the committed runner (`ff25bce2`) against the downloaded Lutris 0.5.23
source in `/mnt/zram1/lutris-master`. The comparison covered its game launch,
stop and post-exit paths, monitored-command wrapper, subreaper/process watcher,
Wine/UMU command construction, prefix handling, DLL installation/restoration,
runtime library paths, XDG settings and Steam discovery. Unrelated service,
GUI, emulator and translation code was outside this launcher comparison.

Useful ideas adopted from Lutris:

- Architecture-aware translator destinations: a win32 prefix needs x32 DLLs in
  `system32`; win64 uses x64 there and x32 in `syswow64`. The old runner always
  selected x64 for `system32`. A disposable win32 fixture reproduced this.
  DXVK, VKD3D and NVAPI now share the correct destination mapping. DLSS no
  longer falls back to putting a 32-bit library in 64-bit `system32` when
  `syswow64` is absent. DLL disabling/restoration is tested too.
- Lutris's data directory follows XDG. Translator discovery and recognition
  now honor `XDG_DATA_HOME/lutris/runtime`. Steam's existing installation
  path is retained; its directory layout is independently defined by Steam.

Other reproduced faults corrected:

- A direct child killed by SIGTERM returned Python's -15, which would become
  shell status 241. The session now records/returns 143, consistent with scope
  launches and runner interruption. See the documented
  [Python return-code convention](https://docs.python.org/3.14/library/subprocess.html#subprocess.Popen.returncode).
- `--reprovision` mutated the shared profile even during a dry-run. It now
  creates a session-specific profile, so subsequent launches retain their
  configured provisioning policy.
- Wine inspected a nested `prefix/pfx` while launching and shutting down at
  `prefix`. Environment construction, shutdown and the session lock now use
  the actual Wine prefix. UMU's environment contract is preserved.
- An interruption after the game succeeded could leave only the successful
  game record. The interrupted phase now gets its own failure record; config
  patch errors use the same exception path without duplicate records.

The runner retains cgroup supervision. Lutris's watcher excludes processes by
name and relies on a subreaper, while its client also searches process command
lines and UUID environments. Those heuristics are unnecessary for the runner's
owned systemd scope and could miss legitimate workloads. The delayed Windows
child fixture verifies that the runner waits for the workload after its first
launcher exits. No Lutris runtime downloads or legacy platform branches were
introduced.

Second-pass verification:

- **40 tests passed in 214.523 seconds**, with both FUSE/scope and Wine
  integration enabled. This includes 20 repeated launches, the prior failure
  and cleanup cases, stopped-process shutdown, post-launch interruption,
  normalized direct-child status, win32/win64 DLL wiring/restoration, custom
  XDG discovery and a real nested Wine prefix with a delayed Windows child.
- All six new focused regression tests failed against the committed baseline;
  all six pass against the edited runner. The baseline run used a disposable
  copy and did not modify the committed source or installed prefixes.
- **43/43 installed DwarFS profiles** again mounted, exposed a readable game
  executable and unmounted. The three real native Wayland/Wine Wayland/
  gamescope window-close checks again returned 0 with both layers gone.
- All 50 profiles still parse; the same eight remain unavailable. Doctor's
  39 checks contain no FAIL/WARN results. Installed package versions remain
  those recorded above; relevant installed command help was rechecked.
- Compilation, scoped Git whitespace/diff review and final mount/scope
  inspection passed. The limitations in the first-pass report still apply;
  DLL placement fixtures do not prove NVIDIA execution or a win32 Wine build.

Run every test from this directory:

```sh
RUNNER_INTEGRATION=1 RUNNER_WINE_INTEGRATION=1 python3.14 -m unittest discover -s tests -v
```

Evidence is in `audits/2026-09-30/second-pass/` under the state directory
documented above. Scores are pass percentages for the named regression groups,
not an overall quality score or a guarantee about all games.

| Area / probe group | Before | After | Evidence | Result |
|---|---:|---:|---|---|
| Session correctness | 0 | 100 | Four reproduced regressions: 0/4 versus 4/4 passing | Better |
| DLL wiring and XDG discovery | 0 | 100 | Two regression tests: 0/2 versus 2/2 passing | Better |
| Performance / efficiency | N/A | N/A | No frame-rate or resource-use benchmark | Unverified |
