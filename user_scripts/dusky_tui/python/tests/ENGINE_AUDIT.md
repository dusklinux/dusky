# TUI engine verification — 2026-10-29

Scope: all 15 engines requested by the user. This is functional and reliability
verification, not a security review or proof of correctness on every machine.

## Environment actually exercised

- `/usr/local/bin/python3`: CPython 3.15.1; `/usr/bin/lua`: Lua 5.5.1.
- systemd 262.1-1; fontconfig 2.18.3; Bash 5.3.20.
- Hyprland v0.56.0-273-g19fb395d4, with its installed companion libraries.
- Running kernel: 7.3.0-rc7-dusky-battery.
- Arch package `python` is **3.14.8-1**. The local 3.15 interpreter currently wins
  PATH lookup. The ISO must explicitly provide/select Python 3.15; package version
  and interpreter selection are not interchangeable. The running release-candidate
  kernel also does not establish the final ISO's stable kernel baseline.

## Results and reproduction

Run from `user_scripts/dusky_tui` with Python 3.15:

```sh
python3 -X dev -m unittest \
  python.tests.test_engine_second_pass python.tests.test_engine_audit \
  python.tests.test_config_engines \
  python.tests.test_engine_regressions python.tests.test_fontconfig \
  python.tests.test_systemd_engine python.tests.test_input_audit \
  python.tests.test_workspace_rules python.tests.test_shared_core -v
```

The initial 18 new audit tests were run against HEAD versions of the five initially
changed modules, supplied through `DUSKY_AUDIT_ORIGINALS`: 31 subtest/assertion
failures and two errors; only one complete test method passed. Those same 18 tests
pass after the fixes. The final audit file also adds hardware, file metadata,
privileged-helper, include, multiline-shell, and kernel-release regressions.

All 15 requested engines passed 100 isolated load/write/readback cycles each
(1,500 cycles total), using `python.tests.test_engine_matrix.exercise(module, 100)`.
System commands are simulated in that matrix; Lua evaluation/mutation uses the
installed interpreter. See `test_engine_matrix.py` for the exact fixtures.

Additional exercised cases include:

- 128 distinct writes from eight threads to each of cmdline, shell environment,
  and JSON engines, with complete readback.
- 250 randomized 60-character shell values, including quotes, dollars, backticks,
  backslashes, whitespace, and Unicode; readback checked using real Bash.
- 500 randomized TOML control-character/Unicode serialization round trips in the
  existing regression suite.
- Backdated external edits, inode replacements retaining mtime, competing engine
  instances, symlinks, permissions, invalid indices, invalid paths, deletion,
  failed fsync/rename, temporary-file cleanup, and mocked privileged atomic writes.
- Real Lua syntax checks, nested tables, dormant callbacks, include order,
  repeated call ambiguity, gestures, event handlers, missing monitor properties,
  mixed-batch failure, fractional-scale precision, NaN and infinity.
- Font XML preservation and installed fontconfig validation, toolkit fixtures,
  cross-process font edits, and systemd partial command failure/readback fixtures.

Three 10,000-setting batch workloads (load, batch write, complete readback) took
0.166 s for cmdline, 0.175 s for shell environment, and 0.045 s for JSON on this
machine. These are single workload timings, **not before/after speedup claims**.

Read-only live checks loaded 1,836 Hyprland state bindings across 24 files, 56
trackpad bindings, 59 autostart bindings, 63 monitor bindings, and 584 systemd unit
bindings. Elevated read-only boot discovery found three entries and 49 bindings.
No real service enablement, compositor writes, boot writes, firmware writes, or
toolkit changes were used to validate mutation behavior.

The original full repository suite ran 592 tests with five existing failures:
three missing matrix fixtures (`bluetooth_proximity`, `matugen_presets`, `neovim`),
a stale Waybar matrix mock, and a GSettings interactive UI test. These are outside
the requested engine set. See the final run outcome below.

The first pass ended with 196 focused/shared-core tests passing. The second pass
reviewed the actual `gitdelta FILE` output, reproduced additional failures, and
completed **216 focused tests**, all passing. That pass
executed **638 repository tests: 633 passed, five failed**. The failures are
the same five outside-scope failures listed above; there were no test errors.
The final 15-engine stress matrix again passed all 1,500 cycles. The two audit
files then contained 26 first-pass tests and 20 second-pass tests. The final
pass below records the latest totals.

