# Dusky TUI: benchmarks and startup diagnostics

Start here. This folder is self-contained; no previous AI plan or RAM workspace is required. Production code is adjacent under `../../frontend`, `../../engines` and `../../main`; the UFW schema is `../../../../network_manager/tui_firewall.py`.

The latest shared-framework audit is [audit-20261003-second-pass.md](audit-20261003-second-pass.md); [audit-20261003.md](audit-20261003.md) preserves the first-pass findings. The framework requires **Python 3.15 or newer** and uses native `lazy import` / `lazy from` syntax and `re.prefixmatch()`. It has no older-Python compatibility path. Entry points remain `python3`; until the system upgrade, activate the 3.15 venv for every command below. The system interpreter and package installation are unchanged.

## Contents

- [implementation.md](implementation.md): current architecture, contracts and verification limits.
- [final-checklist.md](final-checklist.md): final architectural checklist and fresh full-suite verification, 2026-09-29.
- [plan.md](plan.md): investigation priorities for further optimization; hypotheses require measurements.
- [benchmark_startup.py](benchmark_startup.py): fresh-process profiler of the actual launcher.
- [compare_startup.py](compare_startup.py): alternating before/after process measurements, including raw samples, quartiles, source hashes and separately recorded first invocations.
- [fixture.py](fixture.py): root-free functional smoke workload using a temporary INI file.
- [baseline-ufw-8w.json](baseline-ufw-8w.json): unmodified five-run reference for the recorded production snapshot, dated 2026-09-28.
- [baseline-summary.json](baseline-summary.json): current-only reference summary and source identities.
- [harness-verification.json](harness-verification.json): fresh one-run smoke and privileged UFW checks of this harness; not a matched 8 W comparison.

The raw reference preserves original recorded absolute paths as provenance. Those paths are not runtime dependencies. Its profiler predates this folder's launcher-default and source-fingerprint improvements, and the production sources have changed since that reference. The dated architecture and checklist documents describe their original verification scope. Always capture a fresh baseline before changing production code; the reference is one machine/workload, not a performance target for every installation.

## Python 3.15 diagnostics

Use the installed environment with matching TUI dependencies:

```sh
source "$HOME/.venvs/315/bin/activate"
python3 --version
cd "$HOME/user_scripts/dusky_tui/python/tests/benchmarks"
python3 -m unittest discover -s .. -p 'test_*.py'
python3 -X importtime=2 ../../main/main.py --help 2>/tmp/dusky-imports.log
python3 -m profiling.tracing -o /tmp/dusky-calls.pstats ../../main/main.py fixture.py --export-docs
python3 -m profiling.sampling run -a --flamegraph -o /tmp/dusky-startup.html benchmark_startup.py fixture.py --worker --result /tmp/dusky-profiled-worker.json --tabs 1,2 --observe 0.5 --origin-ns "$(python3 -c 'import time; print(time.perf_counter_ns())')"
```

`importtime=2` includes cached imports; `profiling.tracing` attributes function-call costs; `profiling.sampling` provides Python 3.15's Tachyon sampler. These are diagnostic runs, not speed samples. The sampling command invokes the harness worker directly so the actual TUI is the sampler's child. On the audited host, `--subprocesses` reported starting a child profiler but produced no child report; a separate attach probe confirmed process-memory permission denial. Direct worker sampling succeeded without changing system permissions. Sampling can miss transient stacks; consult its error rate before interpreting sample proportions. Inspect profiler help on the installed build. The native interpreter observed during the audit has PGO/LTO, a GIL, no JIT, and was built without frame pointers; do not infer native-stack profiling capabilities from upstream defaults.

For a saved unmodified launcher tree, run matched process comparisons:

```sh
python3 compare_startup.py --before /path/to/saved/tree/python/main/main.py --runs 31 --output /tmp/dusky-comparison.json
```

The saved tree needs its original frontend and engines importable beside the launcher. Compare with the same interpreter and dependency versions. The tool alternates invocation order and deliberately warms caches; its first invocation is not a cold-cache measurement. Use separate empty `XDG_CACHE_HOME` directories for fresh Dusky bytecode-cache runs, without claiming that the OS filesystem cache is cold.

