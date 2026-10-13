# Dusky Center startup and runtime review

Reviewed 2026-10-13. Dusky Tray was a read-only reference. Changes are confined
to Center; no service, desktop policy or configuration action was replaced.
The updated Center was subsequently installed through its existing setup script. This is a review of the Rust application and its launch/build path,
not a security audit or a claim that every externally configured program was
executed.

## Architecture and decisions

| Path | Finding and decision |
| --- | --- |
| CLI → instance lock → configuration | Keep the existing on-demand lifecycle and normal Wayland toplevel. Closing drains pending setting/service writes; the app does not remain resident. Keep configuration search order and semantic validation. |
| Graphics initialization | Center had a Vulkan-first/EGL compositor but its `iced::Element` aliases selected Iced's default renderer. Explicit renderer aliases now connect that existing compositor to the application. Keep drawing, fonts, icons, colors, window rules, and geometry. |
| GPU discovery | Port Tray's runtime DRM discovery and matching driver selection before executor threads start. Respect explicit adapter/offload/loader overrides. Include both Intel ANV and HASVK candidates, distinguish Nouveau from proprietary NVIDIA, and recognize AMD/Virtio. Unknown vendors and unrecognized ICD filenames keep normal discovery. A valid first `AQ_DRM_DEVICES` entry identifies the compositor hint; without it, only a single-card system is filtered. Ambiguous multi-GPU systems keep normal discovery. Explicit `WGPU_POWER_PREF` and `WGPU_ADAPTER_NAME` bypass filtering. Iced 0.14 reads the power preference but its compositor does not consume the adapter-name variable; no name-based selection guarantee is claimed. Automatic GPU variables are removed from child commands and application launchers, preserving the session environment. Missing standard ICD filenames do not force EGL. |
| Executors | Four bounded blocking workers handle queries and finite writes; two Iced workers handle streams/timers. Initial read-only queries start before graphics initialization. Writes remain deferred until their tasks run. A discarded queued job is skipped; running commands retain their deadline and existing parent-death cleanup. |
| Hardware reads | Audio output, microphone, Night Light and brightness return independently. Each visible control permits one outstanding read. DDC no longer delays publication of unrelated audio results or suppresses later audio refreshes. Hidden controls schedule no new hardware work; entering a page/subpage/expander or reloading refreshes visible hardware immediately. Core replies use per-control edit timestamps and previous-value checks, retaining grace-period and pending-write protections. An unrelated slider edit no longer discards a valid core reply; a same-value edit still invalidates an older reply. Generic control snapshots retain their conservative global slider-revision guard. Visibility means the current page/subpage and expanded items, not viewport-based culling; Night Light availability is still queried while its service is inactive. |
| Generic TOML controls | Preserve page-local polling, command intervals, one-shot reads, collapsed-expander suppression, scope-batched/deduplicated service queries, unavailable states, rollback and persistence after successful actions. These already address the relevant Tray concerns. A generic page snapshot still runs sequentially; its individual commands have deadlines, but a page with many slow commands can take substantially longer than one command. It occupies a blocking worker rather than a timer worker. |
| Slider writes and shutdown | Preserve one active write plus one latest pending value per slider, 100 ms explicit debounce, keyboard/wheel commits, fractional values, flush on navigation/reload/close, and waiting for bounded writes on close. Tray's immediate-exit policy would lose Center's setting edits and was not ported. |
| Process waits | Port pollable process descriptors in place of 10 ms periodic polling. Continue draining both nonblocking pipes, enforce timeouts on the process group, wait for descendants holding inherited pipes, and stop polling the exit descriptor once exit is observed. Keep the leader unreaped until inherited pipes close, protecting its numeric process-group ID during cleanup. A spawn/shutdown registry kills still-active finite-command groups when the application runner returns; explicitly detached launchers remain independent. Deadline overflow is rejected before spawning. Round poll timeouts upward and retain deadlines through interrupted waits. Retain Center's shell helpers and directory fsync after persistent writes. |
| Runtime paths | Verify tmpfs before creating directories, ignore empty or relative XDG runtime values, and remember the selected directory for the invocation. Keep the shared `dusky-tray` cache namespace and Center's separate lock filename so existing cache/lock relationships survive. With no usable tmpfs candidate, startup reports an error instead of using an unchecked disk fallback. |
| Backlights | Reuse one discovered backlight list when selecting a device; preserve ranking and preference for writable devices. Keep existing sysfs/logind/brightnessctl reads and DDC scaling to actual VCP maxima. |
| Metrics | Remove unused CPU/RAM fields and their startup/three-second polling. These values had no view call site but caused `/proc` reads and a CPU sample write. The visible TOML memory/kernel labels remain independently queried. |
| Themes, icons, generators and search | Keep complete palette parsing and retaining the last valid palette, icon-handle caching (including failed system-icon discovery), existing generator ordering/substitution, nested search routing and lazy page defaults. These differ from Tray's fixed layout; replacing them would change Center's product behavior without a demonstrated benefit. |
| Installation and offline builds | Keep the existing installer/package selection, optimized release profile and source fingerprints. Both installer and ISO recipe include all `src` files in their digest, including the new GPU and worker modules. No new dependencies or lockfile changes were needed. |