## Per-engine review

| Engine | Result / change |
|---|---|
| bridged_ini | Reviewed; inherited INI behavior and commented-default precedence pass. |
| cmdline | Shared snapshots/atomic helper, locking, cache refresh, validated duplicate targets, quoted spaces, deterministic append order, and init `--` boundary. |
| flatdotconfig | Reviewed; duplicate aliases, CRLF, scope insertion, deletion and shared commit regressions pass. |
| fontconfig | Preserve existing ownership and mode, retain the creating process umask for new files, and sync the containing directory after replacement. XML, actual fontconfig, toolkit and process-lock tests pass. |
| ini | Reviewed; flags, comments, duplicate precedence, substitutions, batches and shared atomic helper tests pass. |
| json_engine | Detect stale reads, refresh cache, reject non-finite output, honor deletion, preserve literal dots in slash-separated scopes, and reuse shared commits. JSONC writes regenerate JSON. |
| lua | Existing parser/type/identity regressions pass. Hybrid staging now evaluates unrelated includes while restricting writes to the staged target, checks dependency snapshots, and reports failed post-save refresh. |
| matugen | Reviewed; repeated enable/disable, multiline strings, nested arrays, prose/comments, invalid documents and missing blocks pass. |
| monitor_engine | Stage injected fields/blocks and AST edits before replacing live source; preserve included existing bindings; reject invalid numeric edits; remove fake offline connector; use XDG primary path and report primary-save failure. Scale output retains five decimal places; upward rounding uses ceil. |
| toml | Reviewed; dates/times, inline tables, quoted keys, controls/Unicode, missing deletions and concurrency regressions pass. Writes regenerate TOML. |
| trackpad | Reviewed; real Lua gesture edits preserve callback/custom fields and dormant examples; malformed/mixed transactions fail without live-file edits. |
| systemd_boot | Loading is read-only even as root. Honor explicit missing paths, detect stale files, use atomic privileged commits, preserve loader globs, reject unknown targets, handle indexed initrd edits and repeated changes, report synthesis failures. Do not interpret binary ALL_kver images as release text or invent an absent stock-kernel image. |
| systemd | Reviewed; actual inventory reads plus simulated enablement, scope, timeout, partial failure and observed-state tests pass. |
| autostart_engine | Discover supported DRM GPU/vendor and physical disk for newly enabled dashboards; preserve existing selected commands. HUD uses its supported argument-free form. Existing event/compound-command regressions pass. |
| environment_variables | Shared atomic snapshots/locking, cache refresh, quote removal, literal shell value encoding, CRLF, indices and comments. Explicit `__VAR__` requests shell expansion. Refuse partial edits of multiline assignments. |

## Contracts for future TUI authors

- The preserved API is `load_state()`, `write_value(key, scope, value, type)`, and
  `write_batch(changes) -> (success, message, debug)`. Inspect success/message and
  reload after conflicts or reported partial/post-save failures. Empty load state
  alone does not distinguish a missing file from every read/parse failure.
- Treat file snapshots as optimistic conflict detection. Same-instance locks
  serialize threads; they are not universal process locks. Arbitrary unrelated
  processes can still race the last check and rename. Fontconfig additionally
  locks cooperating font writers across processes.
- Atomic replacement protects one file. Lua multi-file, boot entry/loader/EFI,
  monitor-primary, and font/toolkit workflows are **not all-or-nothing transactions**.
  A later operation can fail after an earlier one persisted. Readback and error
  reporting matter; perform final ISO boot/apply tests in a disposable VM.
- JSON/TOML writers regenerate documents and discard comments/formatting. Their
  dotted/slash aliases cannot represent every possible ambiguous literal key
  without an escaping protocol. They handle object/table paths, not arbitrary
  array-element addressing. `nil`/`__DELETE__` retain reserved deletion semantics.
- Kernel parameter base bindings target the last occurrence; actual kernel
  precedence varies by parameter. `:N` addresses an occurrence. Appending a
  nonexistent index is rejected unless it is the next occurrence. Parameters
  after `--` belong to init and are preserved. Keep values to one physical line.
