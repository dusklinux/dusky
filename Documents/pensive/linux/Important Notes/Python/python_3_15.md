# Python 3.15 new features and modernization reference

Target CPython 3.15+ on the Linux ISO. Use the latest documented syntax, standard-library APIs, and implementation methods wherever they fit the script being updated. This is a feature reference for modernization, performance, and efficiency.

**Deployment baseline:** treat CPython **3.15.0rc3** and its documented 3.15 feature set as the final modernization target for this project, as instructed. Use these features now; do not defer adoption because of the rc label or add compatibility paths for older Python. Build-dependent capabilities such as JIT/free threading and filesystem support still depend on the actual target environment.

Primary references: [What's New in Python 3.15](https://docs.python.org/3.15/whatsnew/3.15.html), [3.15 changelog](https://docs.python.org/3.15/whatsnew/changelog.html#changelog).

## Instructions for the modernization agent

- Prioritize frequently launched commands, interactive startup, long-running services, hot loops, and significant memory or data-copy costs. Consider execution frequency, user-visible latency, and total resource use.
- Skip speculative performance refactors of one-time installation scripts and rarely used cheap tools. Still fix relevant compatibility failures and concrete reliability problems.
- In scripts selected for modernization, adopt every applicable current syntax/API improvement. Prefer explicit `lazy import`, built-in `sentinel`/`frozendict`, unpacking comprehensions, `math.integer`, `re.prefixmatch`, and current typing syntax over older equivalent patterns. Clearer, simpler code is a valid benefit even without a speedup; performance claims require measurements.
- Fix 3.15 incompatibilities, replace outdated patterns with current equivalents, and optimize consequential costs. Preserve semantics: choose a feature because it fits the operation, and keep necessary initialization, mutation, ownership, and concurrency behavior.
- Follow `AGENTS.md` and the requested scope. Inspect the selected scripts and directly relevant callers/dependencies. Distinguish source-level modernization from interpreter gains that already occur without edits.

## Check and measure only what matters

Confirm the interpreter used by the actual launcher/shebang/venv, not just the shell default:

```bash
command -v python3
python3 -VV
```

For a startup candidate, use `python3 -X importtime script.py` and repeated fresh-process timings of a representative command. Compare medians; distinguish cached filesystem runs from genuinely cold runs. For a memory candidate, compare peak RSS on realistic inputs. Profile CPU work only when it is consequential. Do not require every diagnostic for every script.

Useful targeted checks: `python3 -X dev -W default script.py` for runtime warnings, and `python3 -X warn_default_encoding script.py` for implicit encodings. Run only safe, representative invocations; syntax-checking does not validate behavior.

Validate syntax/API modernization with appropriate functional checks; it does not require a speedup to be useful. For performance changes, measure representative workloads and retain improvements beyond measurement noise. Record before/after results and report unmeasured performance claims as unverified.

## New language features and efficient data handling

### Explicit lazy imports (PEP 810)

Python 3.15 supports explicit module-level imports:

```python
lazy import json
lazy from pathlib import Path
```

Loading happens on first use. Prefer explicit top-level `lazy import`/`lazy from` for dependencies needed only on deferred execution paths. Replace function-local imports or custom lazy-loader scaffolding whose sole purpose is delayed initialization with this syntax. This is particularly useful for frequent commands with optional subcommands, export formats, or GUI paths. If every invocation immediately uses it, laziness mostly shifts the same work and may add overhead. A one-time installer is not a worthwhile lazy-import optimization target.

Preserve eager imports for required early dependency checks, import-time registration/configuration, and dependencies first needed during finalization or shutdown. Loading and errors occur later; changes to environment or import paths before first use can affect behavior. Test an unused path, first use, failure timing, and relevant initialization/cleanup. Measure end-to-end latency too: faster startup can hide a slower first action. Lazy reification cycles can raise `ImportCycleError` (an `ImportError` subclass).

Explicit lazy imports are forbidden inside functions, classes, and `try`/`except`/`finally` blocks, and cannot use wildcard or `__future__` imports. Keep function-local imports where genuinely dynamic selection or local import-error handling requires them. Keep genuinely type-only imports under `TYPE_CHECKING` when runtime loading is unnecessary. Runtime annotation inspection (including `typing.get_type_hints()`) can force lazy imports; check reflection-dependent callers and tool support for the new syntax.

Process modes are `normal` (honors explicit lazy imports) and `all` (makes eligible ordinary imports lazy). `python3 -X lazy_imports=all script.py` or `PYTHON_LAZY_IMPORTS=all` selects the latter. Applications can use `sys.set_lazy_imports()` and `sys.set_lazy_imports_filter(callback)` to choose eager boundaries; the callback receives `(importing_module, imported_module, fromlist)` and returns `True` to allow laziness or `False` to force eager loading. Libraries should express their own lazy imports without changing application-wide policy. `sys.lazy_modules` is diagnostic metadata; `types.LazyImportType.resolve()` explicitly resolves a proxy when tooling or a warmup boundary needs it.

```python
# Replace an import inside export_report() used solely to defer startup cost:
lazy from report_backend import render

def export_report(data):
    return render(data)
```

Sources: [import statement](https://docs.python.org/3.15/reference/simple_stmts.html#the-import-statement), [runtime controls](https://docs.python.org/3.15/library/sys.html#sys.set_lazy_imports).

### Consume a bytearray without copying the whole payload

```python
# Before: copy, then discard mutable contents
payload = bytes(buffer)
buffer.clear()

# After: consume the buffer
payload = buffer.take_bytes()
```

In CPython, taking **all** bytes is zero-copy. Taking a prefix with `buffer.take_bytes(n)` while retaining a remainder requires a copy; it is not a general zero-copy slicing API. If the remainder should be discarded, truncate first, then take all bytes.

Also consider `return buffer.take_bytes()` when a function returns `bytes(buffer)` and discards its private buffer. This changes the buffer's contents. Check aliases, exported memoryviews, retry behavior, and exception paths. Negative `n` indexes from the end; out-of-range sizes raise `IndexError`, unlike clamped slices. Guard a failed delimiter search before using its result as a size. Benchmark realistic payload sizes.

Source: [bytearray.take_bytes](https://docs.python.org/3.15/builtins/stdtypes.html#bytearray.take_bytes).

### Combine checksums without rereading data

`zlib.crc32_combine(crc1, crc2, len2)` and `adler32_combine(adler1, adler2, len2)` combine checksums of consecutive chunks. `len2` is the second chunk's byte length. Useful when checksums already exist; avoid concatenation or another full scan. Verify chunk order, lengths, and equivalence to checksumming the complete payload.

Source: [zlib](https://docs.python.org/3.15/library/zlib.html).

## Preferred current syntax and APIs

Use these wherever their semantics fit a script selected for modernization. Clarity and reduced boilerplate count as improvements; do not label them speedups without measurement:

| Feature | Appropriate use and limitation |
|---|---|
| `frozendict(...)` | Prefer the built-in for immutable mappings, snapshots, and hashable mapping keys. Replace equivalent custom frozen-mapping helpers. Shallow immutability; contents must be hashable for hashing. It is not a `dict` subclass or a live view of a mutable mapping. |
| `json.loads(..., object_pairs_hook=frozendict, array_hook=tuple)` | Immutable JSON object/array structure when consumers do not mutate it. |
| `MISSING = sentinel("MISSING")` | Clear missing-value marker with identity comparison (`is`). Define once; module-global matching name supports pickling. Prefer this built-in over ad-hoc sentinel objects/classes for clear representation, typing, and identity-preserving copying. |
| `[*items for items in groups]`, `{*items for items in groups}`, `{**d for d in mappings}`, `(*items for items in groups)` | Prefer unpacking comprehensions/generator expressions for direct flattening and merging. Later duplicate dict keys win; the generator form stays lazy. Measure hot paths rather than assuming a speedup. |
| `asyncio.TaskGroup.cancel()` | Normal early group termination without injecting a synthetic exception. Cancels unfinished children and the group body; group exit suppresses its internal cancellation, not unrelated failures. Test cleanup and external cancellation. |
| `threading.serialize_iterator(iterable)` | Serialize advancement of a shared iterator; items are distributed among consumers. Adds synchronization overhead. |
| `@threading.synchronized_iterator` | Make an iterator-producing callable return serialized iterators. |
| `threading.concurrent_tee(iterable, n=2)` | Each consumer gets the full stream. Each derived iterator needs one consumer at a time; lagging consumers retain buffered items. |

Sources: [built-in types](https://docs.python.org/3.15/builtins/stdtypes.html), [json](https://docs.python.org/3.15/library/json.html), [sentinel](https://docs.python.org/3.15/builtins/functions.html#sentinel), [unpacking comprehensions](https://docs.python.org/3.15/whatsnew/3.15.html#pep-798-unpacking-in-comprehensions), [TaskGroup](https://docs.python.org/3.15/library/asyncio-task.html#task-groups), [threading](https://docs.python.org/3.15/library/threading.html).

### Filesystem, configuration, and persistent-data candidates

| API | Use when it replaces existing work or clarifies an actual contract |
|---|---|
| [`os.makedirs(..., parent_mode=...)`](https://docs.python.org/3.15/library/os.html#os.makedirs), [`Path.mkdir(..., parents=True, parent_mode=...)`](https://docs.python.org/3.15/library/pathlib.html#pathlib.Path.mkdir) | Set modes for newly created intermediate directories without a separate walk or changing the process-wide umask. The umask still applies; existing directory modes stay unchanged. Do not replace chmod logic requiring exact final modes. |
| [`os.path.realpath(..., strict=...)`](https://docs.python.org/3.15/library/os.path.html#os.path.realpath) | `ALLOW_MISSING` allows missing components; `ALL_BUT_LAST` allows only the last component to be missing. Both resolve symlinks and raise other errors. Prefer the matching mode over manual partial-resolution scaffolding for paths to be created. |
| [`os.statx()`](https://docs.python.org/3.15/library/os.html#os.statx) | Linux metadata such as birth time or mount ID. Requested fields are not guaranteed: inspect `stx_mask` and handle unavailable fields (`None`). Verify build/filesystem support; use ordinary `stat()` when its fields are sufficient. |
| [`tomllib`](https://docs.python.org/3.15/library/tomllib.html) | Now parses TOML 1.1, including multiline inline tables. Can replace a third-party parser used only for reading supported TOML. It does not write TOML or preserve formatting; preserve stricter format requirements of other consumers. |
| [`urllib.parse`](https://docs.python.org/3.15/library/urllib.parse.html) | `missing_as_none=True` distinguishes absent from empty URI components; `keep_empty=True` can preserve empty delimiters when rebuilding. Adopt only where these distinctions matter to round trips or a protocol. |
| [`dbm` reorganize](https://docs.python.org/3.15/library/dbm.html), [`shelve`](https://docs.python.org/3.15/library/shelve.html) | `dbm.dumb`/`dbm.sqlite3` and shelves gain compaction; schedule it after significant deletions, not every write. Check backend support, I/O cost, and required free space. Shelve custom serializers can replace wrappers: supply both `serializer(value, protocol)` and `deserializer(bytes)`, preserving the stored format. |

### Additional 3.15 facilities

| Feature | Modern use |
|---|---|
| `unicodedata.iter_graphemes(text)` | Replace custom grapheme splitting for user-perceived characters; graphemes differ from code points and terminal display columns. Unicode data is updated to 17.0.0. |
| `math.isnormal`, `issubnormal`, `fmax`, `fmin`, `signbit` | Replace equivalent IEEE-754 helpers; preserve NaN and signed-zero semantics. |
| `array`/`memoryview` formats | Use `Zf`/`Zd` for complex float/double data; `array` also supports half-float `e`. `struct` prefers `Zf`/`Zd` over soft-deprecated `F`/`D`. |
| `Counter` symmetric difference | Use `a ^ b` for the multiset symmetric difference instead of equivalent manual count logic. |
| `bytes.replace(..., count=n)` | Use the supported keyword to make the replacement limit explicit. |
| Expanded `__slots__`; generic `slice` | 3.15 permits more slot configurations, including tuple-derived classes, and `slice` subscription for typing. Adopt where the class/API contract benefits; slotting changes attribute and inheritance behavior. |
| `mmap.mmap.set_name()` | Name anonymous Linux mappings when memory attribution is useful. |
| `wave` floating-point WAVE | Use stdlib IEEE float WAVE support and format getters/setters where they replace equivalent custom handling. |
| `ssl` group/ciphersuite/signature APIs | Use the current API for existing TLS configuration needs; available operations depend on the linked OpenSSL version. |

Sources: [unicodedata](https://docs.python.org/3.15/library/unicodedata.html), [math](https://docs.python.org/3.15/library/math.html), [array](https://docs.python.org/3.15/library/array.html), [struct](https://docs.python.org/3.15/library/struct.html), [Counter](https://docs.python.org/3.15/library/collections.html#collections.Counter), [wave](https://docs.python.org/3.15/library/wave.html), [ssl](https://docs.python.org/3.15/library/ssl.html).

### Integer math, regex, and typing

Prefer `from math.integer import comb, factorial, gcd, isqrt, lcm, perm` for exact integer operations. Prefer `re.prefixmatch()`/`Pattern.prefixmatch()` for beginning-of-string matching. These are the current names; retaining old aliases is unnecessary for this 3.15-only target. Their soft deprecation is not a runtime performance difference.

Use new `TypedDict` schemas and `TypeForm` where they describe the actual API:

```python
from typing import Any, TypeForm, TypedDict

class Point(TypedDict, closed=True):
    x: int
    y: int

class Metrics(TypedDict, extra_items=float):
    name: str

def deserialize[T](target: TypeForm[T], value: Any) -> T:
    ...  # existing conversion implementation
```

`closed=True` rejects additional keys statically; `extra_items` describes their value type. `TypeForm[T]` accepts evaluated type expressions, including unions and parameterized types. These do not provide runtime validation. `@typing.disjoint_base` expresses disjoint class hierarchies. `TypeVarTuple` accepts bound/variance keywords, but specification/tool support must match the semantics being used.

Also use modern syntax already available at this baseline: `list[T]` and `T | None`, `type Alias = ...`, and `def f[T](...)`/`class C[T]` in place of equivalent legacy typing boilerplate. These predate 3.15; preserve runtime alias/reflection behavior when converting.

Sources: [math.integer](https://docs.python.org/3.15/library/math.integer.html), [re](https://docs.python.org/3.15/library/re.html), [typing](https://docs.python.org/3.15/library/typing.html).

## Gains that normally need no source edit

CPython 3.15 improves Base64/Base32/Base85 codecs, `csv.Sniffer`, buffered-file line iteration, hex encoding/hash digests, allocator behavior, and interpreter internals. Some gains depend on the build and hardware. On supported Linux builds, `Popen.wait(timeout=...)` uses event-driven waiting instead of the old busy loop. Preserve simple stdlib calls; remeasure historical workarounds before replacing or removing them. Continue draining subprocess pipes appropriately (`communicate()` when needed).

Upstream benchmark ratios describe particular operations/builds, not whole-script gains. Frame pointers improve native profiling on supported builds; they are not a reason to rewrite Python source.

Sources: [3.15 optimizations](https://docs.python.org/3.15/whatsnew/3.15.html#optimizations), [changelog](https://docs.python.org/3.15/whatsnew/changelog.html#changelog) (for buffered I/O and hex-encoding changes).

## Compatibility checks: follow the APIs the script actually uses

| Area | Relevant 3.15 change |
|---|---|
| Text encoding | UTF-8 mode is enabled by default, but can be disabled. Keep explicit encodings for defined file formats; use `encoding="locale"` only for intentional locale text. |
| `strptime()` | `%d` without a year raises `ValueError`; `%e` without a year is deprecated. Supply a deliberate year, accounting for leap days. |
| `sqlite3` | `connect()` parameters after `database` are keyword-only. Leading arguments to `create_function`, `create_aggregate`, and selected callbacks are positional-only; check the exact method signature. |
| Import loaders | Removed `load_module()` support: use the modern loader protocol (`exec_module`, with `create_module` as required). |
| Removed APIs | Check actual uses of `sre_compile`/`sre_constants`/`sre_parse`, `co_lnotab` (use `co_lines()`), CGI support, `PurePath.is_reserved`, `glob.glob0`/`glob.glob1`, `ctypes.SetPointerType`, `typing.no_type_check_decorator`, WAVE marker methods, and `sysconfig.is_python_build(check_home=...)`. `RLock` no longer accepts arbitrary arguments. |
| Typing construction | Keyword-field `NamedTuple("Name", x=int)` is removed; use class syntax or a list of field pairs. Empty `TypedDict("Name")`/`TypedDict("Name", None)` must become `TypedDict("Name", {})` or class syntax. |
| Package resources | `importlib.resources.files(package=...)` is removed; pass the anchor positionally. |
| Import metadata | The import system no longer sets/uses module `__cached__`; use `__spec__.cached`. `importlib.metadata.metadata()` raises `MetadataNotFound` when distribution metadata is missing, instead of returning an empty object. |
| AST construction | Missing required fields or unknown constructor keywords raise `TypeError`; construct valid concrete nodes. |
| `argparse` | `-f`/`-foo` now infers `dest="foo"`. Specify `dest` if consumers expect another name. Suggestions default on; verify exact-output consumers. |
| Compression | `gzip` and `tarfile` default to compression level 6 instead of 9. Expect changed compressed bytes/size; specify a level when the tradeoff is part of the contract. This is a default change, not a format change. |
| Base64 | `urlsafe_b64decode()` no longer requires padding by default. Use `padded=True` if required by the format. `canonical=True` rejects nonzero padding bits on supporting decoders; it alone is not full input validation. |
| `contextlib` | Context decorators now keep the context open during generator iteration, coroutine awaiting, and async-generator iteration. Check lifetime-sensitive code. |
| XML iteration | Close `ElementTree.iterparse()` when abandoning iteration, particularly when it opened the input file. |
| `array` | `typecodes` is now a tuple; complex format codes are multi-character. Check assumptions about string operations. |
| Resource limits | `resource.RLIM_INFINITY` is now positive. Use the constant instead of literal `-1`/`-3` values. |
| Warning tests | `unittest.assertWarns()`/`assertWarnsRegex()` no longer swallow unrelated warnings. Verify newly surfaced warnings instead of ignoring them broadly. |
| Machine-consumed output | More stdlib CLIs emit color. Use an API's color option or `PYTHON_COLORS=0` for affected Python commands; check exact-output consumers. |

Sources: [porting/removals](https://docs.python.org/3.15/whatsnew/3.15.html#removed), [datetime](https://docs.python.org/3.15/library/datetime.html), [sqlite3](https://docs.python.org/3.15/library/sqlite3.html), [base64](https://docs.python.org/3.15/library/base64.html), [contextlib](https://docs.python.org/3.15/library/contextlib.html), [gzip](https://docs.python.org/3.15/library/gzip.html), [tarfile](https://docs.python.org/3.15/library/tarfile.html), [import metadata](https://docs.python.org/3.15/library/importlib.metadata.html), [resource](https://docs.python.org/3.15/library/resource.html), [unittest](https://docs.python.org/3.15/library/unittest.html), [color controls](https://docs.python.org/3.15/using/cmdline.html#controlling-color).

For deprecations, fix actual warnings and relevant uses while touching code. Common near-term candidates: `asyncio` event-loop policies → `asyncio.run()`/`Runner` with `loop_factory`; `asyncio.iscoroutinefunction()` → `inspect.iscoroutinefunction()` (both removals scheduled for 3.16); `ByteString` → `collections.abc.Buffer` or the concrete types the API accepts (3.17). Also replace `os.path.commonprefix()` with `commonpath()` only for filesystem path prefixes, preserving intentional string-prefix behavior. Use current replacements in the selected scripts. Distinguish scheduled removals from soft deprecations, and preserve the operation's semantics when replacing it.

Packaging only: `.start` files contain UTF-8 `pkg.mod:callable` entries called eagerly during `site` initialization. A matching `.start` suppresses executable imports in its corresponding `.pth`; static path entries remain. Executable `.pth` lines are silently deprecated in 3.15. Consult [site](https://docs.python.org/3.15/library/site.html) for the transition schedule. Do not add startup hooks for optional application work or edit third-party installations just to modernize them.

## New profiling and runtime facilities

Use these only for consequential workloads:

```bash
python3 -m profiling.sampling run --mode cpu --flamegraph -o profile.html script.py
python3 -m profiling.sampling attach PID
```

Tachyon defaults to wall samples, main thread, 1 kHz, non-blocking sampling. Use `-a` for all threads, `--async-aware` for logical coroutine stacks, or `--subprocesses` for Python worker processes when relevant. `--async-aware` uses `--async-mode` instead of `--mode`. Check achieved sample rate and rejected samples before drawing conclusions. Measure final timings without instrumentation. CPU mode helps separate computation from waiting. Sampling is low overhead, not literally free; very short scripts may yield too few samples. `--native` adds artificial native-call markers, not full native backtraces. Confirm available options with the installed `--help`. `profiling.tracing` provides deterministic profiling; `cProfile` remains an alias, while `profile` is deprecated.

Source: [profiling.sampling](https://docs.python.org/3.15/library/profiling.sampling.html).

For JIT or free-threading decisions, inspect the actual process after importing relevant dependencies:

```python
import sys
import sysconfig

jit = getattr(sys, "_jit", None)  # build-dependent, even within the target version
print("JIT available/enabled:", bool(jit and jit.is_available()),
      bool(jit and jit.is_enabled()))
print("Free-threaded build:", bool(sysconfig.get_config_var("Py_GIL_DISABLED")))
print("GIL enabled now:", sys._is_gil_enabled())
```

- **JIT:** experimental and build-dependent. If available, compare `PYTHON_JIT=0` and `PYTHON_JIT=1` on real workloads, including warmup. Do not branch application logic on `sys._jit.is_active()` or restructure code on assumed JIT gains.
- **Free threading:** requires a suitable build and compatible dependencies; extensions can re-enable the GIL. Evaluate only for actual parallel CPU work. Keep required synchronization and benchmark scaling against overhead.
- **Multiprocessing:** when porting pre-3.14 code, account for the Linux default `forkserver` start method (changed in 3.14), main-entry guarding, and picklable worker inputs. In 3.15 all `-X` options propagate to spawned workers, including `lazy_imports`; retest worker initialization after import-policy changes. `set_forkserver_preload(..., on_error=...)` adds control over preload failures; useful only for an existing forkserver workload.
- **GC:** 3.15 uses the restored generational collector, not the incremental collector from early 3.14. `gc.get_stats()` and stop callbacks expose `duration` (seconds) and `candidates`. Use these for demonstrated GC costs; do not disable collection or retune thresholds from old advice.
- **Native/build features:** when native code or interpreter packaging is in scope, use the 3.15 `PyBytesWriter` API for bytes construction, `PySlot`/`PyModExport_*` for suitable new extension definitions, interpreter guard/view APIs for finalization-safe attachment, and `abi3t` for the free-threaded Stable ABI. Preserve frame-pointer build flags. Huge pages are a separate measured build/runtime choice. Pure Python scripts need no C-API edits.

Sources: [sys JIT API](https://docs.python.org/3.15/library/sys.html#sys._jit), [free-threading HOWTO](https://docs.python.org/3.15/howto/free-threading-python.html), [multiprocessing](https://docs.python.org/3.15/library/multiprocessing.html), [gc](https://docs.python.org/3.15/library/gc.html).

## Sources and completion rule

Use current 3.15 language/library documentation for interface contracts, the [What's New](https://docs.python.org/3.15/whatsnew/3.15.html) page for discovery, and the [changelog](https://docs.python.org/3.15/whatsnew/changelog.html#changelog) for relevant fixes. PEPs explain design but can lag implementation. Project tests/callers define behavior to preserve; documentation is not permission to change that behavior.

Look up changelog entries only for APIs or workarounds relevant to the touched path. The changelog includes older release history and superseded development changes; confirm the release section, later reversions, and installed patch level before assuming a fix or feature applies. If sources conflict, verify the documented interface against the deployed runtime and report the discrepancy rather than guessing. Reproduce an old bug before removing its workaround.

Finish each edit by reviewing its diff and testing the applicable behavior. For performance changes, remeasure the targeted cost. State which current features were adopted, why they fit, and what was verified. **Use all applicable modern features within the selected script; keep required semantics and skip low-value optimization work outside that scope.**
