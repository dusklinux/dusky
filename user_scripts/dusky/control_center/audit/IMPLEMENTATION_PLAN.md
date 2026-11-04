# Dusky Control Center: audit and implementation handoff

> Historical pre-fix audit. The confirmed fixes were implemented on 2026-09-26; see [IMPLEMENTATION_RESULT.md](IMPLEMENTATION_RESULT.md) for current behavior and verification. The reproduction artifacts below describe the old source.

Audit date: 2026-09-26. Application source was not modified. Only files under `audit/` were added. No sudo credential was needed or used.

## Verdict

Keep Dusky's Python/GTK architecture and its existing appearance. It is already a better fit for this machine's system-management workflow than HyprMod. It is not flawless: several reproducible state, command, and configuration problems deserve fixes. HyprMod is itself Python/GTK4/libadwaita, not a Go application. Its main strength is integrated Hyprland configuration editing; it is not a replacement for Dusky's system, audio, memory, installer, and service tools.

No evidence justifies a language rewrite, blanket async rewrite, full configuration framework, or implementing every HyprMod feature. Recommended order: confirmed correctness fixes, action/state infrastructure with real consumers, service observations, then optional usability and feature work.

## Evidence and limits

Installed versions observed with `pacman -Q`: Python 3.14.7-1, python-gobject 3.56.3-1, GTK 4.22.5-1, libadwaita 1.9.4-1, Hyprland 0.56.2-3, systemd 262-1. `hyprctl version -j` also reported 0.56.2. These are the machine's measured versions, not a claim that no newer upstream commit exists.

Completed checks:

- `python -m unittest discover -s tests -v`: 29 tests passed in 1.641 seconds, including real GTK map/hide/reopen, three repeated view rebuilds with weak-reference collection, failed reload, CSS rejection, settings ordering, and process-group cancellation tests.
- Constructed all 18 top-level and 27 explicit nested navigation pages using the real GTK classes and loaded TOML. No application warnings or errors; no action launches. This is construction coverage, not proof that every button's external program works.
- Parsed all 346 configured command/query strings with `/bin/sh -n -c`: no shell syntax errors. This does not validate command semantics, quoting of substituted data, executable availability, privileges, or side effects.
- Queried all 17 distinct configured systemd units read-only; all were loaded. There are 26 configured service widgets because some units appear on several pages.
- Ran `python audit/reproduce_findings.py`. Real subprocesses and temporary-file fixtures reproduce the issues documented below. Action dispatch is mocked where executing it would change the desktop. Results are in `audit/reproduced.json`.
- Examined launcher, service unit, keybinding/window rule, selected external helpers, and the Matugen GTK4 template where they explain app behavior.

Scope limits: no installer, reset, disk, swap, VPN, firewall, keylogger, reboot, or other consequential action was executed. The hundreds of external helper programs are not all independently audited. HyprMod was inspected as source, not installed or launched: its window constructor explicitly reloads the running compositor when online. Its test suite was not run. Reference review focused on state/ownership, configuration writes, schema, undo/profiles, monitor confirmation, options, search, lifecycle helpers, and test design; it is not a certification of every line in that repository.

The existing test run emitted Python asyncio-policy deprecation warnings from installed `gi/events.py`, not Dusky's own event-loop setup. Do not add a local monkey patch to silence them.

## How Dusky is configured

### Files and responsibilities

| File | Current responsibility | Assessment |
|---|---|---|
| `dusky_control_center.py` (2,055 lines) | Application lifecycle, config validation, lazy pages, navigation/search, generators, reload and CSS provider | Keep controller model; extract pure validation when extending it |
| `dusky_config.toml` (4,808 lines) | Page structure, properties, commands, service units, persistence keys, generators | The actual feature catalogue; must migrate alongside any action API |
| `lib/rows.py` (4,252 lines) | Widgets, monitoring, IPC, async runners, command substitution, service views | Main correctness work belongs here; consolidate concrete repeated behavior |
| `lib/utility.py` (754 lines) | TOML loading, launch command parsing, terminal wrapping, system info, atomic buffered writes | Preserve atomic writes and shell-aware behavior; distinguish launch from completed action |
| `lib/service_manager.py` (452 lines) | Unit normalization, async `systemctl`, privilege handling | Already much stronger completion handling than generic controls; state model is too narrow |
| `dusky_text_editor.py` (99 lines) | Reads editor/terminal defaults from Lua; execs through `dusky-run` | Works with configured direct assignments; not a general Lua interpreter |
| `dusky_style.css` (865 lines) | Custom themed layout, cards, buttons, sliders, entry internals | Preserve appearance; named colors and private-widget assumptions need controlled modernization |
| `tests/test_reliability.py` (366 lines) | 29 focused regression tests | Valuable baseline; not end-to-end coverage of every action |