## Transfers deliberately excluded

Tray's compact-window sizing/positioning, focus-grab click-away helper, panel
opacity, notification grouping/timestamps, clock/weather/network presentation,
SVG glow masks and animation handling belong to its panel UI. Center does not
use those paths. The copied notification/status/appearance modules contain
unused Tray functionality; their presence does not make it a Center hot path.
In particular, Tray's recent network-sampling fixes were not imported into an
unused Center network function.

The GTK `lib/rows.py`, service/utility libraries and CSS are legacy material,
not dependencies of the Rust window. Configured editor/radio/WireGuard actions
still refer to the existing `control_center` helper directory. Changing those
external integrations or modernizing the legacy Python implementation was not
necessary for these Rust startup improvements.

## Final audit and empirical verification

The follow-up audit reviewed every changed file against the saved original and
previous implementation, and relevant call sites against the installed APIs.
It found four concrete defects/limitations in the earlier work: unrelated edits
invalidated independent core reads, firmware order could choose the wrong GPU,
automatic GPU tuning leaked into children, and direct-child parent-death signals
did not clean up grandchildren on normal application exit. Regression fixtures
and isolated process probes demonstrated these behaviors before correction.

| Changed file | Final review and verification |
| --- | --- |
| `src/main.rs` | GPU setup precedes threads; two stream workers and four blocking workers; eager boot; runner return invokes finite-query shutdown, including graphics errors. Live start/close/error paths exercised. |
| `src/ui.rs` | All blocking task conversions, visibility/navigation/reload call sites, stale replies, latest-write coalescing, pending metadata, failure persistence, shutdown draining and removal of unused metrics checked. Core reply payloads no longer allocate a singleton vector/string; generic polling excludes duplicate native core sliders. |
| `src/renderer.rs` | Compositor selection and every forwarding method reviewed. Pixel-exact wrapper/native comparison covers text, SVG, alpha, borders, clipping and transforms at three scales. Explicit backend semantics and automatic fallback exercised live. |
| `src/icons.rs` | Renderer alias checked through UI builds and full-page GPU rendering. |
| `src/gpu.rs` | AQ order, single-card selection, ambiguous cards, vendor/driver variants and explicit override behavior verified. Child environments checked in isolated processes; no mutation after executor startup. |
| `src/backend/worker.rs` | Four-worker bound, canceled queued work, eager/deferred submission and independent timers tested. |
| `src/backend/cmd.rs` | pidfd ownership, pipe draining, exit/timeout/error cleanup, registry lock ordering, process-group lifecycle and directory fsync reviewed. Signal interruptions, descriptor exhaustion and concurrent shutdown tested. |
| `src/backend/controls.rs` | One added call restores the session environment for structured application launches. The actual systemd scope launcher was exercised after parent shutdown. |
| `src/backend/system.rs` | Actual tmpfs checks before mkdir, absolute-path selection, stable caching and one-scan backlight ranking checked. Invalid paths and nested-directory fixtures tested. |
| `src/backend/mod.rs` | Worker module registration checked by debug/release builds and Clippy. |
| `README.md`, this report | Runtime behavior, measurements, exclusions and limitations corrected to match the final code. |

- Original baseline: **57 passed, one live-systemd test ignored**; Clippy passed.
- Final complete suite: **79 passed, zero failed, zero ignored**, including the
  isolated live systemd enable/start/disable/stop fixture and both GPU tests.
- All **18 pages at 1.0, 1.6 and 2.0 scale** render separately under forced Vulkan
  and Wayland EGL. Both backends pass the exact wrapper/native pixel comparison.
  This validates rendered buffers, not every physical pointer interaction.
