# Shared TUI second-pass audit — 2026-10-03

Green Light for the tested Python 3.15 shared framework. This pass reviewed the first-pass changes and their callers, corrected six reproduced defects, and verified startup against the preserved first-pass source. See [the first-pass report](audit-20261003.md) for the earlier architecture review and original optimization measurements.

## Runtime and syntax

The user explicitly selected Python 3.15-only code and accepted that existing system-Python launchers will not work until their interpreter is upgraded. All three shared production files now use native `lazy import` / `lazy from` statements. Removed `__lazy_modules__` compatibility declarations and replaced prefix matching with `re.prefixmatch()`. Whole-value validation uses `fullmatch()`. Existing `python3` entry points remain unchanged; no system interpreter, symlink or pacman package was replaced.

Verification used `$HOME/.venvs/315/bin/python`, Python **3.15.0rc3**, with Textual **8.2.8** and Rich **15.0.0**. Native syntax and import behavior were checked on the installed runtime. A fresh UI import does not load the hidden Markdown widget. There are no unused module-level imports in the three production files according to the focused AST check.

The interfaces follow the documented [Python 3.15 lazy-import syntax](https://docs.python.org/3.15/reference/simple_stmts.html#lazy-imports) and [prefix matching API](https://docs.python.org/3.15/library/re.html#re.prefixmatch). Latest syntax was adopted where it expresses the existing behavior clearly; unrelated new features were not added merely for novelty.

## Corrections

| Path | Before | After |
|---|---|---|
| Engine mapping | Inherited `get()` swallowed a registered constructor's `KeyError` and returned its default | Only unregistered keys use the default; constructor/binding errors propagate, and failed construction can be retried |
| Deferred discovery | State/readiness could publish while row replacement waited for positional save callbacks | Waits asynchronously for pending saves before collecting/applying rows; checks again after I/O; rereads invalidated prefetched state |
| Unit loading | Startup used shared unit lists; discovery could mix targets and omit rows retained in other tabs | Reads use the relevant engine's index, and discovery uses the prospective schema filtered by engine/target |
| RGB rendering | A component beyond Python's integer-string limit raised `ValueError` | Float conversion followed by clamping handles oversized numbers, long zero prefixes and Unicode decimal digits |
| CLI default reset | Failed batches were replayed as individual writes and could then report success | Reports the batch failure with exit status 1; does not duplicate side effects; retains per-item results for engines that provide them |
| Export diagnostics | The escalation notice went to stdout, contaminating unbuffered exports | Notice goes to stderr; the export data stream remains separate |

The engine factory lock, single-instance construction, UI-thread binding, lazy shutdown, clone fast paths and on-demand help architecture were retained. The discovery wait adds no sleep when saves are absent; while saves are pending it yields to the event loop every 50 ms. It does not block UI input. Failure reporting deliberately does not claim that a partially failed batch rolled back.

## Empirical verification

Added seven meaningful regression tests for the six defects. All seven expose failures on the hash-verified first-pass source; the CLI failure test has two subcases. That focused baseline ran 21 tests with six failure and two error outcomes. The saved first-pass full suite had 210 passing tests. The final source passes **217 tests in 48.266 s** on Python 3.15. Syntax, source-identity, native lazy-import, deferred Markdown and Git whitespace checks passed.

The escalation test intercepts re-execution; it never invokes sudo. Configuration writes and reset-failure fixtures use temporary files or in-memory engines. A terminal-driver smoke completed without recorded errors at the actual 80×24 PTY viewport. Physical display presentation and every privileged/hardware backend were not exhaustively exercised.

### Matched startup results

Same interpreter, dependencies and isolated INI fixture for both variants. Fresh process measurements alternate order, with 31 samples per variant after three warmup pairs. Lifecycle measurements use 11 warm pairs after three warmup pairs, plus seven fresh-bytecode pairs, at 120×40 with tabs 1/2 and a 0.5 s observation interval. All 36 measured lifecycle records completed without recorded errors; the six additional warmup records are also retained. Recorded SHA-256 identities match the measured sources.

| Median measurement | First pass | Final second pass | Assessment |
|---|---:|---:|---|
| CLI help | 87.10 ms | 86.70 ms | Unchanged within 0.5% |
| Documentation export | 118.69 ms | 118.42 ms | Unchanged within 0.3% |
| Core import | 57.19 ms | 57.30 ms | Unchanged within 0.2% |
| UI import | 325.79 ms | 325.42 ms | Unchanged within 0.2% |
| First headless display callback, warm | 533.25 ms | 534.60 ms | Unchanged within 0.3% |
| Boot refresh, warm | 633.03 ms | 634.62 ms | Unchanged within 0.3% |
| First headless display callback, fresh bytecode | 1371.07 ms | 1371.10 ms | Unchanged |
| Boot refresh, fresh bytecode | 1471.83 ms | 1471.60 ms | Unchanged |
| Whole-workload CPU, warm | 913.27 ms | 913.48 ms | Unchanged |
| Whole-workload peak RSS, warm | 49560 KiB | 49480 KiB | Unchanged |
| Widgets at boot | 45 | 45 | Unchanged |

The observed differences do not justify an additional performance gain or regression claim. Rounded benchmark-relative startup scores are **100→100**; reliability scores are **N/A→N/A**, because seven failing regression tests becoming passing tests do not define a universal quality scale. The concrete reliability result is better.

Host timings varied between sessions. Compare paired variants within this report, not absolute values against the earlier report. OS caches and power/background load were not controlled. Fresh bytecode isolates Dusky's `XDG_CACHE_HOME`, not the entire filesystem cache. CPU/RSS include navigation, observation and shutdown; callbacks are not pixel presentation or keyboard-to-pixel latency.

### Diagnostics and artifacts

Exercised final-source `-X importtime=2`, call tracing and direct-worker Tachyon sampling. The interactive flamegraph contains shared TUI stacks. Sampling captured 1700 iterations and reported a 30.82% error rate, so it supports exploratory attribution rather than precise sample-percentage claims. Async call-tracing cumulative totals overlap and include event-loop waits. Profiles and the terminal smoke were separate from speed samples.

Sources, test logs, raw measurements, the comparison driver, profiles and `startup.html` are saved under `${XDG_STATE_HOME:-$HOME/.local/state}/dusky/audits/20261003-second-pass`. `manifest.json` records artifact hashes. The first-pass source was recovered from its durable snapshots and checked against its original SHA-256 identities. Only the shared files, regression tests and audit documentation changed; existing staging was left untouched. Revalidate the RC runtime and Textual instrumentation against the final ISO packages before relying on release-specific behavior.