`audit/config_inventory.json` contains the complete parsed node/property and command inventory plus source SHA-256 hashes. This is more reliable than making the next model reconstruct the TOML from prose.

### Page coverage

| Page | Main role |
|---|---|
| Home | Radios, theme, updater/power launchers, power profile, volume/brightness, memory/kernel |
| System | Updates, snapshots, locale, session, Git, services, kernel tooling |
| Memory | ZRAM/disk-swap helpers, proactive reclaim controls and telemetry |
| Disk & Files | Storage tools, filesystem maintenance, file manager, FTP |
| Network | Radios, WARP/Tailscale/SSH, generated WireGuard controls, DNS/hosts tools |
| Hardware | TLP, power saver, handedness, input configuration |
| Display | Brightness and launches for rotation, scaling, monitor configuration |
| Audio | Audio Studio/DSP, noise suppression, presets, volume, devices/services |
| Visuals | Theme, wallpaper, appearance, animation and shader tools |
| Components | GTK, clipboard, clock, Waybar, notifications, lock/idle |
| Services | Startup settings and system/user service toggles |
| Configs | Default apps, resets, editor links, generated theme editor entries |
| Keybinds | Existing editing/search/cheatsheet tools |
| Tools & AI | Existing LLM, OCR, speech, media tools |
| Setup | Installers and optional setup tools |
| Troubleshoot | Existing repair/reset tools |
| Keylogger | Existing local tool/service launchers; outside this audit's action testing |
| About | Version and hardware/system labels |

Configured widgets include 172 buttons, 13 toggle cards, 12 toggles, 10 grid cards, 9 selections, 8 entries, 5 sliders, 13 labels, 19 service rows, 7 service cards, 27 navigation entries, and 4 explicit expander templates/entries. There are 2 directory generators and 1 file generator. Counts include template definitions, not every generated runtime instance. `spin`, `secret`, `multi_text`, `keybind`, `color`, `path`, `async_selector`, and `flag_group` exist in code but are not used by this TOML.

There are 13 `key` bindings across 12 unique settings: `dusky_theme/state` occurs twice; the others are `auto_login_tty`, `filemanager_switch`, `warp_state`, `power_saver_state`, `left_handed_mouse`, `mono_audio`, `wayclick`, `opacity_blur`, `clipboard_persistance`, `clipboard_state`, and `time_format`. Do not rename spellings such as `clipboard_persistance` without migrating the external consumers.

### Lifecycle, launch and persistence

- App ID is `com.github.dusky.controlcenter`. Startup calls `hold()`, builds Home hidden, and realizes the window. Repeated activation toggles visibility; close hides; Ctrl+Q quits. Other root pages and nested navigation are lazy.
- The installed unit is `~/.config/systemd/user/dusky.service`, not `dusky_control_center.service`. It uses `Type=dbus`, `--gapplication-service`, restart backoff, and a 2-second stop timeout. It was inactive when queried; do not infer that the app is always currently running as this service.
- SUPER+SPACE invokes the app's D-Bus Activate method. A Hyprland rule floats/centers it at width 670 and 90% monitor height. This overrides much of the script's 639×800 default. Preserve that integration.
- Config/CSS are loaded relative to the script. Ctrl+R reloads; there is no automatic file watcher for these two files. Reload requests coalesce, malformed TOML retains the old config, and CSS parse errors retain the old provider.
- Search debounces 200 ms, ranks matches, caps display at 50, searches generated items, and navigates/highlights rather than duplicating live controls. Generator caching preserves runtime item identity across search/build.
- Settings use `$XDG_CONFIG_HOME/dusky/settings` with a home fallback, a 250-ms buffer, serialized disk writes, temporary sibling files, file fsync, atomic rename, directory fsync, and shutdown drain. Pending reads see buffered values. Keep these guarantees.
- `execute_command()` returns spawn success. It preserves shell semantics when needed, wraps terminal programs, uses `pkexec` when requested, and normally prepends `/usr/local/bin/dusky-run`.
- `dusky-run` uses a transient systemd user scope with Dusky's OOM policy. Therefore rapid slider events currently create wrapper/systemd work as well as the final command. Preserve the scope policy when introducing completed-action handling.
- Generic status polling already has cancellation, generation guards, timeouts, and hidden-widget suspension. Selection fetches still use a separate thread/subprocess path. Service checks are once-per-map, not live subscriptions. `hyprland_event` is implemented but configured on zero current widgets.