- **20 repeated ordinary-suite cycles: 1,520 passing test executions** in 22.01 s.
  The three environment-dependent tests are covered separately above. Across
  those cycles, stress fixtures submit **327,680 slider changes**, drop
  **200,000 canceled queued jobs**, and exercise **6,000 command/descriptor
  lifecycles**. Each write fixture verifies only the initial and latest values
  run, navigation/close preserve pending metadata, and a deliberate failed final
  write does not persist the failed value.
- Process tests cover both large pipes, continuously writing timeouts, closed
  pipes with a live child, trailing inherited output, unreaped leader lifetime,
  four active groups with grandchildren, 32 concurrent spawn/shutdown workers,
  successful/nonzero results, spawn failures and deadline overflow. FD counts
  remain unchanged through repeated success/failure/timeout/shutdown paths.
- Interrupted waits preserve results and deadlines with captured and discarded
  output. `strace` fault injection forces `pidfd_open` to fail with `EMFILE`;
  the child is killed/reaped, the registry clears, and the next command succeeds
  without leaked descriptors. Separate `poll`/`EINVAL` fault injection also
  verifies group kill/reap, descriptor cleanup and successful next-command
  recovery. A traced **200 ms timeout used 20 poll calls before
  and 2 after** (whole test, including harness activity), rather than periodic
  ten-millisecond process checks. This is not an application-wide CPU benchmark.
- Boot fixtures prove read-only queries start before the Iced task is polled,
  microphone replies publish while output-volume IPC waits, and only an active
  category suppresses a subsequent read. A **20 ms Iced timer fired in 22.1 ms**
  in the initial focused run with all four blocking workers occupied; repeated
  stress tests also require timer progress.
- **31 isolated GPU environment cases** cover absent, empty and nonempty
  overrides; all **15 preference variables** are checked in direct and finite
  child-command paths. Actual structured and shell systemd launchers also survive
  parent shutdown with the original session GPU environment.
- **24 live release launches**, with graceful PID-targeted Hyprland window closes
  and duplicate-launch checks, passed. Cases include automatic selection,
  explicit Vulkan, explicit EGL, Vulkan failure falling back to EGL and 20
  additional reopen/close cycles. An explicit Vulkan-only failure and failure of
  both backends return errors without a frame or unwanted backend override.
- Locked offline debug/release builds and **Clippy `--all-targets -D warnings`**
  pass. New/rewritten GPU, worker, command and renderer modules pass rustfmt.
  Whole-project formatting has confirmed pre-existing failures; unrelated source
  formatting was retained. Final changed diffs pass whitespace checks. No new
  dependencies, lockfile changes, Tray edits or installer-code changes were made.

## Startup measurements

Five alternating before/after launches used the same debug profile, configuration,
display and `WAYLAND_DEBUG=1` observation method. The timestamp is from process
launch to its first non-null Wayland surface buffer attachment, not display
scanout. This avoids comparing the old disconnected trace hook with the newly
connected compositor trace. All times are milliseconds:

| Run | Before | After |
| --- | ---: | ---: |
| 1 | 3935.5 | 558.5 |
| 2 | 5085.3 | 559.2 |
| 3 | 5128.2 | 560.9 |
| 4 | 5115.9 | 544.3 |
| 5 | 5075.0 | 557.5 |
| Median | **5085.3** | **558.5** |

The observed median fell **89.0%** on this machine. This is a debug-build,
local-session comparison with warm filesystem caches; it does not quantify
release-build gains on other GPUs or an ISO's disk-cold first boot. The earlier
single launch without automatic driver selection still took about 3.88 seconds
after connecting the Vulkan-first renderer, supporting driver probing as the
major local remaining delay.

The final optimized release was separately exercised with normal automatic
selection, explicit Vulkan, explicit EGL and forced Vulkan failure. First-frame
submission traces were **124.0, 135.8, 208.3 and 183.2 ms**, respectively. The
failure case deliberately supplied an unusable Vulkan driver list and a valid
Mesa EGL override, logged the Vulkan failure, then rendered through EGL. Each
live case also verified that a duplicate launch exits successfully. These are
single release smoke measurements with no matched release baseline.