Hidden help Markdown now mounts on first opening. Programmatic callers must await `app.action_toggle_help()`; normal Textual key/actions already support async actions. Backends discovered in deferred schemas must be registered before state collection. Discovery waits for positional save callbacks before replacing rows or publishing state, and unit reads are scoped to the relevant engine/target. `LazyEnginePool` is a mapping rather than a `dict` subclass; `initialized_values()` inspects constructed backends without instantiating unused ones. Unknown lookups raise `KeyError`; registration, iteration and length do not construct engines. `get()` uses its default only for unregistered keys and propagates errors from registered engine constructors/binding. Constructors and blocking reads stay on their requesting workers; `set_app` binding runs once on the UI thread. The factory lock is released before waiting on the UI, so simultaneous UI lookup cannot deadlock the worker's binding callback. A failed headless reset batch reports failure and is not automatically replayed as individual writes.

## Run the benchmarks

From an installed Dusky tree:

```sh
cd "$HOME/user_scripts/dusky_tui/python/tests/benchmarks"
python3 benchmark_startup.py fixture.py --runs 1 --tabs 1,2 --observe 0.5 --timeout 60 --output /tmp/dusky-smoke.json
sudo python3 benchmark_startup.py "$HOME/user_scripts/network_manager/tui_firewall.py" --runs 5 --tabs 1,2,14 --observe 3.2 --timeout 180 --label '8 W configured; headless; uncontrolled caches' --output /tmp/dusky-ufw-current.json
```

Set and confirm the intended power envelope yourself before collecting comparisons. The profiler records available powercap limits; it does not change them or prove that actual power consumption is 8 W. Use the same firewall state, power settings, viewport, schema, navigation, observation duration and background workload for comparisons. It performs read/inspection and tab navigation, with no simulated save/reset actions; schema startup behavior still runs normally. UFW requires root, and the benchmark deliberately rejects launcher sudo re-execution. Inspect every run's errors, stderr and external command return codes: nonzero commands may be recorded without failing the workload.

The default launcher is adjacent `../../main/main.py`; `--launcher PATH` overrides it. In isolated checkouts, inspect `environment.source_sha256` paths to confirm the schema has not imported another installation. Schemas can alter `sys.path`. Run the harness's `--help` for all options. Reports should go outside this handoff folder unless deliberately replacing the reference with a documented new measurement.

For real terminal output, run from an interactive kitty terminal on Wayland, adding `--mode terminal` to the smoke or UFW command. Do not redirect terminal output; use `--output` for JSON. Record actual `viewport_width`/`viewport_height`, terminal version, scale and synchronized-output detection. Requested 120×40 does not guarantee that viewport. To assess opening artifacts, also capture and inspect opening-frame video; this profiler cannot certify optical flicker or tearing.

## Interpret the report correctly

- `first_compositor_callback_ms`: first eligible Textual display callback, measured from the parent's spawn request. Headless mode delivers no terminal frame.
- `first_driver_flush_return_ms`: terminal mode only; driver return is not presentation acknowledgement.
- `active_data_refresh_ms`: initial-tab dependency/content readiness followed by a refresh callback. It is not measured keyboard-input-to-pixel TTI.
- `boot_refresh_ms`: all startup/discovery complete followed by refresh. It can occur after usable initial content.
- `compose_step` spans measure generator execution only, excluding Textual DOM mounting/layout between yields. Widget counts are counts, not allocation bytes.
- Import spans are inclusive and overlap. Use a separate `python3 -X importtime …` run for attribution; tracing itself changes timing. No separate warm-import benchmark is provided.
- Switch durations include programmatic activation, readiness and refresh callbacks. Read `warmed_before`, `retained_content_before` and `dirty_collector_before`; clean revisits may reuse cached system data.
- `--observe` examines idle callbacks after boot, not idle before first display. Hidden Rich polling should be absent; generic supplied Widgets manage their own timers.
- CPU time and peak RSS cover the whole worker workload, including observation, navigation and shutdown. They are not startup-only metrics.
- Fresh interpreters do not imply cold filesystem/bytecode caches. Milestones and inclusive spans must not be summed. Five runs justify sample summaries, not tail-latency guarantees.
- Readiness instrumentation uses private framework/Textual state. Revalidate it after upgrades or lifecycle changes; arbitrary Widget readiness has no collector signal. Headless timeout kills the process group; terminal timeout kills only the child.

## Verification when changing production

```sh
python3 -m unittest discover -s "$HOME/user_scripts/dusky_tui/python/tests" -p 'test_*.py'
```

The October 3 audits expanded the suite from 192 to 217 tests, including shared mapping/concurrency, constructor-error propagation, CLI round trips and failed-batch handling, export diagnostics, deferred targets and pending saves, unit-target isolation, lazy help, null values and rendering regressions. See the latest audit report for final test results. Run focused tests while iterating, then the complete suite for a production change. Check performance under the configured envelope and inspect real terminal rendering separately.