## Required fixes, in implementation order

### 1. Radio off state must be an actual successful result

**Confirmed:** Home and Network use `rfkill list ... | grep -q 'Soft blocked: no' && echo yes` for Bluetooth and Wi-Fi. When blocked, the command exits nonzero. `_run_shell_async()` returns `None`; `_poll_command()` ignores that value. A previously true card stays true. The fixture reproduces exactly this behavior without changing a radio.

Files: the four radio `state_command` entries in TOML; `StateMonitorMixin` and polling result handling in `lib/rows.py` if adding unknown-state display.

Implementation:

1. Return explicit true/false with successful status for a valid query. Separate tool/query failure from a legitimate blocked state. A robust query reads rfkill's structured output and tests the relevant device records, including hard blocking if the card means usable radio power.
2. Define absent-device behavior explicitly: unavailable/disabled control is more informative than a permanently false but clickable card.
3. A minimal `... && echo yes || echo no` fixes the observed stale-off bug, but also turns rfkill failure into false; do not present that minimal change as complete error handling.
4. Keep radio writes in existing authorized helpers/commands; no need for a new network management subsystem.

Acceptance: fixture outputs for unblocked, soft blocked, hard blocked, absent device, missing executable and nonzero query failure. Show true, then false on the same mapped card. Failed query must be distinguishable from false. Test both duplicated pages.

### 2. Give each persisted setting one writer and a defined representation

**Confirmed:** `save_setting()` serializes bool with `str(value)`, producing `True`/`False`. `toggle_time.sh` checks lowercase `true` and writes lowercase values itself. The temporary fixture starts from Python's `True` with 24-hour formatting; invoking the helper's toggle leaves 24-hour formatting unchanged instead of switching to 12-hour. Generic toggle rows also launch helpers and queue their own write to the same key, creating competing writers and allowing a failed helper to leave a success-looking state file.

Files: `lib/utility.py:save_setting`, `ToggleRow`, `GridToggleCard`, `SelectionRow`; the 13 TOML key bindings; relevant external helper contracts.

Implementation:

1. Inspect the writer/reader for each of the 12 unique keys listed above. Record ownership, true/false or enum representation, and readback method. Some helpers may depend on uppercase values: do not globally lowercase without this inventory.
2. Add an explicit persistence ownership field to the validated schema, for example `persistence = "action" | "app"`. Set it explicitly on all current key-bound controls. `key` can remain the readback source even when the helper owns writes.
3. For action-owned keys, the GUI must never independently save the optimistic requested state. Let the helper publish its authoritative result; refresh after completion.
4. For app-owned booleans, standardize the documented storage representation and migrate affected consumers together. Preserve existing nonboolean enum values.
5. Continue displaying the last confirmed value on failure. Handle file deletion as a state refresh too; the current file monitor ignores `DELETED`.

Acceptance: temporary settings directories; failed helper leaves stored value untouched; cancelled authentication does not persist success; external helper writes update both duplicate controls; repeated toggles yield the same disk and UI state; file delete/recreate/atomic replace all refresh. Test clock via a temporary XDG config tree and a stub `pgrep`, as the audit probe does.

### 3. Separate launching an application from applying a setting

**Confirmed:** EntryRow always emits `Applied: ...` or its configured toast after calling `execute_command()`, even if that function returns false. The fixture gets `Applied: 42` from a mocked failed spawn. A successfully spawned command may also later fail. Ordinary toggle/card/selection actions mostly ignore outcomes; service actions already track completion.

Files: `lib/utility.py:execute_command`, action handlers in `lib/rows.py`, validated TOML action definitions. Introduce a small `lib/actions.py` with actual consumers; do not duplicate another shell parser.

Implementation contract:

- Define explicit action behavior: detached launch versus a finite apply operation. Keep launchers/terminal tools as launchers; their success text should say Launched, not Applied.
- A finite apply returns a structured result: spawn failure, exit status, timeout/cancellation, bounded stderr, and success. Use `Gio.Subprocess` async APIs; no blocking calls in GTK callbacks.
- Prefer argv for data-bearing actions. Retain an explicit shell form for real pipelines and scripts. The next task migrates unsafe generated forms.
- Keep existing `dusky-run` scope placement. Verify that observing its process completion reflects the child outcome with the installed wrapper; test success, nonzero exit, and scope startup failure. If a separate setting runner is introduced, it must preserve the intended OOM/scope behavior explicitly.
- Track finite operations at application/controller lifetime. Hiding a widget should cancel observation work, not a user-requested change. Reload currently unroots service widgets and calls `_toggle_handle.cancel()`; cancelling pkexec/systemctl does not reliably undo an already submitted systemd job. Reload must not imply rollback.
- Widgets subscribe to the operation outcome using generation/lifetime guards. Disable a discrete control while applying, then read the actual value back. Persist only according to task 2.
- Classify long terminal dialogs/installers as detached launchers. Do not run every command under a universal 2-second timeout or claim to undo arbitrary scripts.

Acceptance: real fixture scripts that exit 0, exit 7 with stderr, fail to spawn, exceed timeout, and spawn a child. Callback once; truthful toast; no GTK blocking; no false persistence; no child leak for a cancelled fixture operation. Start apply, hide/reopen, switch pages, reload, then complete; the transaction must remain coherent and a rebuilt widget must show the actual result. Use temporary fixture services for systemd behavior, not production services.

### 4. Fix continuous sliders and stale query responses

**Confirmed:** All five configured sliders set `debounce=false`: four volume/brightness controls and the noise-suppression aggressiveness control. A fixture moving through 80 different values produces 80 launches. A slow query can also overwrite a newer immediate edit: set 80, then deliver old readback 10; the displayed slider becomes 10. The existing regression test only protects while `_pending_value` is non-None; immediate dispatch clears that protection.

Files: `SliderRow`, `SpinRow`, `SliderMonitorMixin`, polling generation handling, the five configured slider definitions.

Implementation:

1. Give setting edits a revision counter. Invalidate/cancel observations started before an edit; only accept a result associated with the current revision and no newer pending apply.
2. Use a latest-value queue: at most one finite apply in flight per setting and one replacement pending value. Coalesce intermediate drag positions. Never cancel an already-started system mutation solely because the slider moved again.
3. Throttle responsive volume/brightness updates to a measured interval (start around 50–100 ms), then guarantee the final requested value is dispatched. Keep the thumb responsive independent of subprocess duration.
4. After the last apply completes, read back the real value. Reject stale observations from before that apply. Failed apply must be visible.
5. Test keyboard stepping and scrolling as well as pointer drag. For debounced controls define hide/quit behavior; a pending edit must not silently disappear during reload.

Acceptance: delayed reads before/during/after an edit, rapid 0→100→20 input, slow/out-of-order command fixtures, final value 20 both in UI and backend, bounded launch count, no replay after widget disposal. Compare command/scope counts and UI responsiveness before/after; do not claim a particular speedup without measuring.

### 5. Unify selection subprocess cleanup with the working async runner

**Confirmed:** `SelectionRow._fetch_selection_async()` and `_fetch_options_async()` use `subprocess.run(shell=True, timeout=...)` in the thread pool. Hiding invalidates results but does not cancel these processes. A timeout fixture spawning `sleep` leaves the child alive; the audit explicitly kills that child afterward. The newer `_run_shell_async()` already handles process groups, and its existing tests pass.

Files: the selection fetch methods and `LabelRow._exec_cmd` in `lib/rows.py`; retain the existing protected runner or extract it without changing semantics.

Implementation: route finite command queries through a shared cancellable argv/shell query runner. Keep file reads/system-info work in a worker when useful. Preserve independent options and selection generations, one active fetch plus a coalesced follow-up, signal suppression, and selection preservation. Retain the old worker ordering regression tests. Inspect LabelRow's remaining synchronous helper path before converting; its current exec-valued labels normally already use async polling.

Acceptance: real subprocess child cleanup on timeout/unmap/dispose; query finish after remap cannot update a new generation; options refresh preserves selection; no hidden periodic sources; no unbounded worker queue; all existing lifecycle tests pass.