| Area | Before score | After score | Concrete result | Assessment |
| --- | --- | --- | --- | --- |
| Startup performance | N/A | N/A | Debug median 5085.3 → 558.5 ms | Observed better locally |
| Waiting efficiency | N/A | N/A | 200 ms timeout: 20 → 2 poll calls | Observed fewer wakeups |
| Query/timer reliability | N/A | N/A | Shared blocking executor → separate pools; 20 ms timer completed in 22.1 ms under four blocked queries | Verified better isolation |
| Runtime filesystem reliability | N/A | N/A | Assumed RAM path → verified tmpfs and stable selection | Better by code and filesystem tests |
| Core read reliability | N/A | N/A | Global invalidation → per-control edit checks; regression tests pass | Verified better |
| GPU/child reliability | N/A | N/A | Ambiguous GPU guess and leaked tuning → conservative discovery and session inheritance | Verified better in fixtures/local launches |
| Shutdown reliability | N/A | N/A | Direct-child death signal → active process-group cleanup on runner return | Verified better for owned active groups |

No defensible application-wide 1–100 scoring scale exists for these measurements;
N/A avoids inventing numerical quality ratings. No memory or general steady-state
CPU speedup is claimed.

## Environment and limits

Development environment: kernel **7.3.0-rc6**, Rust/Cargo **1.99.0**, systemd
**262**, Bash **5.3.20**, Hyprland **0.56.0-273-g19fb395d4**, Mesa/vulkan-intel
**26.2.4**, Vulkan loader **1.4.363.0**, NVIDIA userspace **615.78.08**, and Python
**3.15.0rc3**. Cargo resolves Iced core/renderer/wgpu **0.14.0**, widget **0.14.2**,
window runner **0.14.1** and wgpu **27.0.1** from the existing lockfile.

The live device is Intel i915; NVIDIA userspace driver files are installed.
GPU fixtures cover discovery order/vendor variants; physical AMD, NVIDIA,
Virtio and multiple-GPU machines were not available. Without a valid
`AQ_DRM_DEVICES` hint, only a single DRM-card system is automatically filtered.
Multiple cards retain normal discovery; firmware order is not assumed. Driver
filtering does not uniquely select a physical device among same-driver GPUs.
Explicit power/offload/loader preferences bypass filtering.
No full ISO installation, disk-cold boot or physical DDC-monitor test was run.
The ISO has not shipped, so its final package versions cannot be certified from
this development machine. No claim of universal speedup or absence of all
possible configuration/hardware edge cases is made.

Cleanup covers active finite commands remaining in their owned process groups
on normal runner return. Forced SIGKILL/abort cannot execute that cleanup; Linux
parent-death signaling covers the direct child, not arbitrary grandchildren.
Commands that deliberately detach from their group or leave background children
after completing are outside that active-group guarantee. Privileged/custom
external actions were not comprehensively executed. The four-worker bound limits
concurrency, not latency: several slow queries can still queue a write until a
worker becomes available. Existing command deadlines and close draining remain.
No universal resource/latency guarantee under kernel-uninterruptible I/O is made.

At the user's subsequent request, `./scripts/install.sh` built and installed
a CPU-native release in `~/.local/share/dusky/dusky_center/dusky-center`, with
`~/.local/bin/dusky-center` pointing to it. Its source, CPU and binary fingerprints
were verified. The installed launcher rendered a frame, rejected a duplicate
launch successfully and closed gracefully; its first-present trace was 64.9 ms
in that single smoke run. This native build has different CPU flags from the
earlier release measurements, so that timing is not a matched comparison.
Existing configurations were preserved. A matching generic ISO package instead
lives at `/usr/bin/dusky-center`, selected through the same local launcher.

## Interface references

- [Linux pidfd polling and lifecycle](https://man7.org/linux/man-pages/man2/pidfd_open.2.html)
- [Iced 0.14 application renderer parameter](https://docs.rs/iced/0.14.0/iced/fn.application.html)
- [Vulkan loader interfaces and environment overrides](https://vulkan.lunarg.com/doc/view/latest/linux/LoaderInterfaceArchitecture.html)
- [Mesa GPU selection environment variables](https://docs.mesa3d.org/envvars.html)

Additional interface checks: [Linux parent-death signal semantics](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html), [Rust Linux process APIs](https://doc.rust-lang.org/std/os/linux/process/trait.CommandExt.html) (the `linux_pidfd` extension remains nightly in the observed toolchain; the stable syscall plus `OwnedFd` is retained), and [Hyprland window selectors](https://wiki.hypr.land/configuring/selectors/) for isolated live window closes.