- Shell environment values are literal by default; `__VAR__$HOME/bin` explicitly
  opts into expansion. This is a single-line assignment editor, not a full shell
  language parser. Multiline assignments require direct source edits. CRLF is
  preserved as file content; Bash itself expects LF shell source.
- Lua mutation supports identifiable configuration call sites. Ambiguous repeated
  calls and complex expressions may be refused. Monitor injection with a mixed
  batch requires edited bindings in the target file; unrelated includes are allowed
  and remain untouched. Ordinary existing included bindings remain editable. These refusals preserve source instead of guessing a target.
- Boot entry synthesis remains based on Arch/mkinitcpio and conventional boot
  mount layouts. Arbitrary custom ESP/XBOOTLDR topologies, UKI binary editing,
  firmware persistence and successful reboot were not established by fixtures.
  On this machine boot discovery requires elevated access. The final ISO needs
  VM coverage for its actual kernel presets, image paths, partition layout and EFI.
- Dashboard discovery selects the first supported GPU/physical disk in sorted
  sysfs order when adding a command. It cannot infer a user's preferred GPU/disk
  on every multi-device machine; existing explicit selections are preserved.
- Keep Python 3.15 syntax already in use (lazy imports, sentinel, frozendict,
  PEP 695 aliases, unpacking comprehensions, re.prefixmatch and math.integer).
  No compatibility paths for older Python or Xorg were added.

## Reference contracts checked