### 6. Treat generated filenames as data, never shell source

**Confirmed:** `_inject_variables()` substitutes raw strings into all commands. A legal directory name containing `$(printf CHANGED)` changes when inserted inside double quotes and executed through a shell. WireGuard templates also insert unquoted paths/names into commands and interpolate into nested single-quoted shell programs. This is a correctness problem even without an attacker: spaces and apostrophes are enough to break commands.

Files: both generators and `_inject_variables` in `dusky_control_center.py`; all three `item_template` definitions in TOML; the new action argv path.

Implementation:

1. Keep plain text substitution for titles/descriptions. For argv actions, replace a placeholder inside its existing argument string; never split a substituted argument again.
2. Migrate generated editor actions to an argv list with a separate filename argument. Expand trusted home paths explicitly, not by executing a shell.
3. For genuinely necessary `sh -c` logic, use a fixed script and positional parameters: `sh -c '... "$1" ...' dusky-action <path>`. Pass the filename/name separately. Do not fix nested quoting by globally applying `shlex.quote()` to every display substitution.
4. WireGuard interface names have their own constraints. Validate them separately from config path handling; support legitimate subdirectory config paths. Review the existing `/etc/wireguard` permissions helper instead of blindly changing directory permissions.
5. Use a dedicated helper or a native confirmation for delete, with paths as argv and an option delimiter. Keep the current explicit deletion confirmation. Audit no-terminal `sudo` calls: if NOPASSWD is an intentional local policy, report failure accurately when it is absent; otherwise use the established Polkit path. Do not guess or broaden sudo policy.

Acceptance: generated names with spaces, apostrophes, double quotes, dollar signs, backticks, braces, semicolons, Unicode and leading hyphens. Capture argv in a fixture helper; require byte-for-byte intended path and no evaluated substitution. Verify cached generated identities still match search/highlight after reload.

### 7. Refresh entry values without overwriting edits

**Confirmed:** EntryRow fetches `value_command` only while text is empty. Once a value was loaded, reopening the page never queries it again. Existing protection against overwriting user typing is good, but conflates loaded text with an unsaved edit. This affects memory/ZRAM/reclaim entries whose values can be changed externally.

Implementation: track `last_confirmed_text`, dirty status, request revision and apply state. On map, refresh a clean entry; preserve dirty text. On successful apply, read back the actual value and mark clean. On failed apply, retain the typed value with an error. An empty field must not automatically mean pristine; define whether empty input is valid per control. Do not turn every entry into a continuously polled text field.

Acceptance: clean reopen reflects an external change; typing survives a delayed read; clearing a field survives delayed initialization; apply success/failure behaves correctly; hide/reopen has no obsolete callback or leaked command.

### 8. Validate the actual configuration language and fix stale editor links

**Confirmed:** `_validate_config_node()` verifies container shapes but accepts unknown widget types, incorrect property types, and numeric action commands. Unknown root item types become buttons; unsupported expander child types disappear. The audit fixture is accepted despite all those invalid semantics. Current valid configuration still builds all 45 pages.

Also confirmed: `Environment Global` and `Environment Hyprland` point to absent `~/.config/hypr/conf/environment.conf` and `environment-hyprland.conf`. The current tree uses Lua and already exposes `edit_here/source/environment_variables.lua`. Opening absent obsolete files can create ineffective configuration. A missing GPU Screen Recorder config may instead be a legitimate optional-install condition; do not blindly remove that entry.

Files: controller validation/builders, `ExpanderRow._build_single_row`, TOML editor links, new pure `lib/config_schema.py` if useful.

Implementation:

- Validate known node kinds, valid parent/child contexts, required fields, exact property types, finite numeric ranges, positive step, action shapes, generator templates and placeholder names. Reject bool where a number is required. Distinguish a selection's action map from a direct action.
- Validate page ID uniqueness and redirect destinations. Report a full config location and useful error; malformed reload must preserve the working UI.
- Inventory all currently used properties first using `config_inventory.json`; do not reject valid properties because `TypedDict` declarations are incomplete.
- Have expanders reuse the canonical row builder through context, with allowed child types defined by the schema. Grid-only widgets should be rejected in invalid contexts, not silently skipped. No need for a runtime plugin registry.
- Add a pure `--validate` path that does not import GTK, require a display, create settings directories or launch commands. Move the current environment check/preflight out of import-time code as needed. Missing PyGObject currently fails on importing `lib.utility` before its intended preflight message; make dependency diagnostics truthful.
- Remove the two obsolete environment links, or replace them only with verified intended current sources. Keep the existing user override Lua editor; do not expose the defaults file as the place for ordinary customization.
- Add explicit existence requirements only to editor/open-existing actions. Install/create commands must remain usable when their targets do not exist.

Acceptance: full real config validates; malformed semantic fixtures fail before widget build; valid configuration remains loadable headlessly; wrong command types, unknown types, bad ranges, duplicate IDs and unresolved placeholders get exact error paths; old environment links are absent; editor targets resolve; failed reload retains prior UI/CSS.

## Reliability improvements after the confirmed fixes

### Service state and observation

The current UI shows `is-active` but acts with `enable/disable --now`. Runtime activity and boot enablement are independent. Only `active` counts as true; failed/activating/not-found all collapse to false when stdout is present. The batch API exists but current widgets call the single-unit function individually. There is no subscription while a page remains open. All 17 configured units exist on this machine, so missing-unit presentation is a future/fixture case, not an observed missing dependency.

Recommended contract: display runtime status and enablement separately. Either retain one clearly labelled combined action (“Enable at startup and start now”), or provide separate startup and runtime controls. Model `LoadState`, `ActiveState`, `SubState`, `UnitFileState` and operation error. Avoid automatically interpreting `inactive` for every oneshot as disabled.

Build one shared service observer with Gio D-Bus connections for system and user scope, on-demand initial state and `PropertiesChanged`/unit change handling. Subscribe only while consumers exist, handle bus owner changes, and resnapshot after reconnect. Use documented systemd Manager Subscribe/Unsubscribe semantics where required. A smaller first step may use the existing batch API and an explicit refresh, but must not claim it is live observation. Preserve proper Polkit authentication for modifications; read-only observation does not need sudo.

Test with temporary user units: active+disabled, inactive+enabled, failed, activating, missing, masked, static and oneshot; change state externally while a widget remains mapped; shared widgets agree; bus disconnect/reconnect; authentication cancellation; reload during operation. Add `--` where applicable and reject option-like unit inputs. Do not change real SSH, VPN or firewall services to test.

### Settings path and reload errors

`_validate_settings_path()` rejects `..` but resolves an existing symlink to any destination; `StateMonitorMixin` rejects monitoring paths outside SETTINGS_DIR. The fixture demonstrates this inconsistent contract. No escaping symlink was found among the sampled live keys. Define one policy: allow paths whose resolved target remains inside the settings root, and reject external targets. Reject the settings directory itself as a key. Test internal, external, dangling and nested symlinks. Keep this a local data-contract fix, not a claim that the app is a security sandbox.

CSS read failures currently become empty CSS, causing a successful reload to remove the working provider. Distinguish intentional no stylesheet from I/O/UTF-8 read failure; reject the latter reload and preserve existing style. Add corresponding injected-error tests.

Reload restores the selected page by index. Use the already-defined stable page IDs so adding/reordering pages retains the same page. Preserve the deepest still-valid navigation path if worthwhile; fall back explicitly to that page's root. No need for UUIDs everywhere.

## Modern GTK and future development

### Keep working APIs; modernize the real weak points

Gio's callback async APIs are supported current APIs. Do not replace them merely to use `async def`; PyGObject's own documentation currently labels asyncio integration experimental. The installed warnings come from its policy integration. Likewise `match` is not inherently “hyper-fast”; remove performance claims from docstrings unless backed by a benchmark.

New UI should use public GTK4/libadwaita APIs already available in the installed stack. `Gtk.FileDialog` and `Gtk.ColorDialog` are already in use. For shortcut help use `Adw.ShortcutsDialog`, not HyprMod's older `Gtk.ShortcutsWindow` implementation. For ordinary application actions use `Gio.SimpleAction` plus application accelerators so help/menu/keybindings derive from one action list.

Remove the historical `/tmp/hypr/...` socket fallback when touching Hyprland IPC; use the documented `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock`. An absent/reconnecting compositor should produce disconnected state, not a legacy-path guess. Current TOML uses no `hyprland_event`, so do not build a shared Hyprland event service solely for hypothetical consumers. If native monitor/input controls are introduced, share a single connection then, match complete event names before `>>`, coalesce bursts, and retain real query cancellation.