- [Python 3.15 changes](https://docs.python.org/3.15/whatsnew/3.15.html)
- [Kernel parameter quoting and init boundary](https://www.kernel.org/doc/html/latest/admin-guide/kernel-parameters.html)
- [systemd 262 loader configuration and EFI overrides](https://www.freedesktop.org/software/systemd/man/latest/loader.conf.html)
- Installed `bootctl`, `systemctl`, `hyprctl`, `fc-list`, `fc-cache`, Lua help/version
  output; existing frontend engine factory and directly relevant callers/tests.

## Before/after assessment

Scores below are a measured pass-rate score for the original 18 new regression
methods, rounded to 1–100, not a percentage probability of correctness.

| Area | Before score | After score | Evidence / assessment |
|---|---:|---:|---|
| Reproducing regression methods | 6 | 100 | 1/18 complete methods passed before; 18/18 after. Observed improvement on those fixtures. |
| Runtime performance gain | N/A | N/A | Large-batch absolute timings recorded; no before/after benchmark. Gain unverified. |
| Commit-path duplication | N/A | N/A | Cmdline/env/JSON/boot reuse shared atomic I/O; boot reuses cmdline mutation. Concrete simplification; no subjective score assigned. |

The verified behavior is substantially more reliable on the exercised cases.
Universal future compatibility, firmware persistence, and correctness on every
hardware/boot layout remain unverified.

## Second-pass diff and intention review

`gitdelta FILE` was run separately for all ten files from the first pass, then
again for the final tracked changes including `lua.py`. The new second-pass test
file was read directly because it is not yet in the index. Bare `gitdelta` was
never invoked. No staging, commits, resets, or publication were performed.
Existing staged first-pass engine versions were copied read-only into a temporary
directory to reproduce failures without reverting the work tree.

The 20 second-pass tests pass against the corrected code. Against those saved
first-pass versions, 17 failed and three passed. This caught actual regressions
introduced by the first rewrite as well as older corner cases that survived it.

| File / area | Review decision and demonstrated reason |
|---|---|
| cmdline | Normalize missing base and `:1` aliases to the same target. Previously a batch could append two parameters for one occurrence. |
| shell environment | Retain the literal/explicit-expansion contract. Additional real Bash tests confirm single quotes, backslashes and leading/trailing spaces round-trip. |
| JSON | Retain shared atomic I/O. Add explicit evidence that serialization fails before any temporary file is created; the changed shared-core fsync test still checks descriptor closure and original-file preservation. |
| fontconfig | **Undo the unnecessary first-pass forced 0644 mode for new files.** Preserve the original umask behavior; retain ownership preservation and directory fsync. A restrictive-umask test failed before this correction. |
| monitor | Keep `auto` as a Lua string; retain numeric deletion and virtual enabled-to-disabled deletion mapping. Snap scale against an explicitly requested resolution even when mode and scale arrive in the opposite order. Preserve unrelated includes during field injection. |
| Lua hybrid helper | The target is staged; includes may be evaluated but cannot be written by the hybrid mutator. Requested cross-file edits fail without changing either source. Check dependency snapshots before and after staging, and report a failed state refresh after persistence. |
| boot state | Capture snapshots from the actual reads under the instance lock. A fixture changes loader.conf between parsing and later path selection: that stale load now prevents a write. Use the shared kernel parser so arguments after `--` do not appear as kernel state. |
| boot writes | Prevalidate the entry before a loader commit, reject conflicting whole-options/parameter edits and ambiguous title slugs, normalize metadata aliases, avoid loader writes for a target-only selection, and preserve the previous selection if target synthesis fails. Report firmware-sync failure as a failure after the configuration was saved. |
| autostart | Detect dashboard commands with shell-quoted script paths, including home paths containing spaces. Runtime device discovery remains intentional; existing explicit selections remain untouched. |

The remaining seven engines are unchanged and continue to pass their targeted
tests and 100-cycle matrix fixtures. Lua received only the targeted hybrid-helper
changes described above.

| Second-pass area | Before score | After score | Basis |
|---|---:|---:|---|
| New regression-method pass score | 15 | 100 | 3/20 passed on the saved first-pass versions; 20/20 now. Observed improvement on these cases only. |
| Runtime speedup | N/A | N/A | No comparative benchmark; no speedup claimed. |
| Intention review | N/A | N/A | One unnecessary permission change reversed; remaining changes tied to code comparisons and reproducing fixtures. |

These are test pass-rate scores, not guarantees about every future configuration,
firmware implementation, device, or Python/Hyprland release.

## Final complete-diff verification

All 12 edited files were passed individually to `gitdelta FILE`, including the
now-tracked second-pass test file. The complete initial 3,369-line patch was read
in full, including deleted code, and subsequent corrections were reviewed in
full. The file-specific form of gitdelta was used throughout; it does not invoke
the manifest-staging branch.

The final review reproduced six additional shell parsing failures in three test
methods, using real Bash as the reference:

- `X=#ff0000` has a literal hash value; KEY= already starts the shell word.
- An escaped blank remains part of the value, including immediately before a
  literal hash or before the whitespace separating an actual comment.
- Unquoted U+00A0 and U+2028 are shell word characters, not formatting to trim.

The parser now tracks unquoted/unescaped word boundaries and the last significant
value position. All six cases match real Bash; an unrelated setting can be saved
without altering the original assignment. A separate deterministic 1,000-case
sweep of concatenated, single-quoted, double-quoted and escaped assignments also
matched real Bash (seed 3152026).

The environment engine's original shebang and the cmdline/environment public type
annotations were restored. A duplicate environment-value validation and an
unreachable cmdline alias fallback were removed; whole-document preflight and
canonical occurrence identities already establish those conditions. The parser
remains linear in source length. No runtime speedup is claimed.

Final verification: **219 focused/shared-core tests passed**; all **1,500 isolated
engine cycles passed**; all 15 engine files and edited test files syntax-checked;
Git diff whitespace checks passed. The two audit files contain 26 original tests
and 23 second/final-pass tests.

The final complete repository run executed **641 tests in 82.870 seconds: 636
passed and five failed**. The failures remain exactly the same pre-existing
outside-scope failures listed above; there were no test errors.

SHA-256 byte hashes for all 11 edited Python files were recorded before the final
test runs and matched afterward. This confirms that the reviewed/tested source
bytes are the bytes left in the workspace. The report itself was reviewed and
rendered with gitdelta again after its final update.

| Final-pass area | Before score | After score | Evidence |
|---|---:|---:|---|
| Second/final regression-method pass score | 87 | 100 | Before the final correction: 20/23 methods passed, with six failures across three new methods. Afterward: 23/23 passed. Observed improvement on these fixtures only. |
| Performance gain | N/A | N/A | No before/after benchmark; no gain claimed. |
| Code-path duplication | N/A | N/A | Two redundant checks/branches removed after verifying the earlier validation and normalization. |

These results establish improvements on the exercised contracts; they do not
establish successful boot/firmware writes on every ISO layout or universal future
compatibility.