### Theme and accessibility

The stylesheet parses successfully today. Named colors work with your existing Matugen output. Modern libadwaita offers CSS variables; its docs explain named compatibility colors do not pick up variable overrides. Your `~/.config/gtk-4.0/gtk.css` symlinks to Matugen's generated GTK4 CSS and the `gtk4-colors.css` template currently emits named colors. Therefore replacing `@accent_bg_color` with `var(--accent-bg-color)` only in Dusky is not a safe standalone edit.

If modernizing, update the authoritative Matugen GTK4 template and Dusky together: explicit color-variable definitions from the same generated palette; direct mappings for window/card/accent/error/warning colors; consciously map `@borders` to the intended border variable. Do not globally rewrite GTK3 theme output or other consumers. Keep layout/radii/spacing identical. Require light/dark screenshots and matching palette values before accepting it. This is maintenance work, not an urgent functional bug.

`SliderRow` clears its title and hides internal children. The scale itself should get an accessible label from its configured title and expose its value; optionally give a compact value label/tooltip if it fits the design. Grid toggle cards should expose toggle state to assistive technology, not only colored CSS and ON/OFF text. Verify with GTK accessibility inspection/AT-SPI, not just a constructor test.

Three areas depend on private widget structure: slider title hiding, EntryRow's `_hide_internal_adwaita_icons`, and preferences-group centering. Replace with supported composition/CSS nodes when touching them, or isolate them and require rendering smoke tests after GTK updates. Do not remove the entry-icon workaround blindly; it was added for a specific theme issue. Scoped app CSS and focus-visible checks are worthwhile; a full visual redesign is not.

### Configuration authoring and diagnostics

Document the validated TOML schema and one working example per supported configured widget. Derive supported types from the same definitions used by validation/building. Avoid an enormous generic settings backend abstraction.

Add a small diagnostics view or command showing installed library versions, config path, validation errors, missing declared capabilities, and last finite-action error. Store only bounded/sanitized diagnostics: do not log arbitrary secret inputs or whole command lines. Keep diagnostics user-facing (“brightness tool unavailable”), not class names and stack traces.

Current `SecretRow` is unused and stores text in ordinary settings files and can put it in argv. Before exposing it in TOML, define secure storage/transport (e.g. the system secret service when persistence is actually needed). It should not be advertised as a password manager simply because it masks entry text. No need to add a secrets backend while there are no consumers.

## What to borrow from HyprMod, and what to defer

| Reference implementation | Useful idea | Recommendation for Dusky |
|---|---|---|
| `hyprmod/core/state.py` | Separate live, saved, default values; apply outcome before confirming a change | Adopt the smaller requested/confirmed/error state needed by current controls |
| `core/ownership.py`, `core/config.py` | Explicit ownership; managed-file serialization and atomic writes | Borrow ownership discipline; Dusky already has atomic persistence |
| `core/undo.py`, `core/pending.py`, `pages/pending.py` | Undo history, per-setting dirty state, review changes | Optional only for explicitly reversible native settings; never promise undo for arbitrary scripts/installers |
| `pages/monitors/page.py`, `confirm_controller.py`, `ui/monitor_preview.py` | Monitor geometry, validation, preview, confirm/revert timer | Most useful substantial future GUI feature; existing display TUI/wizards mean it is optional |
| `pages/animations.py`, `ui/bezier_editor.py` | Visual curve editor and preview | Optional convenience over Dusky's existing animation tools |
| Binds/rules/workspaces/autostart/env pages | Integrated editors with structured domain models | Existing Dusky tools already cover much of this; no need to rebuild all now |
| `core/profiles.py` | Named snapshots of owned settings | Defer until there is a bounded set of reversible owned settings |
| `ui/search.py` | Precomputed search text | Dusky already has ranking, fuzzy normalization and result limits; measure before replacing |
| `core/bug_report.py` | Version/path context for diagnosis | Add small local diagnostics; automatic reporting/upload is unnecessary |
| `core/schema.py`, migration code | Version-aware universal-app schema and migrations | Do not import old-version support into this current-only application |
| `ui/timer.py`, `ui/signals.py` | Central timer ownership and blocked programmatic updates | Keep Dusky's tested lifetime/generation mechanisms; factor only duplicated real behavior |

HyprMod contains behavior that should not be copied blindly: its window startup reloads the compositor; its broad migration logic solves a different support policy; several state rollback paths log IPC failure after updating model state. Those observations are source-level cautions, not an empirical claim that HyprMod is generally unstable or slower.

If native monitor editing is later selected: use current Lua/config APIs and live monitor data from the installed compositor, validate geometry/modes/mirroring, take a pre-change snapshot, apply temporarily, require confirmation, and persist only after acceptance. Restore on timeout, close, or explicit reject. A main-loop countdown alone cannot guarantee restoration after an app crash or frozen UI; use an independent revert mechanism for that guarantee. Test hotplug, last-output disable, invalid mode, fractional scale, app termination and confirmation. This is a separate project, not prerequisite infrastructure for fixing the current app.

## Execution instructions for the next model

1. Read this plan, `audit/reproduced.json`, `audit/config_inventory.json`, and the existing tests. Compare inventory hashes to current source so you notice intervening user changes. Work only on authorized phases; optional native editors/profiles are not automatically selected.
2. Preserve a source backup or create a local version-control baseline before edits. This directory was not a Git checkout during the audit. Do not invent a remote or push anything.
3. Keep the Python script entry point, TOML configuration, installed system integration, and visual design. Target the actual current Arch stack; no obsolete API fallback layers or migration support for old Hyprland syntax.
4. For every confirmed bug, first turn the audit reproduction into a failing regression test for the desired behavior. `reproduce_findings.py` asserts OLD behavior and will intentionally stop passing as fixes land; it is evidence, not the permanent acceptance suite.
5. Implement small coherent changes in the order above. Share query/action infrastructure only where it immediately replaces actual duplicated behavior. Do not combine a style rewrite with process/state changes.
6. Run the existing suite and focused new tests after each phase. Use real subprocess fixtures and temporary settings/files/services for completion, failure, timeout, cancellation and ordering tests. Mocks alone do not prove process cleanup or disk semantics.
7. Perform a real GTK smoke pass: open/close/toggle, all 18 pages and 27 nested pages, generator expanders, search result navigation/highlight, keyboard controls, reload while hidden/visible, invalid TOML/CSS, repeated reload collection, light/dark styling. Record what was manually observed versus automated.
8. Measure before/after startup, visible query/command counts, hidden periodic work, rapid-slider scope launches and repeated-reload memory. Treat RSS alone cautiously because GTK allocators cache memory; inspect surviving widgets, processes, descriptors, and GLib sources as well. Do not claim zero CPU from source comments.
9. Verify shutdown in an isolated app/service with pending writes and slow queries, accounting for the installed `TimeoutStopSec=2s`. Prove normal writes drain; document the behavior of operations that legitimately outlive that deadline. Do not increase production service limits without evidence.
10. Do not test reset/install/delete/disk/swap/network/firewall actions by clicking them on the live setup. Test their dispatch using capture helpers and disposable fixtures. Any genuinely necessary live end-to-end change must have a specific reversible scenario; report untested production actions honestly.
11. Final report must list changes, exact test commands/results, measured improvements, screenshots for visual changes, and remaining unverified behavior. A passing unit suite is not permission to claim every external utility is correct. No absolute guarantee of future bleeding-edge compatibility is possible; repeat the relevant gates after dependency updates.

## Primary documentation checked

- [Gio.Subprocess](https://docs.gtk.org/gio/class.Subprocess.html): asynchronous child lifecycle and completion APIs.
- [PyGObject asynchronous programming](https://pygobject.gnome.org/guide/asynchronous.html): supported callback approach; asyncio integration status.
- [Hyprland IPC](https://wiki.hypr.land/IPC/): current socket locations and event interface.
- [Libadwaita CSS variables](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest/css-variables.html): modern palette interface and compatibility colors.
- [Adw.ShortcutsDialog](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1.8/class.ShortcutsDialog.html): modern shortcut help API, available in the installed newer libadwaita.
- [Gtk.Accessible.update_property](https://docs.gtk.org/gtk4/method.Accessible.update_property.html): accessibility properties; use the binding's available array/value form rather than copying C varargs syntax.

For exact systemd semantics use the installed systemd 262 manual/introspection during implementation; the online freedesktop manual request returned HTTP 403 during this audit.
