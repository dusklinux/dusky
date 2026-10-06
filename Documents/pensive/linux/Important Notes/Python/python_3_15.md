# Python 3.15 Modernization, Performance & Migration Reference for Arch Linux — Audited Final

> **Purpose:** This note is a migration/optimization corpus for an AI agent that will inspect and improve an existing collection of Python scripts for **CPython 3.15+** on Arch Linux. It is intentionally operational: it separates source-level refactors from interpreter-level gains, calls out semantic hazards, supplies search patterns and concrete transformations, and provides an evidence-driven validation workflow.
>
> **Primary upstream references:** [What’s New in Python 3.15](https://docs.python.org/3.15/whatsnew/3.15.html), [Python 3.15 changelog](https://docs.python.org/3.15/whatsnew/changelog.html#changelog), and the individual PEPs and library-reference pages linked throughout.
>
> **Audit status:** this edition was reconciled against the current CPython 3.15 documentation, library reference, language reference, configure documentation, relevant accepted/final PEPs, and late 3.15 changelog entries. It deliberately gives **implemented 3.15 behavior and current library/runtime documentation precedence over older PEP prose** where they differ. Python’s own What’s New page explicitly warns that PEPs are not necessarily kept synchronized after implementation.

## Audit corrections incorporated into this edition

The earlier draft was already unusually comprehensive. This audited edition preserves its useful structure and makes the following material corrections or additions:

- **Lazy-import modes:** runtime mode is `"normal"` or `"all"`; do not emit or depend on the earlier-design `"none"` mode.
- **Lazy-import introspection:** `sys.lazy_modules` is a debugging/introspection **set** of fully qualified names, not a contractual exact list of unresolved bindings; `types.LazyImportType.resolve()` is the documented explicit reification hook.
- **Lazy-import cycles:** `ImportCycleError`, a new `ImportError` subclass, represents direct or indirect self-import during lazy reification.
- **Late lazy-import correctness:** current 3.15 contains fixes for sibling submodule independence, aliased submodule imports, module-level `__getattr__`, `exec()`/non-module globals, and cleanup of `sys.lazy_modules`; old workarounds for those earlier-development defects should not be cargo-culted into final source.
- **Lazy-import finalization hazard:** CPython itself reverted some attempted lazy imports in `subprocess` because finalizers such as `__del__` could need `terminate()`, `kill()`, or `send_signal()`. Treat destructor/finalizer/shutdown dependencies as presumptively eager unless tested.
- **`.start` encoding:** `.start` files are specified as **UTF-8**. The `utf-8-sig` transition applies to `.pth` decoding, not to a requirement that `.start` files be described as `utf-8-sig`.
- **Tachyon:** this edition records default sampling behavior, blocking/non-blocking tradeoffs, native/GC frames, subprocess profiling, real-time sampling statistics, and output formats beyond flamegraphs/heatmaps.
- **JIT:** current upstream benchmark context is recorded: about **7–8% geometric-mean improvement on x86-64 Linux** in the cited pyperformance comparison, with individual workloads ranging from roughly **15% slower to more than 100% faster**. This is exactly why repository-specific A/B measurement is mandatory.
- **Thread iterators:** `concurrent_tee()` edge cases and its one-thread-at-a-time contract per derived iterator are explicit.
- **`asyncio.TaskGroup`:** the 3.15 `GeneratorExit` special case is included alongside `TaskGroup.cancel()`.
- **Native/C API:** the PEP 788 guard/view/thread-state APIs, GC-traversal-safe `*_DuringGC()` family, ABI checks, `PyTuple_FromArray()`, Stable-ABI critical sections, and PEP 820/793 migration implications are made explicit.
- **Huge pages:** Linux `MAP_HUGETLB` experimentation now carries the documented `SIGBUS` exhaustion/COW warning.
- **Late security/correctness:** the release-line audit includes current TLS hostname-validation hardening and pathological float/complex formatting bounds, plus selected late lazy-import/free-threading corrections.

### Source-precedence rule for the local agent

When sources disagree, use this order:

```text
1. Current Python 3.15 language/library/C-API/configuration documentation
2. Current Python 3.15 What's New + 3.15 changelog
3. Accepted/final PEP text for rationale and deeper design context
4. Existing project tests/contracts
5. Third-party commentary
6. Model memory
```

A PEP is invaluable for rationale, but it is not automatically the last word on the shipped interface. Never synthesize a migration from stale PEP pseudocode when the 3.15 runtime/reference documentation says otherwise.

---

## 0. Operating doctrine for the migration agent

Treat Python 3.15 modernization as four different classes of work. Do not mix them.

| Class | Meaning | Typical examples | Source edit required? |
|---|---|---|---|
| **A — Automatic runtime gain** | CPython itself became faster/leaner/more observable | faster Base64/Base32/Base85 codecs, mimalloc raw allocator, event-driven `Popen.wait(timeout)`, frame pointers, class-descriptor sharing | Usually **no** |
| **B — Opt-in source optimization** | New language/library feature can remove work or defer it | `lazy import`, `bytearray.take_bytes()`, `frozendict`, `TaskGroup.cancel()`, `threading.serialize_iterator()` | **Yes** |
| **C — Semantic modernization** | New API makes intent clearer/safer without an inherent speed guarantee | `sentinel`, `re.prefixmatch`, `math.integer`, `TypeForm`, closed `TypedDict`, unpacking comprehensions | Usually **yes** |
| **D — Compatibility/porting change** | Existing code may break, warn, or subtly behave differently | UTF-8 default, `datetime.strptime()` day-without-year error, removed `load_module()`, SQLite parameter-kind changes | Sometimes mandatory |

**Agent invariant:** never rewrite merely because a feature is new. Rewrite when at least one of the following is true: it removes work, removes allocation/copying, reduces startup or steady-state cost, improves correctness, eliminates a deprecation/removal risk, improves concurrency semantics, or makes a measurable maintenance improvement without semantic regression.

### 0.1 Mandatory pre-edit inventory

Capture the actual interpreter/build before transforming code:

```bash
python -VV
python - <<'PY'
import sys
import sysconfig

print("version:", sys.version)
print("implementation:", sys.implementation)
print("abiflags:", getattr(sys, "abiflags", None))
print("abi_info:", getattr(sys, "abi_info", None))
print("GIL enabled now:", getattr(sys, "_is_gil_enabled", lambda: "unknown")())
print("Py_GIL_DISABLED:", sysconfig.get_config_var("Py_GIL_DISABLED"))
print("CFLAGS:", sysconfig.get_config_var("CFLAGS"))
print("PY_CFLAGS:", sysconfig.get_config_var("PY_CFLAGS"))

jit = getattr(sys, "_jit", None)
if jit is not None:
    print("JIT available:", jit.is_available())
    print("JIT enabled:", jit.is_enabled())
else:
    print("JIT API: unavailable")

if hasattr(sys, "get_lazy_imports"):
    print("lazy-import mode:", sys.get_lazy_imports())
PY
```

Record, per script/package:

- cold-start wall time;
- warm-start wall time;
- peak RSS;
- import-time profile;
- CPU profile for representative workloads;
- I/O versus CPU attribution;
- unit/integration test baseline;
- warnings under development mode;
- native-extension inventory (`.so` modules) if free-threading or ABI work is contemplated.

Useful baseline commands:

```bash
# Import timeline (stderr)
python -X importtime -c 'import your_package' 2> importtime.before.txt

# Development checks / resource warnings
python -X dev -W default your_script.py

# Find implicit text-encoding choices
python -X warn_default_encoding your_script.py

# Basic wall/user/sys/RSS measurements on Linux
/usr/bin/time -v python your_script.py

# Repeat a startup-oriented command externally if you have a benchmark runner;
# otherwise script many subprocess invocations and report median/p95.
```

For performance patches, preserve a **before/after benchmark artifact**. Prefer medians and distributions over single runs; pin workload, inputs, environment variables, CPU governor/thermal conditions when practical; separate cold-start from warmed steady state.

---

# 1. PEP 810 — Explicit lazy imports: the highest-leverage startup feature

**References:** [What’s New: lazy imports](https://docs.python.org/3.15/whatsnew/3.15.html#pep-810-explicit-lazy-imports), [PEP 810](https://peps.python.org/pep-0810/), [`sys` lazy-import controls](https://docs.python.org/3.15/library/sys.html), [`types.LazyImportType`](https://docs.python.org/3.15/library/types.html)

Python 3.15 adds a `lazy` soft keyword for imports. The imported module/binding is represented by a lightweight proxy and actual module loading is deferred until first use.

```python
lazy import json
lazy from pathlib import Path

print("startup path")        # neither necessarily loaded yet
payload = json.loads(text)   # json resolves here
path = Path("data.json")     # pathlib resolves here
```

This is the most direct 3.15 source-level tool for reducing import-dominated startup latency and memory footprint in command-line programs, GUI applications, multipurpose scripts, plugin-rich applications, and packages with deep dependency trees.

## 1.1 Exact syntax and restrictions

Valid at **module scope**:

```python
lazy import package
lazy import package.submodule as sub
lazy from package import Name
lazy from package.submodule import A, B
```

Invalid:

```python
def f():
    lazy import package      # SyntaxError

class C:
    lazy import package      # SyntaxError

try:
    lazy import optional     # SyntaxError
except ImportError:
    ...

lazy from package import *   # SyntaxError
lazy from __future__ import annotations  # SyntaxError
```

`lazy` is a soft keyword, so ordinary identifiers named `lazy` remain possible outside the syntactic import form.

## 1.2 Failure timing changes

With an eager import, missing/broken dependencies fail at import time. With a lazy import, the failure is deferred to the first operation that forces resolution.

```python
lazy import optional_backend

print("program can reach here")
optional_backend.run()  # ImportError (or dependency initialization error) occurs here
```

This is a **semantic change**, not merely a performance switch. Python’s traceback is designed to retain information about both the lazy-import site and the forcing use, but application behavior still changes because errors move later in time.

**Do not lazify an import if early failure is part of the contract.** Examples: required dependency checks, startup health checks, command validation that should fail before any work is performed, or services where readiness must imply all mandatory modules initialized successfully.

## 1.3 Process-wide modes

Python 3.15 exposes a process-wide mode:

```bash
python -X lazy_imports=all app.py
python -X lazy_imports=normal app.py

PYTHON_LAZY_IMPORTS=all python app.py
PYTHON_LAZY_IMPORTS=normal python app.py
```

Programmatically:

```python
import sys

print(sys.get_lazy_imports())
sys.set_lazy_imports("all")
# ... later, if desired:
sys.set_lazy_imports("normal")
```

**Use the current 3.15 runtime documentation as the source of truth:** the supported modes are `"normal"` and `"all"`. Do not generate code that assumes a `"none"` mode merely because an earlier design text or stale discussion mentions one.

### Recommended role of global `all`

Treat `-X lazy_imports=all` primarily as:

1. a diagnostic to estimate upper-bound startup gains;
2. a way to discover import-side-effect assumptions;
3. a controlled application-wide policy only when you own the import graph and have strong tests.

For long-lived maintainable code, prefer **explicit `lazy import` declarations** around known cold paths, unless the program is deliberately designed around a global policy.

## 1.4 Programmable filter

Global lazy mode can be constrained:

```python
import sys


def lazy_filter(importing_module, imported_module, fromlist):
    # Keep third-party packages eager; lazify only our own cold modules.
    return imported_module.startswith("myapp.")

sys.set_lazy_imports_filter(lazy_filter)
sys.set_lazy_imports("all")
```

The filter receives the importing module name (which can be `None`), imported module name, and from-list (or `None`). Return `True` to allow laziness, `False` to force eager loading.

This is valuable for gradually adopting lazy imports while explicitly excluding modules with import-time registration, native initialization, or poorly tested side effects.

## 1.5 `__lazy_modules__`: compatibility-oriented opt-in

A module can declare fully qualified module names whose ordinary imports should be lazy:

```python
__lazy_modules__ = ["json", "pathlib"]

import json      # lazy on 3.15 according to the declaration
import os        # normal/eager
```

Use this mechanism when source syntax must still parse on pre-3.15 interpreters. If the codebase is truly 3.15-only, explicit `lazy import` syntax is usually clearer.

Relative imports must be reasoned about using their **fully qualified module names** when populating `__lazy_modules__`.

## 1.6 Reification model and non-obvious semantics

A local binding initially references a `types.LazyImportType` proxy. When an operation needs the target, Python resolves the import and transparently replaces/uses the real object.

Important consequences:

- Lazy import defers **when** the module is imported; it does not partially execute the module. Once forced, normal module execution occurs.
- For `lazy from X import a, b`, forcing one imported name loads module `X`, but the distinct imported bindings may still have their own lazy reification behavior.
- After hot use, adaptive specialization is designed to remove continuing proxy-check overhead; lazy imports are not intended to impose a perpetual tax on every access.
- Reification observes import-system state at the time it occurs. If code mutates `sys.path`, `sys.meta_path`, import hooks, or related state between declaration and first use, the result can differ from eager import behavior.
- A failed resolution can be attempted again on later use; do not assume failure permanently poisons every proxy.
- Merely examining a module namespace through `globals()`, a module `__dict__`, or similar introspection should not be treated as equivalent to intentionally forcing every lazy import.
- Dynamic import APIs such as `importlib.import_module()` and direct `__import__()` calls remain ordinary dynamic operations; do not rewrite code expecting the `lazy` keyword semantics to appear automatically.

## 1.7 `sys.lazy_modules` and introspection

Python exposes lazy-import state for debugging. Treat `sys.lazy_modules` as an **observability/debugging aid**, not an application invariant. Its contents describe names associated with lazily imported modules and can include entries whose proxy/binding state makes simplistic “present means unloaded” assumptions unreliable.

When an agent needs to force or inspect a proxy, consult `types.LazyImportType` and the current 3.15 library reference rather than depending on undocumented internals.

## 1.8 What to lazify first

High-probability wins:

```python
# CLI with optional export paths
lazy import pandas
lazy import matplotlib.pyplot as plt
lazy from myapp.report import generate_pdf
lazy from myapp.gui import launch_gui
```

Candidates detectable statically:

- heavy modules imported at top level but used only in one CLI subcommand;
- GUI stacks used only in interactive mode;
- database/ORM clients used only on some paths;
- serialization/export libraries used only for particular formats;
- cloud SDKs, HTTP clients, cryptography/tooling packages loaded only conditionally;
- expensive internal modules imported by a broad `__init__.py` surface;
- modules currently imported inside functions purely to dodge startup cost.

A useful modernization pattern is moving a **function-local performance import** back to the top level and marking it lazy:

```python
# old workaround
def export_pdf(data):
    from reportlab.pdfgen import canvas
    ...

# 3.15 style
lazy from reportlab.pdfgen import canvas

def export_pdf(data):
    ...
```

Benefits: import dependencies become visible in one place while preserving demand loading.

## 1.9 What NOT to lazify blindly

Search the imported module and its transitive initialization for side effects. Keep imports eager when they intentionally:

- register plugins, codecs, serializers, routes, CLI commands, ORM models, dependency-injection providers, or dispatch implementations;
- configure logging handlers/formatters;
- install signal handlers;
- mutate global registries relied on before direct symbol access;
- monkey-patch another module;
- read environment/configuration once at startup where timing is semantically significant;
- start threads/processes/watchers;
- initialize native libraries or hardware resources expected to exist immediately;
- perform mandatory dependency/license/security validation;
- populate package attributes that callers access without importing the defining submodule;
- deliberately fail fast.

**Import-side-effect audit rule:** if correctness depends on “module imported” rather than “a value from the module was used,” laziness is suspect.

## 1.10 Lazy imports and `TYPE_CHECKING`

3.15 creates opportunities to simplify patterns such as:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from expensive_pkg import Widget
```

A runtime-needed type can instead sometimes be made lazy:

```python
lazy from expensive_pkg import Widget
```

Do not mechanically remove every `TYPE_CHECKING` guard. Distinguish:

- imports needed only by a static type checker — keep type-only machinery if runtime has no need;
- imports referenced by evaluated runtime annotations, decorators, serializers, or reflection — lazy import can be useful;
- circular-import avoidance — verify actual runtime annotation evaluation and import cycles before changing.

## 1.11 Lazy-import benchmarking protocol

For each candidate package/script:

```bash
# Baseline
python -X importtime -c 'import myapp' 2> eager.importtime.txt

# Estimate all-lazy ceiling / find breakage
python -X lazy_imports=all -X importtime -c 'import myapp' 2> all-lazy.importtime.txt

# Benchmark real entrypoint repeatedly after explicit transformations
/usr/bin/time -v python -m myapp --help
/usr/bin/time -v python -m myapp representative-command
```

Measure at least:

- `--help` / no-op startup;
- one path that never uses each lazified dependency;
- one path that does use it, including the first-use latency;
- peak RSS before and after;
- total steady-state runtime for representative workloads.

The goal is not merely to make startup look fast by moving all cost into an unexpectedly visible first action. Optimize for the actual interaction contract.


## 1.12 Runtime-control API details the agent should know

The 3.15 `sys` API includes both setters and getters for the process policy:

```python
import sys

mode = sys.get_lazy_imports()              # "normal" or "all"
current_filter = sys.get_lazy_imports_filter()

sys.set_lazy_imports_filter(my_filter)     # or None to clear
sys.set_lazy_imports("all")
```

**Library-author rule:** process-wide lazy-import policy belongs to the application or embedding environment, not an ordinary reusable library. A library calling `sys.set_lazy_imports("all")` changes import semantics for unrelated code in the interpreter. Libraries should normally use explicit `lazy import` declarations for their own cold dependencies and leave global policy to the application.

The filter receives the **resolved** imported module name. For a relative import such as:

```python
# package.mod
lazy from .sub import feature
```

the imported-module argument is the fully qualified module name (for example, `package.sub`), not the textual `.sub` spelling.

## 1.13 `types.LazyImportType.resolve()` — deliberate reification

When tooling or a carefully designed boundary genuinely needs to force a lazy proxy, use the documented API rather than implementation internals:

```python
from types import LazyImportType

value = globals().get("backend")
if isinstance(value, LazyImportType):
    value = value.resolve()
```

`resolve()` reifies the import and returns the real imported object. This is primarily useful to debuggers, introspection tooling, serializers, dependency checks, and explicit warmup phases. Application code should usually just use the imported name naturally and let first use trigger reification.

Do not sprinkle `resolve()` calls through hot code: doing so defeats the abstraction and can accidentally turn an intended cold dependency back into startup work.

## 1.14 Lazy-import cycles: `ImportCycleError`

Python 3.15 adds `ImportCycleError`, a subclass of `ImportError`, for a lazy import that fails because reification directly or indirectly attempts to import itself.

A migration agent should distinguish this from a generic missing dependency:

```python
try:
    use_feature_that_forces_lazy_import()
except ImportCycleError:
    # Structural import-cycle problem: redesign dependency direction.
    raise
except ModuleNotFoundError:
    # Potentially an absent optional package.
    ...
```

Do **not** paper over `ImportCycleError` by retrying imports, mutating `sys.path`, or converting the cycle into a blanket function-local import. Fix the dependency graph or preserve an eager import at the boundary that establishes the required initialization order.

## 1.15 `sys.lazy_modules` is observability, not a correctness primitive

Current 3.15 documents `sys.lazy_modules` as a **set** of fully qualified module-name strings that have been lazily imported and generally not yet loaded. It can contain additional strings and consumers are explicitly expected to verify status.

Therefore these are invalid application assumptions:

```python
# Bad: treats an implementation/debug set as a definitive loading oracle.
if "pkg.backend" in sys.lazy_modules:
    assert "pkg.backend" not in sys.modules

# Bad: correctness depends on exact cleanup timing of debug metadata.
while "pkg.backend" in sys.lazy_modules:
    ...
```

Use it for diagnostics, tests of broad behavior, and tooling—not as a synchronization protocol or business-logic state machine.

## 1.16 Late 3.15 lazy-import fixes that invalidate earlier-development workarounds

The 3.15 changelog contains a cluster of correctness fixes around the new mechanism. The local agent should know them because code written against earlier alphas/betas may contain unnecessary compensating logic:

- sibling lazily imported submodules resolve independently instead of one access spuriously forcing another sibling;
- aliased dotted imports such as `lazy import a.b as c` correctly resolve the submodule binding and fail with the proper import exception when appropriate;
- `lazy from ... import ...` cooperates with attributes supplied through module-level `__getattr__`;
- lazy imports used with `exec()` and non-module globals/builtins no longer leave cached global loads as unresolved proxies in the fixed cases;
- `sys.lazy_modules` bookkeeping was tightened so already-loaded/resolved modules are discarded more consistently;
- direct low-level `__lazy_import__` edge cases around `fromlist` were corrected.

**Migration rule:** if the repository contains comments such as “work around Python 3.15 lazy import bug,” reproduce the alleged bug on the deployed 3.15 binary before retaining the workaround.

## 1.17 Finalizers, destructors, shutdown paths: keep dependencies eager by default

A particularly instructive CPython changelog entry reverted attempted lazy imports inside `subprocess`: delayed imports could fail when object finalizers (`__del__`) invoked `terminate()`, `kill()`, or `send_signal()` during teardown.

That generalizes into an agent rule:

> If code can run during object finalization, interpreter shutdown, `atexit`, weakref callbacks, signal teardown, logging shutdown, or emergency cleanup, do not lazify its dependencies merely because they are cold in the normal path.

Shutdown is a hostile environment: module globals may already be cleared, import machinery may be partially torn down, locks may have different liveness properties, and surfacing first-import latency/errors there is usually the wrong failure mode.

Audit patterns such as:

```text
__del__
weakref.finalize
atexit.register
signal.signal
context-manager cleanup
finally blocks that run at process termination
logging.shutdown
subprocess cleanup/kill/terminate paths
```

If such code depends on a module, an eager import is often the more robust choice even when startup would be microscopically faster without it.

---

# 2. PEP 814 — `frozendict`: immutable, potentially hashable mappings

**References:** [PEP 814](https://peps.python.org/pep-0814/), [built-in mapping types](https://docs.python.org/3.15/library/stdtypes.html#mapping-types-dict-frozendict), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#pep-814-add-frozendict-built-in-type)

Python 3.15 adds a built-in immutable mapping:

```python
CONFIG = frozendict(host="localhost", port=5432)
```

Properties:

- immutable after construction;
- **not** a subclass of `dict`;
- insertion order is preserved;
- equality/comparison semantics ignore insertion order, like `dict`;
- hashable iff all keys and values are hashable;
- generic: `frozendict[str, int]`;
- supports normal read-only mapping operations;
- supports merging; `|=` rebinds to a new frozen dictionary rather than mutating in place;
- omits mutators such as item assignment/deletion, `clear`, `pop`, `popitem`, `setdefault`, and `update`.

## 2.1 High-value migrations

### Immutable module constants

```python
# before
HTTP_STATUS = {"ok": 200, "missing": 404}

# after
HTTP_STATUS = frozendict(ok=200, missing=404)
```

This turns accidental mutation into an immediate error and communicates invariant intent.

### Hashable composite keys

```python
from functools import cache

@cache
def compile_query(options: frozendict[str, str]):
    ...

opts = frozendict(dialect="sqlite", mode="strict")
compile_query(opts)
```

Do not convert a mapping to `frozendict` merely to make it cacheable if values are themselves unhashable.

### Immutable JSON trees

Python 3.15’s `json` `array_hook` combines neatly with `frozendict`:

```python
import json

obj = json.loads(
    text,
    object_pairs_hook=frozendict,
    array_hook=tuple,
)
```

This produces a deeply immutable shape for JSON objects/arrays, subject to leaf values.

## 2.2 Compatibility audit: `dict` identity assumptions

Code like this excludes `frozendict`:

```python
if isinstance(value, dict):
    ...
```

If the semantic contract is “mapping,” prefer:

```python
from collections.abc import Mapping

if isinstance(value, Mapping):
    ...
```

Use `(dict, frozendict)` only where the accepted concrete built-ins are intentionally narrower than arbitrary `Mapping` implementations.

Standard-library support in 3.15 includes `copy`, `decimal`, `json`, `marshal`, `plistlib` serialization, `pickle`, `pprint`, and `xml.etree.ElementTree`; `eval()`/`exec()` accept frozen mappings for globals, while `type()` and `str.maketrans()` accept them where mappings are expected.

## 2.3 Do not overuse

Do **not** convert:

- hot mutable accumulators;
- mappings updated incrementally in loops;
- object state designed for mutation;
- API payloads that downstream code mutates;
- caches where immutability would force frequent whole-mapping reconstruction.

Immutability can improve correctness and enable hashing; it is not a blanket “faster dict.” Benchmark mapping-heavy hot paths rather than presuming a speedup.

---

# 3. PEP 661 — built-in `sentinel`

**References:** [PEP 661](https://peps.python.org/pep-0661/), [`sentinel()`](https://docs.python.org/3.15/library/functions.html#sentinel), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#pep-661-add-sentinel-built-in-type)

Python now has a first-class sentinel constructor:

```python
MISSING = sentinel("MISSING")


def lookup(key, default: str | MISSING = MISSING):
    if default is MISSING:
        ...
```

Signature:

```python
sentinel(name, /, *, repr=None)
```

Semantics:

- every call creates a unique sentinel;
- compare with `is`, not `==`;
- sentinels are truthy;
- copy/deepcopy preserve identity;
- cannot be subclassed;
- `|` works for type expressions, e.g. `int | MISSING`;
- `__name__` identifies the sentinel;
- `__module__` is writable;
- pickling preserves identity when the sentinel is discoverable by the required module/class qualified name.

### Replace ad-hoc sentinel boilerplate

```python
# before
_MISSING = object()

# after
MISSING = sentinel("MISSING")
```

The built-in has a useful representation and explicit typing story, so it is generally superior for public or diagnostic-facing sentinels.

### Pickling rule

Prefer a module-global matching binding:

```python
PICKLABLE = sentinel("PICKLABLE")
```

or a matching qualified class binding:

```python
class State:
    UNKNOWN = sentinel("State.UNKNOWN")
```

Do not create a fresh sentinel at each comparison site:

```python
# wrong: this creates a new unique object every call
if value is sentinel("MISSING"):
    ...
```

Define once; reuse identity.

---

# 4. PEP 798 — unpacking in comprehensions and generator expressions

**References:** [PEP 798](https://peps.python.org/pep-0798/), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#pep-798-unpacking-in-comprehensions)

Python 3.15 permits `*`/`**` unpacking directly in comprehension output positions:

```python
lists = [[1, 2], [3, 4], [5]]
flat = [*xs for xs in lists]

sets = [{1, 2}, {2, 3}]
merged_set = {*s for s in sets}

dicts = [{"a": 1}, {"b": 2}, {"a": 3}]
merged_dict = {**d for d in dicts}  # later a=3 wins

gen = (*xs for xs in lists)
```

Equivalent conceptual forms:

```python
[x for xs in lists for x in xs]
{k: v for d in dicts for k, v in d.items()}
```

Async generator expressions support the same idea:

```python
(*items async for items in agen())
```

### Agent guidance

Candidate simplifications:

- flattening nested comprehensions;
- `itertools.chain.from_iterable(...)` used solely for a flattening expression;
- repeated dictionary merges expressed as nested key/value comprehensions.

Do **not** assume this syntax is automatically faster than `chain`, nested comprehensions, or a purpose-built loop. Prefer it when it improves intent and benchmark it in allocation-sensitive hot paths.

---

# 5. PEP 686 — UTF-8 is the default text encoding

**References:** [PEP 686](https://peps.python.org/pep-0686/), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#other-language-changes)

In 3.15, text I/O that omits `encoding=` defaults to UTF-8 independently of the system locale:

```python
with open("config.txt", "r") as f:
    text = f.read()  # UTF-8 by default in 3.15
```

### Migration rule

**Do not mechanically delete explicit `encoding="utf-8"`.** Explicit encodings remain valuable contracts, improve portability to older runtimes, and make file-format assumptions self-documenting.

Use locale-dependent decoding only when it is intentionally part of the external protocol:

```python
open(path, encoding="locale")
```

Audit ambiguous sites:

```bash
python -X warn_default_encoding your_script.py
# or
PYTHONWARNDEFAULTENCODING=1 python your_script.py
```

This change primarily removes environmental variance; it is not an invitation to ignore encodings at external boundaries.

---

# 6. PEP 829 — `.start` package startup configuration

**References:** [PEP 829](https://peps.python.org/pep-0829/), [`site` documentation](https://docs.python.org/3.15/library/site.html), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#pep-829-package-startup-configuration-files)

Python 3.15 introduces auditable startup configuration files ending in `.start`. Nonblank, noncomment lines identify no-argument entry points:

```text
my_package.startup:initialize
other_package.hooks:install
```

The `site` module imports the specified callable and invokes it with no arguments; its return value is ignored.

Key semantics:

- `.pth` files still extend `sys.path` using path lines;
- executable `import ...` lines inside `.pth` files are deprecated;
- when a matching `.start` file exists, executable import lines in the corresponding `.pth` are ignored;
- static path extensions are processed before startup entry points;
- startup hooks run whenever normal `site` initialization runs (unless Python is started with `-S`);
- duplicate startup entry points are not inherently deduplicated;
- startup exceptions are reported, after which site processing continues according to documented behavior;
- `.start` files **must be encoded in UTF-8**. Do not conflate this with the `.pth` transition: non-`utf-8-sig` `.pth` decoding is deprecated and the locale fallback is removed on the documented PEP 829 timeline.

`site.StartupState` exists for callers/distributors that need to batch site-directory processing and then execute startup hooks after all path extensions have been accumulated.

### Performance implication

A `.start` hook is **eager startup code**. It runs whether your application ultimately needs the package or not. Do not migrate ordinary lazy/optional application initialization into `.start`. Use it only for behavior that genuinely must execute during interpreter startup.

### Packaging audit

Search virtual environments and site-packages for executable `.pth` lines:

```bash
python - <<'PY'
import site
from pathlib import Path

roots = set(site.getsitepackages())
try:
    roots.add(site.getusersitepackages())
except Exception:
    pass

for root in sorted(map(Path, roots)):
    if not root.exists():
        continue
    for pth in root.glob("*.pth"):
        for n, line in enumerate(pth.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.startswith(("import ", "import\t")):
                print(f"{pth}:{n}: {line}")
PY
```

If you maintain the package producing such hooks, migrate executable startup logic to `.start` according to PEP 829 rather than preserving opaque `.pth` execution.

---

# 7. PEP 799 — the `profiling` package and Tachyon sampling profiler

**References:** [PEP 799](https://peps.python.org/pep-0799/), [`profiling`](https://docs.python.org/3.15/library/profiling.html), [`profiling.sampling`](https://docs.python.org/3.15/library/profiling.sampling.html), [`profiling.tracing`](https://docs.python.org/3.15/library/profiling.tracing.html)

Python 3.15 creates a dedicated `profiling` namespace:

- `profiling.tracing`: deterministic call tracing, relocated from `cProfile`;
- `profiling.sampling`: Tachyon, a high-frequency external statistical sampler.

`cProfile` remains available as a compatibility alias. The pure-Python `profile` module is deprecated and scheduled for removal in 3.17; migrate new tooling to `profiling.tracing`.

## 7.1 Why Tachyon matters to an optimization agent

Deterministic profilers instrument call events and can materially perturb microbehavior. Tachyon samples a target process externally, making it suitable for discovering real bottlenecks with very low target overhead. It can attach to a process already running, profile from process start, inspect multiple threads, reconstruct async task stacks, report bytecode/opcode activity, and emit interactive visualizations.

Core commands:

```bash
# Run a script under sampling
python -m profiling.sampling run your_script.py arg1 arg2

# Run a module
python -m profiling.sampling run -m your_package.command arg1

# Attach to a live process
python -m profiling.sampling attach PID

# One-shot stack dump
python -m profiling.sampling dump PID

# Interactive live view
python -m profiling.sampling run --live your_script.py

# All threads
python -m profiling.sampling run -a your_script.py

# Explicit duration/rate
python -m profiling.sampling run -d 30 -r 20khz your_script.py

# Self-contained flame graph
python -m profiling.sampling run --flamegraph -o profile.html your_script.py

# Source-line heatmap
python -m profiling.sampling run --heatmap your_script.py

# Bytecode/specialization visibility
python -m profiling.sampling run --opcodes --flamegraph -o opcodes.html your_script.py
```

Supported conceptual modes include:

- **wall** (default): calendar time, including waits;
- **cpu**: actively executing CPU time;
- **gil**: time spent holding the GIL;
- **exception**: samples threads while an exception is active.

### Diagnostic pattern: wall vs CPU

If a function dominates wall samples but disappears from CPU samples, investigate waiting, blocking I/O, synchronization, subprocesses, network/filesystem latency, or sleeping rather than optimizing Python instructions. If it dominates CPU samples, inspect algorithmic complexity, allocation, Python/native transitions, data representation, vectorization opportunities, or the JIT profile.

### Async-aware analysis

Use `--async-aware` for applications where `await` breaks the apparent physical call stack. This reconstructs logical async task relationships, preventing the agent from optimizing dispatcher/event-loop frames while missing the coroutine actually responsible for latency.

### Opcode mode

`--opcodes` can reveal which bytecodes dominate samples and whether adaptive specializations are involved. This is useful when testing:

- JIT versus interpreter;
- type-stable versus polymorphic hot loops;
- attribute/global/subscript-heavy paths;
- code transformations intended to make specialization easier.

Do not optimize bytecode counts in isolation. A lower opcode count can still lose to more expensive C calls, allocations, cache misses, or I/O.

## 7.2 Linux attach permissions

Attaching to unrelated processes depends on Linux process-inspection permissions (`ptrace`/`process_vm_readv`-related restrictions). Depending on Yama configuration, namespaces, capabilities, and process relationship, attachment may require elevated privileges or `CAP_SYS_PTRACE`.

**Security rule:** do not globally weaken `kernel.yama.ptrace_scope` merely to make profiling convenient. Prefer profiling a child process you launch, a suitably authorized development process, or a narrowly scoped capability when justified.

Profiler and target should use the same compatible Python minor/build family; when experimenting with free-threaded Python, make sure profiler/target build assumptions match the profiler documentation.


## 7.3 Tachyon defaults and advanced controls

The current 3.15 sampling profiler defaults are worth encoding explicitly because they affect interpretation of profiles:

| Setting | Default |
|---|---|
| Sampling rate | 1 kHz |
| Duration | Run to completion |
| Threads | Main thread only |
| Native frames | Not shown (`--native` enables them) |
| GC frames | Included (`--no-gc` suppresses them) |
| Mode | Wall clock |
| Real-time sampling stats | Off |
| Subprocess profiling | Off |
| Sampling | Non-blocking |

Useful additions to the basic workflow:

```bash
# Distinguish Python frames from time inside C/native code
python -m profiling.sampling run --native your_script.py

# Verify achieved sampling rate / missed-sample behavior
python -m profiling.sampling run --realtime-stats your_script.py

# Recursively profile Python subprocess descendants
python -m profiling.sampling run --subprocesses --flamegraph your_script.py

# Use blocking snapshots only when rapidly changing generator/coroutine stacks
# produce inconsistent reconstructed stacks.
python -m profiling.sampling run --blocking your_script.py
```

`--subprocesses` recursively follows Python descendants and is useful for `multiprocessing`, `ProcessPoolExecutor`, and Python subprocess trees; the implementation caps concurrent subprocess profilers to prevent resource exhaustion. It is incompatible with the single interactive `--live` display.

Blocking mode stops the target during each sample, yielding internally consistent stack snapshots at the cost of real target slowdown. Do **not** combine blocking mode with extremely aggressive rates; use it as a diagnostic fallback, not the normal benchmark mode.

### Output-format selection

Use the output format to match the question:

- **pstats**: quantitative function-level aggregation and compatibility with existing `pstats` workflows;
- **collapsed stacks**: interchange with external flamegraph/speedscope tooling;
- **flamegraph**: self-contained call-hierarchy visualization;
- **Gecko/Firefox Profiler**: timeline-oriented analysis and richer markers;
- **heatmap**: source-line localization;
- **live**: interactive, current-state exploration;
- **opcode mode**: interpreter/JIT specialization questions, not ordinary business-logic profiling.

Do not compare performance numbers collected under materially different profiler modes as if the profiler were invisible. Non-blocking sampling is designed for minimal target perturbation; blocking/native/opcode/subprocess modes answer different questions and can carry additional cost.

---

# 8. PEP 831 — frame pointers enabled by default

**References:** [PEP 831](https://peps.python.org/pep-0831/), [What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#pep-831-frame-pointers-enabled-by-default)

Where supported, CPython 3.15 is built with frame pointers by default using compiler options equivalent to:

```text
-fno-omit-frame-pointer
-mno-omit-leaf-frame-pointer
```

This substantially improves native stack unwinding for Linux observability tools such as `perf`, debuggers, crash analyzers, and eBPF-based profilers.

The flags are exposed through `sysconfig`, so native extension builds that correctly consume Python’s build configuration inherit the intended frame-pointer policy.

### Arch/Linux implication

For mixed Python/native workloads, 3.15 makes system-level profiling less brittle. A useful workflow is:

```bash
# Check exported flags
python - <<'PY'
import sysconfig
for key in ("CFLAGS", "PY_CFLAGS", "CONFIG_ARGS"):
    print(key, sysconfig.get_config_var(key))
PY

# Example native profiling workflow when perf is installed/configured
perf stat -- python your_script.py
perf record -g -- python your_script.py
perf report
```

The exact visibility of Python frames and symbols depends on the profiler mode/build, but reliable native unwinding is materially improved by an unbroken frame-pointer chain.

### Native-extension caveat

If a C/C++/Rust extension is built by a backend that **does not** preserve CPython’s `sysconfig` flags, compile it with equivalent frame-pointer settings when mixed-stack observability matters. A single native component that discards the frame-pointer chain can compromise stack unwinding through that component.

There is a CPython configure-time opt-out (`--without-frame-pointers`), but disabling them should be an explicit measured tradeoff, not a reflexive “performance tweak.”

---

# 9. Experimental JIT: materially upgraded in 3.15

**References:** [What’s New — JIT](https://docs.python.org/3.15/whatsnew/3.15.html#an-improved-jit-compiler), [`sys._jit`](https://docs.python.org/3.15/library/sys.html#sys._jit), [configure options](https://docs.python.org/3.15/using/configure.html#cmdoption-enable-experimental-jit)

Python 3.15 substantially upgrades CPython’s experimental tracing JIT. In the current upstream What’s New benchmark context, pyperformance reports roughly a **7–8% geometric-mean improvement on x86-64 Linux** versus the optimized standard interpreter; the corresponding AArch64 macOS comparison is about **11–12%** versus the tail-calling interpreter. Individual JIT-on versus JIT-off results vary enormously—from roughly **15% slower to more than 100% faster** in the cited set (excluding an outlying microbenchmark). Therefore: **detect and benchmark; never assume.**

Major implementation advances include a broader tracing front end, support for more bytecodes/control-flow patterns and object creation, basic register allocation, improved constant propagation/reference-count elimination, optimizations that can expose unique references for in-place numeric operations, and better native-code debugging/unwinding. The implementation also reduced generated-code memory costs compared with earlier experimental revisions.

## 9.1 Detect availability and enablement

```python
import sys

jit = getattr(sys, "_jit", None)
if jit is None:
    print("This build exposes no JIT API")
elif not jit.is_available():
    print("CPython was not built with JIT support")
else:
    print("JIT supported; enabled:", jit.is_enabled())
```

Do not use `sys._jit.is_active()` as an application branch or optimization predicate. It is intended for JIT testing/debugging; probing it from hot code can itself change tracing behavior.

## 9.2 Build modes

On Unix-like systems the configure option is:

```text
--enable-experimental-jit=[no|yes|yes-off|interpreter]
```

Conceptually:

- `no`: do not build JIT support;
- `yes`: build and default it on; `PYTHON_JIT=0` can disable it at startup;
- `yes-off`: build it but default it off; `PYTHON_JIT=1` can enable it;
- `interpreter`: specialized development/debug mode.

LLVM 21 is a build-time dependency for the 3.15 JIT build path; it is not a requirement for merely running an ordinary non-JIT CPython 3.15 binary.

**Arch-specific operational rule:** do not infer JIT support from `python --version`. Inspect `sys._jit.is_available()` on the exact binary. Distribution packaging choices and locally built interpreters can differ.

## 9.3 A/B benchmark JIT correctly

When JIT is compiled into the executable:

```bash
PYTHON_JIT=0 python benchmark.py
PYTHON_JIT=1 python benchmark.py
```

Benchmark long enough to amortize warmup. Record both startup-sensitive and steady-state workloads because JIT compilation can trade warmup/code-generation cost for hot-loop throughput.

Good JIT candidates:

- repeated Python-level numeric/scalar loops;
- hot control-flow-heavy code with stable types;
- long-lived services or batch jobs where warmup is amortized.

Poor assumptions:

- short CLI commands dominated by import/startup;
- I/O-bound scripts;
- workloads whose time is overwhelmingly inside already-optimized native libraries;
- highly polymorphic cold code.

Do not contort readable Python into JIT-friendly shapes without measured evidence. Algorithmic changes, fewer allocations, less I/O, and better data structures usually dominate micro-level JIT gaming.

---

# 10. Automatic CPython 3.15 performance improvements

These are mostly **free wins**. An AI refactoring agent should know about them so it does not introduce needless replacement code.

## 10.1 Mimalloc becomes the raw-memory allocator

CPython 3.15 uses mimalloc as the default allocator for raw memory (`PyMem_RawMalloc` family), improving allocator scalability, especially in free-threaded contexts.

**Agent action:** normally none. Do not replace Python allocations with hand-rolled pooling solely because an older performance note warned about raw allocator contention. Re-profile under 3.15 first.

## 10.2 Base64/Base32/Base85 family speedups

CPython’s low-level implementations were substantially reworked. Upstream’s What’s New reports approximately:

- Base64 encoding: about **2×** faster;
- Base64 decoding: about **3×** faster;
- Base32: roughly **two orders of magnitude** faster in the cited benchmark range;
- Ascii85/Base85/Z85: roughly **two orders of magnitude** faster with dramatically lower memory use in the cited benchmark range.

The exact result depends on data size and workload; treat upstream numbers as directional, not guaranteed application speedups.

**Agent action:** before preserving a bespoke Python implementation, third-party workaround, or convoluted batching code written to compensate for old codec performance, benchmark the 3.15 stdlib implementation. Often the correct modernization is to delete complexity, not add it.

## 10.3 `csv.Sniffer.sniff()`

Delimiter detection is reported as up to about **1.6×** faster.

**Agent action:** no rewrite required. If code caches or bypasses `Sniffer` purely for historical speed reasons, re-evaluate the workaround.

## 10.4 `subprocess.Popen.wait(timeout)` becomes event-driven where supported

On Linux 5.3+, `Popen.wait(timeout=...)` can use `os.pidfd_open()` plus `poll()` instead of a POSIX busy loop. macOS/BSD use `kqueue`; Windows uses its native process wait primitive. A fallback remains for platforms without an event-driven mechanism.

```python
proc.wait(timeout=5)
```

**Arch/Linux consequence:** on a modern Linux kernel, timeout-based subprocess waiting no longer inherently implies the old polling/sleep loop overhead. Do not rewrite straightforward `wait(timeout)` into bespoke polling merely to avoid that historical implementation detail.

Still use `communicate()` rather than `wait()` when pipes can fill; the deadlock warning for undrained `stdout=PIPE`/`stderr=PIPE` remains conceptually important.

## 10.5 Shared `__dict__`/`__weakref__` descriptors

CPython now shares certain `__dict__` and `__weakref__` descriptors per interpreter instead of creating redundant descriptor objects for each class. This can reduce class-creation overhead/memory and reference-cycle pressure.

**Agent action:** automatic. Do not alter class design merely to obtain this gain.

## 10.6 Import locking improvements

Per-module import locks are acquired in a hierarchical order to avoid classes of deadlocks involving concurrent nested imports.

**Agent action:** automatic. It does not make arbitrary import-time side effects thread-safe; continue to avoid fragile concurrent initialization assumptions.

---

# 11. `bytearray.take_bytes()` — zero-copy buffer handoff

**Reference:** [`bytearray` / What’s New](https://docs.python.org/3.15/whatsnew/3.15.html#other-language-changes)

`bytearray.take_bytes(n=None, /)` transfers bytes out of a `bytearray` into an immutable `bytes` object while avoiding a data copy in the intended ownership-transfer case. The bytes are removed from the original buffer.

This is one of the most concrete 3.15 memory-and-throughput refactors for streaming/parsing/network code.

## 11.1 Whole-buffer handoff

```python
# before: allocate/copy, then clear
payload = bytes(buffer)
buffer.clear()

# after: transfer ownership
payload = buffer.take_bytes()
```

## 11.2 Prefix extraction

```python
# before
n = buffer.find(b"\n")
line = bytes(buffer[: n + 1])
del buffer[: n + 1]

# after
n = buffer.find(b"\n")
line = buffer.take_bytes(n + 1)
```

## 11.3 Keep prefix, discard remainder, then hand off

```python
n = buffer.find(b"\n")
buffer.resize(n)
prefix = buffer.take_bytes()
```

### Static-search candidates

Look for adjacent patterns such as:

```text
bytes(buf)
buf.clear()

bytes(buf[:n])
del buf[:n]

bytes(buf[:n])
buf = bytearray(...)
```

Then prove that ownership semantics permit consuming the buffer.

### Safety rule

`bytes(buffer)` leaves the original contents intact; `buffer.take_bytes()` **mutates/consumes the bytearray**. Replace only when the old code already discards/removes/reinitializes the transferred range and no alias expects those bytes to remain in the `bytearray`.

Pay special attention to aliased mutable buffers, exported buffer views, protocol parsers, retry logic, and exception paths.

---

# 12. Free-threaded CPython, `abi3t`, and thread-safe iterator utilities

**References:** [Free-threaded Python HOWTO](https://docs.python.org/3.15/howto/free-threading-python.html), [PEP 803](https://peps.python.org/pep-0803/), [PEP 788](https://peps.python.org/pep-0788/), [PEP 793](https://peps.python.org/pep-0793/), [PEP 820](https://peps.python.org/pep-0820/), [`threading`](https://docs.python.org/3.15/library/threading.html)

Python 3.15 extends the free-threading program in two distinct directions:

1. pure-Python/runtime support continues to mature;
2. native extensions gain a new stable ABI target for free-threaded builds, **`abi3t`**.

Do not conflate “Python 3.15” with “running without the GIL.” The standard GIL build remains distinct from a free-threaded build, and a free-threaded-capable process may have the GIL enabled at runtime.

## 12.1 Detect build capability and runtime state

```python
import sys
import sysconfig

print("build supports free threading:", bool(sysconfig.get_config_var("Py_GIL_DISABLED")))
print("ABI says free-threaded:", getattr(getattr(sys, "abi_info", None), "free_threaded", None))
print("GIL enabled in this process:", sys._is_gil_enabled())
```

`python -VV` / `sys.version` also identify a free-threading build. Use `sysconfig.get_config_var("Py_GIL_DISABLED")` for build-configuration decisions.

Free-threaded builds can control the GIL at process startup with the documented `PYTHON_GIL` / `-X gil` mechanisms. Importing a native extension that is not declared compatible with free threading can cause the runtime to re-enable the GIL, so validate the state **after dependency import** when it matters.

## 12.2 Performance model

Do not assume free threading makes every script faster. It removes a scalability bottleneck for CPU-bound multithreading, but carries single-thread overhead that depends on platform/workload. It is primarily valuable where parallel Python execution can offset that overhead.

Good candidates:

- CPU-bound workloads partitionable across independent threads;
- multi-threaded data transforms that spend substantial time in Python;
- services with genuine parallel CPU demand and compatible dependencies.

Weak candidates:

- single-threaded CLI scripts;
- I/O-bound code already overlapping waits effectively;
- workloads dominated by native libraries already releasing the GIL;
- dependency stacks lacking free-thread support.

**Never switch architecture from processes/async to threads solely because a free-threaded binary exists. Benchmark representative workloads and audit thread safety.** Removing the GIL does not make application data structures or logical invariants magically race-free.

## 12.3 Iterator sharing is not automatically thread-safe

The free-threading HOWTO explicitly warns that concurrently advancing the same iterator can yield unsafe behavior. Python 3.15 therefore adds three tools in `threading`:

### `threading.serialize_iterator(iterable)`

Serializes concurrent advancement of one iterator with a lock. Each source item is delivered to exactly one consuming caller.

```python
import threading

source = threading.serialize_iterator(x * x for x in range(1_000))

def worker():
    for item in source:
        process(item)

threads = [threading.Thread(target=worker) for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
```

If the wrapped iterator implements `send`, `throw`, or `close`, those operations are serialized as well.

### `@threading.synchronized_iterator`

Wraps iterator-producing callables so each returned iterator is serialized:

```python
import threading

@threading.synchronized_iterator
def jobs():
    yield from discover_jobs()
```

### `threading.concurrent_tee(iterable, n=2)`

Creates `n` independent iterators that each see the full stream and may be consumed by different threads. Values are buffered until all derived iterators have consumed them.

```python
left, right = threading.concurrent_tee(source)
```

This differs from `serialize_iterator`: `serialize_iterator` distributes items among callers; `concurrent_tee` replicates the logical stream to each derived iterator.

**Memory caveat:** `concurrent_tee` must buffer values when consumers progress at different rates. A stalled consumer can cause substantial memory retention. The agent should reject this transformation when consumer lag is unbounded unless buffering is explicitly acceptable.

Additional contract details:

- `concurrent_tee(iterable, n=0)` returns an empty tuple;
- negative `n` raises `ValueError`;
- each **derived iterator** is intended to be consumed by one thread at a time;
- if one derived iterator itself must be shared across threads, wrap that derived iterator with `threading.serialize_iterator()`.

That last distinction matters: `concurrent_tee()` synchronizes a family of independent consumers; it does not turn each returned iterator into a freely multi-consumer object.

## 12.4 Native extensions: `abi3t`

Python 3.15 introduces a stable ABI for free-threaded builds. Extensions targeting it can be compatible across free-threaded CPython versions according to Stable ABI guarantees.

Migration generally requires real source work, including:

- avoiding layouts that embed `PyObject` assumptions incompatible with the Stable ABI;
- using PEP 697 patterns such as negative `basicsize` and `PyObject_GetTypeData()` where appropriate;
- adopting the `PyModExport_*` export mechanism introduced by PEP 793;
- using the unified `PySlot` system from PEP 820.

If an extension cannot target `abi3t`, continue producing conventional `abi3` for GIL builds plus a version-specific free-threaded wheel such as `cp315t` as appropriate.

**Agent rule for pure-Python repositories:** do not edit application Python code merely because `abi3t` exists. Instead, inventory dependencies containing `.so` extensions and confirm their free-threading support before recommending a free-threaded deployment.

---

# 13. `asyncio.TaskGroup.cancel()` — direct structured early termination

**Reference:** [`asyncio.TaskGroup`](https://docs.python.org/3.15/library/asyncio-task.html#task-groups)

Python 3.15 adds `TaskGroup.cancel()` for non-exceptional early termination of a task group.

```python
import asyncio

async def run_until_found(items):
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(check(item)) for item in items]

        # some condition determines remaining work is useless
        if result_already_known():
            tg.cancel()
```

Behavioral points:

- unfinished child tasks are cancelled;
- the group’s parent/body is cancelled as part of the mechanism;
- group exit suppresses the internal `CancelledError` used for this non-exceptional group cancellation;
- `cancel()` is idempotent;
- it can be invoked before entering the group, in which case cancellation applies upon entry;
- it can be called after group exit without creating a new failure.

### Replace the old “exception injection” termination idiom

Older documentation commonly demonstrated terminating a TaskGroup by scheduling a coroutine that deliberately raises a custom exception and catching the resulting `ExceptionGroup`. In 3.15-only code, use `tg.cancel()` when the intent is simply “the group’s work is no longer needed.”

Keep exception-driven behavior when cancellation represents an actual domain failure that must be reported rather than normal early completion.

### Cancellation hygiene remains mandatory

Coroutines should generally propagate `asyncio.CancelledError` after cleanup:

```python
async def worker():
    try:
        await work()
    finally:
        await cleanup()
```

Code that catches and suppresses cancellation can break structured-concurrency expectations. Do not rewrite cancellation code without checking cleanup/finalization semantics.


## 13.1 `GeneratorExit` inside a `TaskGroup`

Python 3.15 adds a specific structured-concurrency rule for a `TaskGroup` body that raises `GeneratorExit`: if none of the child tasks raises another exception that should be reported, the `GeneratorExit` is re-raised rather than being packaged into ordinary task-group exception handling.

This matters for async generators/coroutines that own a task group and are explicitly closed. An agent modifying cancellation or cleanup code should test generator closure, not only ordinary cancellation and child exceptions.

Minimum regression matrix for a `TaskGroup` rewrite:

```text
normal completion
child failure
external cancellation
TaskGroup.cancel()
body failure
GeneratorExit / async-generator close
cleanup that itself fails
nested TaskGroups with simultaneous failures
```

---

# 14. Typing changes: PEP 728, PEP 747, PEP 800, and variadic generics

**References:** [typing docs](https://docs.python.org/3.15/library/typing.html), [PEP 728](https://peps.python.org/pep-0728/), [PEP 747](https://peps.python.org/pep-0747/), [PEP 800](https://peps.python.org/pep-0800/)

These changes are mostly static-correctness features rather than runtime performance features, but they materially improve what a modernization agent can express and verify.

## 14.1 PEP 728 — closed `TypedDict` and typed extra items

### Closed dictionaries

```python
from typing import TypedDict

class Point(TypedDict, closed=True):
    x: int
    y: int
```

A type checker can reject unspecified keys. This is useful for strict configuration objects, serialized schemas, internal protocols, and payloads where “unknown key” is a bug.

### Typed extra items

```python
class Metrics(TypedDict, extra_items=float):
    name: str

m: Metrics = {
    "name": "worker-1",
    "cpu": 0.71,
    "rss_mb": 183.2,
}
```

Unspecified keys are allowed, but their values must conform to the declared extra-item type.

`closed=` and `extra_items=` are mutually exclusive. Their state participates in inheritance according to typing rules. Runtime introspection exposes related attributes such as `__closed__` and `__extra_items__`.

### Agent candidates

Upgrade `TypedDict` declarations when comments or validators already imply one of these schemas:

- “no additional fields accepted” -> `closed=True`;
- “known core fields plus arbitrary string-valued metadata” -> `extra_items=str`;
- configuration merges where unknown key types are currently lost to `Any`.

This improves static precision but does not automatically validate runtime dictionaries. Preserve runtime schema validation where externally supplied input is untrusted.

## 14.2 PEP 747 — `TypeForm[T]`

`typing.TypeForm` annotates **values that are themselves evaluated type expressions**:

```python
from typing import Any, TypeForm


def deserialize[T](typ: TypeForm[T], value: Any) -> T:
    ...

x = deserialize(int, payload)
```

`TypeForm[T]` is more expressive than pretending every accepted type expression is simply `type[T]`; type forms can include constructs such as unions and parameterized types that are valid type expressions but are not ordinary class objects in the narrow sense.

At runtime:

```python
TypeForm(obj) is obj
```

It is primarily a static-typing marker.

Candidate APIs:

- cast-like helpers;
- serialization/deserialization functions accepting a target type expression;
- dependency injection / codec lookup based on type expressions;
- validation/adaptation frameworks that consume annotations.

## 14.3 PEP 800 — `@typing.disjoint_base`

```python
from typing import disjoint_base

@disjoint_base
class LeftDomain:
    pass

@disjoint_base
class RightDomain:
    pass

class Impossible(LeftDomain, RightDomain):  # type-checker error
    pass
```

The decorator communicates that unrelated disjoint bases should not overlap through multiple inheritance. Type checkers can use this to prove code unreachable and reason more accurately about type intersections.

This is specialized. Do not annotate ordinary classes merely to chase stronger narrowing; use it where class hierarchy semantics genuinely make bases disjoint, especially in stubs/framework internals.

## 14.4 `TypeVarTuple` parameters

`TypeVarTuple` now accepts parameters analogous to type variables, including `bound`, `covariant`, `contravariant`, and `infer_variance` in its runtime constructor signature. The precise static semantics of some combinations can depend on the typing specification/tooling state.

**Agent rule:** only introduce advanced variadic bounds/variance when the project’s target type checker demonstrably supports the intended semantics. Runtime acceptance is not sufficient evidence of portable static behavior.

---

# 15. Other core language/runtime changes worth scanning for

## 15.1 `slice` is subscriptable/generic

The `slice` class supports runtime subscription consistently with typing use. This enables more precise annotations in 3.15-only code where slice component types matter to an API.

Do not add verbose slice generics when they provide no useful static information.

## 15.2 Expanded `__slots__` flexibility

Python 3.15 allows `__slots__` configurations that were previously rejected in additional cases, including broader support for declaring `__dict__` / `__weakref__` and allowing slots on tuple-derived classes (including classes related to `namedtuple`).

This can unblock memory-conscious class designs, but **do not mass-convert classes to `__slots__`**. Such a migration changes dynamic attribute behavior, weak-reference capabilities, multiple-inheritance constraints, serialization/debugging assumptions, and subclass semantics. Profile object counts/RSS first.

## 15.3 Complex buffer formats

`memoryview` gains support for complex-number PEP 3118-style formats `Zf` and `Zd`. The `array` module likewise gains complex array format support and the half-precision float format `e`.

This is valuable for binary/scientific interop, but do not convert ordinary numeric containers merely because the formats exist. Prefer them when the data source/protocol already uses compatible packed representations and avoiding conversion matters.

## 15.4 `bytes.replace()` keyword `count`

The replacement count can be passed as a keyword in 3.15:

```python
blob.replace(old, new, count=1)
```

This is a readability improvement, not a performance transformation.

## 15.5 Unary plus in match literal patterns

Literal patterns accept unary `+` where syntactically appropriate. This removes an incidental asymmetry; no optimization action is generally required.

## 15.6 More numeric types accepted for timeouts/timestamps

Several timestamp/timeout APIs broaden acceptance toward `numbers.Real`-compatible inputs such as `Decimal`/`Fraction` where documented. This improves API interoperability but **does not imply increased underlying clock precision**. Avoid introducing `Decimal` merely to claim more precise scheduling unless the target API preserves that precision.

## 15.7 Warning filters can express regex fields more directly

Python 3.15 extends `-W` / `PYTHONWARNINGS` parsing so message/module fields can use regex-oriented forms when delimited as documented. This makes command-line deprecation triage more surgical.

Use this to isolate project warnings during migration instead of globally suppressing entire warning classes.

## 15.8 Import metadata cleanup

The import system no longer relies on module `__cached__` in the old way; modern import metadata should be obtained from `module.__spec__` (`__spec__.cached`, `__spec__.loader`, etc.). `__loader__` and `__package__` are on a deprecation path as primary import-system metadata.

Agent search:

```text
.__cached__
.__loader__
.__package__
```

When code is performing importer/tooling introspection, migrate toward `__spec__` rather than adding compatibility shims around legacy attributes.

## 15.9 Stable-ABI shared-object names may include multiarch information

Stable-ABI extension filenames can encode a multiarch tuple in supported configurations. Any custom plugin discovery that assumes a single hard-coded `.abi3.so` suffix should use import machinery/sysconfig rather than filename string slicing.

## 15.10 Colorized CLI output expands

More standard-library CLIs and diagnostic surfaces emit color by default where appropriate. This can affect snapshot tests, log capture, parser scripts, and terminal/nonterminal assumptions. Honor Python’s documented color-control environment variables instead of stripping ANSI escape sequences ad hoc after the fact.

---

# 16. Standard-library change catalog: 3.15 features with migration significance

This section is deliberately broad. Entries marked **automatic** generally need no code change; entries marked **candidate** are worth searching for; entries marked **behavior** deserve regression tests.

## 16.1 New module: `math.integer` (PEP 791)

**References:** [PEP 791](https://peps.python.org/pep-0791/), [`math.integer`](https://docs.python.org/3.15/library/math.integer.html)

Exact integer functions now live in a dedicated submodule:

```python
from math.integer import comb, factorial, gcd, isqrt, lcm, perm
```

They accept integers and `__index__()`-supporting objects and return exact integers. The corresponding aliases in `math` remain for compatibility but are soft-deprecated in 3.15.

**3.15-only modernization:** prefer `math.integer` for new code or when touching imports anyway.

```python
# before
from math import gcd, isqrt

# 3.15-only
from math.integer import gcd, isqrt
```

Do not churn code that still supports older Python solely to follow the new namespace; the existing `math` aliases continue to work.

## 16.2 `argparse`

- `BooleanOptionalAction` supports single-dash long options and alternate prefix characters.
- `ArgumentParser(..., suggest_on_error=...)` now defaults to `True`, so typo suggestions are enabled by default.
- Description, epilog, and help text gain backtick/double-backtick inline-code markup in color-capable output.
- **Porting concern:** destination inference for combinations involving short options and single-dash long options has changed; if downstream code depends on a particular `.dest`, specify `dest=` explicitly rather than relying on inference.

Agent action: scan parsers that use unconventional one-dash long flags or snapshot exact help/error output.

## 16.3 `array`

- adds complex C formats `'Zf'` and `'Zd'`;
- adds IEEE-754 half-float format `'e'`;
- `array.typecodes` changes from a string to a tuple because type codes can now be multi-character.

**Behavior:** code that iterates `array.typecodes` expecting individual characters or uses string-only operations on it must be updated.

## 16.4 `ast`

- `ast.dump(..., color=...)` can return syntax-highlighted ANSI output;
- `python -m ast` output is colorized by default when appropriate;
- compile/parse APIs gain module-name support useful for precise warning filtering.

Snapshot/parsing tools should request noncolored output when machine-readable stability matters.

## 16.5 `asyncio`

- `TaskGroup.cancel()` provides first-class early group termination.
- 3.15 also contains correctness fixes accumulated through the release cycle around cancellation, task groups, eager execution, and event-loop behavior; code depending on prior accidental edge cases should be regression-tested rather than emulated.

## 16.6 `base64`

New parameters substantially broaden explicit format control:

- `z85encode(..., pad=...)`;
- `padded=` for Base32/Base32hex/Base64/URL-safe Base64 encode/decode;
- `wrapcol=` across several encoders;
- `ignorechars=` across several decoders;
- `canonical=` for strict rejection of noncanonical forms/non-zero padding bits.

**Porting behavior:** URL-safe Base64 decoding no longer has exactly the same implicit-padding assumptions older code may have enforced. If protocol conformance requires padded input, request the appropriate `padded=` behavior explicitly.

**Security/protocol guidance:** when inputs cross trust boundaries and a protocol requires canonical encodings, consider `canonical=True` rather than silently accepting alternative representations.

## 16.7 `binascii`

Adds low-level C-backed functions for:

- Base32: `b2a_base32()`, `a2b_base32()`;
- Ascii85: `b2a_ascii85()`, `a2b_ascii85()`;
- Base85: `b2a_base85()`, `a2b_base85()`;
- related Z85 support through the expanded API family;
- `padded=`, `wrapcol=`, `alphabet=`, `ignorechars=`, and `canonical=` controls on applicable functions.

These underlie major automatic codec performance gains. Prefer stdlib implementations over bespoke Python loops unless a benchmark demonstrates a real need.

## 16.8 `calendar`

- richer colorized CLI output;
- HTML CLI accepts year-month selection;
- `HTMLCalendar` output is modernized to HTML5 and dark-mode aware.

Potential regression surface: golden HTML output tests.

## 16.9 `collections.Counter`

Counters support symmetric difference through `^` / `^=` semantics.

Candidate transformation: custom “items in exactly one Counter” code may be replaceable by the built-in operation, subject to Counter’s positive-count semantics.

## 16.10 `concurrent.futures`

`ProcessPoolExecutor` reports PID and exit code when a worker process terminates abruptly. This improves diagnostics automatically.

Agent action: remove fragile exception-string scraping that existed solely to recover this information, if tests show the new structured/traceback information suffices.

## 16.11 `contextlib`

- `ExitStack` / `AsyncExitStack` support arbitrary descriptors for enter/exit protocol methods, aligning behavior with `with`/`async with`.
- `ContextDecorator` / `AsyncContextDecorator`, including `@contextmanager`/`@asynccontextmanager` used as decorators, now keep the context open across **generator iteration, coroutine awaiting, and async-generator iteration** instead of closing immediately after the generator/coroutine object is created.

The second item is a **behavioral correctness change**. Test decorators wrapped around generators/coroutines if code relied on the old accidental lifetime.

## 16.12 `ctypes`

Complex scalar `_type_` codes change:

- `c_float_complex`: `F` -> `Zf`;
- `c_double_complex`: `D` -> `Zd`;
- `c_longdouble_complex`: `G` -> `Zg`.

This aligns with NumPy conventions. Audit metaprogramming that branches on `_type_` string literals.

## 16.13 `dataclasses`

Generated `__init__` annotations no longer expose internal type names. This is automatic, but introspection tests comparing exact annotation representations may change.

## 16.14 `dbm`

`dbm.dumb` and `dbm.sqlite3` gain `reorganize()` to reclaim free space left by deleted entries.

Candidate maintenance task for long-lived mutable databases:

```python
with dbm.open(path, "c") as db:
    ...
    if hasattr(db, "reorganize"):
        db.reorganize()
```

Do not invoke on every mutation; compaction trades I/O/work now for recovered space.

## 16.15 `difflib`

- `unified_diff(..., color=...)` can produce Git-like colored output;
- `HtmlDiff` output styling/HTML standard is modernized.

Machine-consumed diffs should keep color disabled.

## 16.16 `email`

Email generation now errors when an `EmailMessage` cannot be flattened accurately because a non-ASCII mailbox appears in an address header without appropriate EAI handling. Use `EmailPolicy.utf8` where internationalized email is intentionally supported.

**Behavior/security:** do not work around this by lossy ASCII coercion. Choose an explicit mail policy compatible with the receiving infrastructure.

## 16.17 `faulthandler`

`max_threads=` is added to `enable()`, `dump_traceback()`, `dump_traceback_later()`, and `register()`.

Useful for very high-thread-count services where dumping every stack is excessive. For diagnostics, choose a limit that preserves the threads most relevant to the failure model.

## 16.18 `functools`

`singledispatchmethod()`:

- accepts non-descriptor callables;
- dispatches on the second argument in the corrected class-attribute regular-method case.

Regression-test sophisticated dispatch metaprogramming; ordinary method use should simply become more correct.

## 16.19 `gc`

Python 3.15 uses the **generational collector model** restored from the 3.13 line after the short-lived 3.14.0–3.14.4 incremental collector caused production memory-pressure problems.

Additionally, GC callbacks in 3.15 expose richer information including collection `duration` and the number of `candidates` considered.

```python
import gc


def observe(phase, info):
    if phase == "stop":
        print(info.get("generation"), info.get("duration"), info.get("candidates"))

gc.callbacks.append(observe)
```

**Agent rule:** remove/tune GC thresholds only from measurements on the 3.15 collector. Advice derived from the transient early-3.14 collector can be actively misleading.

## 16.20 `hashlib`

Algorithms documented as guaranteed names exist as module attributes even if a particular backend cannot actually provide the algorithm at runtime. Code should catch/handle the operational failure rather than using `hasattr(hashlib, "md5")` as a capability test.

## 16.21 `http.client`

`HTTPConnection` / `HTTPSConnection` add `max_response_headers=` to override the response-header count limit.

Use only where protocol/business requirements justify it. Raising parser limits can increase exposure to resource-exhaustion attacks; lowering them can be a defensive policy.

## 16.22 `http.server`

- colored request logging;
- configurable `default_content_type` / CLI `--content-type` for unknown extensions;
- `SimpleHTTPRequestHandler(..., extra_response_headers=...)` support;
- CLI `-H/--header` for extra response headers.

Useful for development tooling, but `http.server` remains a simple server, not a production hardening substitute.

## 16.23 `importlib.metadata`

Malformed/incomplete distribution metadata directories without a metadata file now raise `MetadataNotFound` instead of returning an empty metadata object.

**Behavior:** catch the specific exception where absence/corruption is expected; do not rely on “empty metadata means missing.”

## 16.24 `inspect`

`inspect.getdoc()` gains `inherit_class_doc` and `fallback_to_class_doc` controls, giving introspection/documentation tools finer control over class-doc inheritance/fallback.

## 16.25 `json`

`load()` / `loads()` add `array_hook=`:

```python
immutable = json.loads(
    source,
    object_pairs_hook=frozendict,
    array_hook=tuple,
)
```

Candidate uses: immutable configuration snapshots, cacheable parsed documents, validation pipelines that should forbid accidental mutation.

Avoid if downstream code intentionally mutates parsed lists/dicts.

## 16.26 `locale`

- locale language codes preserve/support `@` modifiers in `setlocale()`/`getlocale()`;
- `locale.getdefaultlocale()` is undeprecated.

Test code that normalized locale strings by assuming modifiers were stripped.

## 16.27 `math`

Adds:

- `math.isnormal()`;
- `math.issubnormal()`;
- `math.fmax()`;
- `math.fmin()`;
- `math.signbit()`.

These can replace custom IEEE-754 edge-case helpers. In particular, `signbit()` is superior to naive `< 0` when the sign of `-0.0` matters.

Integer-only helpers move conceptually to `math.integer`; the aliases in `math` are soft-deprecated.

## 16.28 `mimetypes`

The database expands and changes mappings, including:

- `application/x-texinfo` -> `application/texinfo`;
- `.ai` -> `application/pdf`.

Snapshot/protocol code that asserts exact MIME strings should be updated intentionally rather than pinning old database mistakes.

## 16.29 `mmap`

On Linux 5.17+, `mmap.mmap.set_name()` can annotate anonymous mappings, improving `/proc`/debugger/profiler observability.

Candidate for applications that create large anonymous mappings and need memory-attribution diagnostics.

## 16.30 `os`

### `os.statx()` — Linux

Python exposes Linux `statx()` where supported (kernel >= 4.11 and compatible glibc):

```python
result = os.statx(path, mask, flags=0, follow_symlinks=True)
```

Use it when you need `statx`-specific metadata/masks or want to avoid retrieving unnecessary fields according to platform semantics. Keep portable `os.stat()` when portability or ordinary metadata suffices.

### `os.makedirs(parent_mode=...)`

Intermediate-directory modes can now be specified explicitly:

```python
os.makedirs(path, mode=0o750, parent_mode=0o750, exist_ok=True)
```

Remember that process `umask` still participates in permission outcomes.

## 16.31 `os.path`

`realpath(strict=os.path.ALLOW_MISSING)` resolves symlinks as far as possible, allows missing path components, but re-raises errors other than `FileNotFoundError` and returns a path intended to be symlink-free for the resolved portion.

Useful in secure path normalization workflows where the final target may not yet exist. Do not confuse canonicalization with authorization; still enforce directory/ownership/access policy.

## 16.32 `pathlib`

`Path.mkdir(..., parents=True, parent_mode=...)` gains the same intermediate-directory permission control as `os.makedirs`.

## 16.33 `pdb`

The newer interactive shell becomes the default input shell. Expect richer interactive behavior; automated debugger scripts should avoid relying on incidental terminal formatting.

## 16.34 `pickle`

Private methods and nested classes gain improved pickling support.

This may allow deletion of custom reducers/workarounds, but remove them only after round-trip and cross-process tests. Pickle remains unsafe for untrusted data.

## 16.35 `pickletools`

CLI output gains color where appropriate. Disable color for machine parsing.

## 16.36 `pprint`

- `expand=` can produce JSON-like expanded formatting when indentation is supplied;
- t-string objects gain pretty-print support.

Primarily readability/debugging improvements.

## 16.37 `re`

Adds clearer aliases:

```python
re.prefixmatch(pattern, text)
compiled.prefixmatch(text)
```

They mean what Python historically called `match`: match at the beginning of the string. `re.match()` / `Pattern.match()` are **soft-deprecated with no planned removal**.

For 3.15-only touched code, `prefixmatch` may improve semantic clarity. Do not churn a large codebase solely to eliminate a soft deprecation, especially if older-version support matters.

## 16.38 `resource`

New platform-dependent limit constants include `RLIMIT_NTHR`, `RLIMIT_UMTXP`, `RLIMIT_THREADS`, `RLIM_SAVED_CUR`, and `RLIM_SAVED_MAX` where available.

Guard with feature checks for cross-platform code.

## 16.39 `shelve`

- `reorganize()` reclaims storage after deletions;
- custom serialization/deserialization functions are supported.

The latter can eliminate wrappers that manually encode every value before insertion. Preserve security properties: custom deserializers must treat persisted bytes according to the trust model.

## 16.40 `socket`

Adds constants for ISO-TP CAN. Relevant to Linux/CAN applications; otherwise no migration action.

## 16.41 `sqlite3`

The CLI gains richer completion (keywords, tables, indexes, triggers, views, columns, functions, schemas) and colorized prompts/help/errors.

Python API **signature porting changes** are covered in §18; those matter more to scripts than the CLI enhancements.

## 16.42 `ssl`

3.15 expands TLS controls, with availability depending on the OpenSSL version used by the interpreter:

- `ssl.HAS_PSK_TLS13` for TLS 1.3 external PSK capability;
- `SSLContext.set_groups()`;
- `SSLSocket.group()` (OpenSSL-version dependent);
- `SSLContext.get_groups()` (newer OpenSSL required);
- `SSLContext.set_ciphersuites()` for TLS 1.3 cipher suites; continue using `set_ciphers()` for <= TLS 1.2;
- signature-algorithm inspection/configuration APIs, including `ssl.get_sigalgs()`, client/server signature policy methods, and selected-algorithm accessors where supported.

**Arch rule:** inspect the actual runtime OpenSSL capabilities; do not assume every 3.15 `ssl` symbol/functionality is available merely from the Python version.

Security-sensitive TLS configuration should follow current protocol policy, not “enable every new knob.”

## 16.43 `subprocess`

`Popen.wait(timeout=...)` is event-driven on supported modern Linux/BSD/macOS rather than polling. See §10.4.

## 16.44 `symtable`

Adds `Function.get_cells()` and `Symbol.is_cell()` for closure/cell-variable introspection. Useful to static-analysis, compiler, debugger, and code-transformation tooling.

## 16.45 `sys`

Adds `sys.abi_info`, a structured namespace for ABI facts, including free-threading state. Prefer this structured interface over brittle parsing of filenames/version strings when deciding ABI behavior.

## 16.46 `sys.monitoring`

Exception-related monitoring events (`PY_THROW`, `PY_UNWIND`, `RAISE`, `EXCEPTION_HANDLED`, `RERAISE`) can now be disabled per code object by returning `DISABLE` from callbacks.

This can reduce instrumentation overhead when a tool determines an event is uninteresting for a particular code object. Monitoring/profiling agents should exploit per-code disabling rather than keeping globally hot callbacks alive unnecessarily.

## 16.47 `tarfile`

3.15 incorporates multiple extraction-filter/path-handling hardening changes, including normalized symlink-target checking, reapplication of filters during link fallbacks, a `LinkFallbackError`, correct rejection behavior even at permissive error levels, and platform path normalization fixes.

**Agent rule:** delete home-grown extraction bypasses/workarounds that weaken stdlib filters. For untrusted archives, keep using an appropriate extraction filter and regression-test path traversal/link cases.

## 16.48 `threading`

Adds `serialize_iterator`, `synchronized_iterator`, and `concurrent_tee`; see §12.3.

## 16.49 `timeit`

`Timer.autorange()` has configurable target timing and the CLI gains `--target-time`. This is useful for making microbenchmarks long enough to overcome timer noise without manually guessing loop counts.

Use microbenchmarks only after a profiler establishes that the measured operation matters to whole-program time.

## 16.50 `tkinter`

- `Text.search()` gains `nolinestop` and `strictlimits`;
- new `Text.search_all()`;
- `pack_content()`, `place_content()`, `grid_content()` replace outdated “slave” terminology in the underlying Tk command naming;
- `Event.user_data` and `Event.detail` are exposed in additional cases.

Relevant to GUI scripts; otherwise no migration action.

## 16.51 `tokenize`

CLI output is colorized by default when appropriate. Keep color disabled for machine pipelines.

## 16.52 `tomllib` — TOML 1.1

Parser support advances to TOML 1.1 while remaining backward-compatible with valid TOML 1.0. New accepted syntax includes:

- multiline inline tables and trailing commas;
- `\xHH` byte-range escapes;
- `\e` escape character notation;
- optional seconds in local datetime/time syntax.

If application configuration intentionally targets strict TOML 1.0 interoperability with other parsers, continue validating against that external constraint; Python accepting a 1.1 construct does not mean every consumer will.

## 16.53 `types`

Exposes `types.FrameLocalsProxyType`, the write-through locals-proxy type associated with `frame.f_locals` under PEP 667 semantics. Useful for debugger/introspection tooling; ordinary application code should rarely need explicit type checks against it.

## 16.54 `typing`

Beyond the PEPs in §14:

- invalid `Protocol` type-parameter declarations that omit inherited variables now raise `TypeError` in cases that were previously accepted incorrectly;
- runtime ordering of protocol type parameters is corrected in certain inheritance patterns;
- `TypeVarTuple` constructor options expand.

Metaprogramming that constructs protocols dynamically should be tested under 3.15 rather than assuming previous permissiveness.

## 16.55 `unicodedata`

Unicode data updates to **Unicode 17.0.0** and adds APIs including:

- `isxidstart()` / `isxidcontinue()` for Unicode identifier properties;
- `iter_graphemes()` for grapheme-cluster iteration;
- `grapheme_cluster_break()`;
- `indic_conjunct_break()`;
- `extended_pictographic()`;
- `block()`.

High-value modernization: replace hand-rolled “character” iteration when user-perceived grapheme clusters matter (cursor movement, truncation, UI slicing). Python `len(str)` still counts code points, not grapheme clusters.

## 16.56 `unittest`

- `assertLogs()` accepts a formatter;
- `assertWarns()` / `assertWarnsRegex()` no longer swallow unrelated warnings and now support nested contexts correctly.

**Behavior:** tests that accidentally depended on unrelated warnings being swallowed may newly surface them. Fix the code/warning filters rather than suppressing globally.

## 16.57 `urllib.parse`

Parsing/assembly functions gain options to distinguish **missing** from **present-but-empty** URI components (`missing_as_none`, `keep_empty`). This matters in signature generation, canonicalization, security policy, and protocols where `//host/path`, empty query, absent query, etc. are semantically distinct.

## 16.58 `venv`

On POSIX systems where `sys.platlibdir != "lib"`, venv creation now creates appropriate platform-library directories instead of relying on a `lib64 -> lib` symlink pattern. Custom tooling that hard-codes venv `lib`/`lib64` layouts should use `sysconfig` paths.

## 16.59 `warnings`

Module filtering in `warnings.warn_explicit()` is improved when no explicit module name is supplied. Combined with regex-aware `-W`/`PYTHONWARNINGS`, 3.15 makes targeted migration warning audits more reliable.

## 16.60 `wave`

- IEEE floating-point WAVE is supported;
- explicit format getters/setters are added;
- `setparams()` accepts format-inclusive parameter tuples as documented;
- float WAVE output writes the required `fact` chunk.

This can replace third-party/simple custom float-WAV writers in small scripts; benchmark or preserve specialized libraries when they provide broader codec/container support.

## 16.61 `xml`

Adds:

```python
xml.is_valid_name(text)
xml.is_valid_text(text)
```

These provide direct preflight checks for XML element/attribute naming and text validity.

## 16.62 `xml.parsers.expat`

Expat parser objects expose tunables for allocation-amplification protections, including thresholds/max amplification. These are security/resource-control features; do not relax them for untrusted XML merely to accommodate pathological input without understanding the denial-of-service implications.

## 16.63 `zlib`

Adds checksum composition:

```python
zlib.adler32_combine(...)
zlib.crc32_combine(...)
```

Useful when chunks are checksummed independently and a combined checksum is needed without re-reading/concatenating all bytes. This can be a real performance win in chunked pipelines.

---

# 17. Porting and compatibility changes: mandatory audit before performance refactors

Performance work is wasted if the resulting program is subtly incompatible with 3.15. Run this audit **before** broad modernization. The items below are source-level or behavioral changes documented for 3.15; convert them into static-search rules where practical.

## 17.1 Removals that can break existing Python code immediately

### `ast`: malformed node construction is now an error

Constructors for AST nodes now raise `TypeError` when required fields are omitted or unknown keyword arguments are supplied. Code generators, linters, AST transforms, macro-like tools, and test fixtures that relied on permissive construction must construct structurally valid nodes.

Agent search targets:

```text
ast.<Node>(
ast.AST(
ast.expr(
```

Do not blanket-edit valid constructors. Parse/instantiate representative generated trees under 3.15 and run `ast.fix_missing_locations()` only for location metadata, not as a substitute for required semantic fields.

### `ctypes.SetPointerType()` removed

The undocumented, long-deprecated `ctypes.SetPointerType()` no longer exists. Any use is a hard migration target; redesign around documented pointer types/functions rather than emulating the private behavior.

### `datetime.strptime()`: day-of-month without year now fails

A format containing `%d` without a year directive now raises `ValueError`.

Bad:

```python
from datetime import datetime

datetime.strptime("31/12", "%d/%m")
```

Use an explicit year when parsing a complete date, or parse a month/day as domain data and inject a deliberate reference year. Do **not** silently choose the current year if leap-day semantics matter.

`%e` without a year is deprecated in 3.15 and scheduled for removal in 3.17; fix both `%d` and `%e` patterns in the same pass.

### `glob.glob0()` / `glob.glob1()` removed

Replace undocumented helpers with public `glob.glob()`/`glob.iglob()` and `root_dir=` where appropriate.

### `http.server` CGI support removed

`CGIHTTPRequestHandler` and `python -m http.server --cgi` are gone. Do not re-create this using ad hoc subprocess CGI dispatch; use a maintained application server/framework when dynamic request handling is required.

### import loaders: `load_module()` removed

Custom import loaders must implement the modern import protocol (`create_module()` when needed and `exec_module()`). The import system no longer invokes `Loader.load_module()`.

Agent searches:

```text
load_module(
class .*Loader
importlib.abc.Loader
```

Inspect custom meta-path/path-entry importers especially carefully; these can also interact with 3.15 lazy import timing.

### `importlib.resources.files(package=...)` keyword removed

The deprecated `package=` parameter name is removed. Pass the anchor argument according to the current `importlib.resources.files()` signature rather than depending on the old keyword.

### `pathlib.PurePath.is_reserved()` removed

Use `os.path.isreserved()` where Windows-reserved-name detection is semantically necessary. On Arch/Linux this often appears only in cross-platform utilities, but it can still break imports/tests.

### `platform.java_ver()` removed

Remove or replace Java-runtime probing with a purpose-specific mechanism if the code still needs it.

### `sre_compile`, `sre_constants`, `sre_parse` removed

These were private implementation modules. Any direct import must be eliminated. Prefer documented `re` APIs; if a tool genuinely needs regex-parser internals, isolate that dependency and accept that it is version-coupled rather than pretending it is stable.

### `sysconfig.is_python_build(check_home=...)` parameter removed

Drop `check_home`; update callers to the current signature.

### `threading.RLock` arbitrary arguments removed

Do not instantiate `RLock` with unrelated positional/keyword arguments that older C implementations happened to tolerate.

### `types.CodeType.co_lnotab` removed

Use `code.co_lines()` for line-table information. Debuggers, coverage tools, profilers, tracers, bytecode analyzers, and source-mapping utilities are the most likely affected code.

### `typing` removals

Modernize these patterns:

```python
# removed undocumented keyword-field NamedTuple form
Point = NamedTuple("Point", x=int, y=int)

# use
Point = NamedTuple("Point", [("x", int), ("y", int)])
# or class syntax

# removed zero-field forms
TD = TypedDict("TD")
TD = TypedDict("TD", None)

# use
TD = TypedDict("TD", {})
# or class TD(TypedDict): pass
```

`typing.no_type_check_decorator` is removed. `typing.ByteString` is no longer exported through `typing.__all__` and is headed for removal in 3.17; prefer `collections.abc.Buffer` for runtime buffer-protocol checks or concrete unions / `Buffer` for annotations.

### `wave` marker APIs removed

`getmark()`, `setmark()`, and `getmarkers()` on WAVE reader/writer objects are removed.

### `zipimport.zipimporter.load_module()` removed

Use `exec_module()` and the modern loader protocol.

## 17.2 Behavior changes that may not fail loudly

### SQLite argument kinds are stricter

Python 3.15 cleans up `sqlite3.Connection` signatures:

- for `sqlite3.connect()`, every parameter except `database` is keyword-only;
- the first three parameters of `Connection.create_function()` and `create_aggregate()` are positional-only;
- the first parameter of `set_authorizer()`, `set_progress_handler()`, and `set_trace_callback()` is positional-only.

This is ideal for an AST-aware codemod because the required rewrite is syntactic but call-site-specific.

Example:

```python
# fragile/old positional style
con = sqlite3.connect(path, 5.0, 0, None, False)

# 3.15-oriented explicit form
con = sqlite3.connect(
    path,
    timeout=5.0,
    detect_types=0,
    isolation_level=None,
    check_same_thread=False,
)
```

For user-defined SQL functions, preserve required leading parameters as positional arguments rather than converting everything mechanically to keywords.

### `resource.RLIM_INFINITY` is always positive

Do not hard-code historical negative sentinel values such as `-1` or `-3`. Use `resource.RLIM_INFINITY` itself. Passing corresponding negative integers to `setrlimit()`/`prlimit()` is deprecated.

### `mmap.resize` can be absent rather than fail at runtime

On platforms lacking the necessary syscall, `mmap.mmap.resize` is removed instead of existing and raising `SystemError`. Capability checks should therefore use normal attribute detection:

```python
if hasattr(mm, "resize"):
    mm.resize(new_size)
else:
    ...
```

On Linux/Arch this is primarily relevant to cross-platform code and test suites.

### `ElementTree.iterparse()` resource ownership is enforced more visibly

If `iterparse()` itself opened the file, leaving its iterator unclosed can emit `ResourceWarning`. Prefer deterministic cleanup:

```python
from contextlib import closing
from xml.etree.ElementTree import iterparse

with closing(iterparse(path, events=("start", "end"))) as events:
    for event, elem in events:
        ...
```

Or call the iterator's `close()` explicitly when abandoning iteration early.

### `argparse` destination inference changes

For an option with both a short name and a single-dash long name, the longer form now determines `dest`:

```python
parser.add_argument("-f", "-foo")
# 3.15 inferred dest: "foo"; older behavior inferred "f"
```

If code reads `namespace.f`, specify `dest="f"` explicitly or migrate consumers to `namespace.foo` deliberately. Search for unusual single-dash multi-character options.

### URL-safe Base64 no longer requires padding by default

`base64.urlsafe_b64decode()` accepts unpadded input. If canonical/padded representation is part of validation or a protocol contract, pass `padded=True` or use `base64.b64decode(..., altchars=b"-_")` in a compatibility-oriented implementation.

Do not confuse **acceptance** with **canonicalization**: security-sensitive token code may need to reject alternate spellings even if the decoder can consume them.

### `unittest.assertWarns*()` no longer masks unrelated warnings

Tests can now expose warnings previously swallowed by `assertWarns()`/`assertWarnsRegex()`. Treat newly visible warnings as evidence; do not install a blanket ignore filter just to make the suite green.

# 18. New deprecations and future-removal debt: fix while touching the code

A 3.15 modernization pass is an opportunity to eliminate APIs already on the clock. This reduces the chance that the same scripts need another mechanical migration immediately afterward.

## 18.1 Newly deprecated in 3.15

### Abstract AST node construction

Instantiating abstract nodes such as `ast.AST` or `ast.expr` is deprecated and scheduled to become an error in 3.20. Construct concrete node classes.

### Alternative-alphabet Base64 permissiveness

When an alternative Base64 alphabet is selected, accepting literal `+` and `/` is deprecated. In future strict mode this will be an error; non-strict mode will discard them. Token/credential parsers should define one canonical alphabet and validate it.

### `-b` / `-bb` and `BytesWarning`

The command-line byte/string transition aids are deprecated and become no-ops in 3.17. Replace their role with static typing, explicit tests, and clear bytes/text boundaries.

### `collections.abc.ByteString` / `typing.ByteString`

Mere import/access now emits deprecation warnings; removal is scheduled for 3.17. Runtime protocol test:

```python
from collections.abc import Buffer

if isinstance(obj, Buffer):
    ...
```

For annotations, prefer `collections.abc.Buffer` when “supports buffer protocol” is really the contract, or an explicit union such as `bytes | bytearray | memoryview` when concrete semantics matter.

### `hashlib` `string=` keyword

Pass initial hash data positionally. `string=` is scheduled for removal in 3.19.

```python
# avoid
hashlib.sha256(string=data)

# use
hashlib.sha256(data)
```

### `http.cookies.*.js_output()`

Deprecated, removal in 3.19. Use the regular `output()` APIs and perform context-appropriate browser/HTML/JavaScript encoding at the proper layer rather than relying on this legacy helper.

### Mutating `imaplib.IMAP4.file`

Deprecated, removal in 3.19. The property is no longer the implementation hook it once was.

### `profile` module

Deprecated, removal in 3.17. Use `profiling.tracing` for deterministic/tracing profiling and `profiling.sampling`/Tachyon for statistical sampling.

### `re.match()` soft-deprecated for new 3.15-only code

`re.prefixmatch()` and `Pattern.prefixmatch()` are the clearer names for Python's longstanding “match only at the beginning” behavior. **There is no removal plan for `match()`**. Therefore:

- if supporting older Python releases, retaining `re.match()` is rational;
- in a 3.15-only codebase, prefer `prefixmatch()` when it improves semantic clarity;
- do not mass-rewrite solely to chase a soft deprecation if compatibility/readability worsens.

### `struct` complex format spelling

`'F'` and `'D'` are soft-deprecated in favor of `'Zf'` and `'Zd'`. Also avoid unusual direct `Struct.__new__()`/re-initialization patterns; those are scheduled for removal in 3.20.

### Runtime protocol checks

Calling `isinstance()`/`issubclass()` on a protocol that merely inherits from a runtime-checkable protocol but is not itself explicitly decorated with `@runtime_checkable` is deprecated and will become `TypeError` in 3.20. Mark every protocol intended for runtime checking explicitly.

### Standard-library version attributes

A long list of stdlib `__version__`, `version`, or `VERSION` attributes is deprecated for removal in 3.20. Do not use a module-local version constant to discover the Python runtime. Use:

```python
import sys
print(sys.version_info)
```

For **third-party package** versions, use distribution metadata instead:

```python
from importlib.metadata import version
print(version("distribution-name"))
```

The affected stdlib modules include `argparse`, `csv`, `ctypes`, `decimal` (where `decimal.SPEC_VERSION` remains the relevant decimal-spec version), `http.server`, `imaplib`, `ipaddress`, `json`, `logging` (including `__date__`), `optparse`, `pickle`, `platform`, `re`, `socketserver`, `tabnanny`, `tarfile`, `tkinter.font`, `tkinter.ttk`, `wsgiref.simple_server`, `xml.etree.ElementTree`, `xml.sax.expatreader`, `xml.sax.handler`, and `zlib`.

## 18.2 Deprecation horizon: remove these while the agent already has context

### Scheduled for Python 3.16

Prioritize:

- import machinery that sets `module.__loader__` without a coherent `module.__spec__.loader`;
- `array('u')` → use `'w'` for `Py_UCS4` Unicode characters;
- `asyncio.iscoroutinefunction()` → `inspect.iscoroutinefunction()`;
- the `asyncio` policy system → `asyncio.run(..., loop_factory=...)` or `asyncio.Runner(loop_factory=...)`;
- `~True` / `~False` → `not x` for logical negation, `~int(x)` only when integer bit inversion is intentional;
- `functools.reduce(function=..., sequence=...)` keyword use → positional leading arguments;
- custom logging handlers using `strm=` → `stream=`;
- undotted extensions passed to `MimeTypes.add_type()` → include `.`;
- `shutil.ExecError` → `RuntimeError` or a domain-specific exception;
- `symtable.Class.get_methods()`;
- `sysconfig.expand_makefile_vars()` → `sysconfig.get_paths(vars=...)`;
- undocumented `TarFile.tarfile`.

### Scheduled for Python 3.17

In addition to `profile` and `ByteString`:

- `datetime.strptime()` `%e` without a year;
- non-ASCII encoding names to `encodings.normalize_encoding()`;
- private `typing._UnionGenericAlias` inspection → `typing.get_origin()` / `typing.get_args()`;
- old `tkinter.Variable.trace*` APIs → `trace_add()`, `trace_remove()`, `trace_info()`;
- `webbrowser.MacOSXOSAScript` (macOS only);
- C-API `bytes_warning`.

### Scheduled for Python 3.18

- passing `bool` where a file descriptor is expected;
- undocumented `Decimal` format code `'N'`;
- executable `import ...` lines in `.pth` files are silently ignored under the PEP 829 transition.

### Scheduled for Python 3.19

- `ctypes` non-Windows `_pack_` behavior that implicitly selects MSVC layout without an explicit `_layout_`;
- `hashlib` `string=`;
- cookies `js_output()`;
- mutation of `IMAP4.file`;
- external string-hash scheme support in the C API.

### Scheduled for Python 3.20

- abstract AST-node instantiation;
- the deprecated stdlib version attributes listed above;
- unusual `struct.Struct` construction/re-initialization;
- runtime checks against protocols not explicitly marked `@runtime_checkable`;
- PEP 829's strict `.pth` transition rules described below;
- private C identifier helpers `_PyObject_CallMethodId()`, `_PyObject_GetAttrId()`, `_PyUnicode_FromId()`;
- `Py_MATH_El` / `Py_MATH_PIl`.

## 18.3 Unscheduled but real deprecation debt

Search and modernize when encountered:

- nested `argparse` argument groups / mutually-exclusive groups; `argparse.FileType`; undocumented `prefix_chars` to `add_argument_group()`;
- generator/coroutine `throw(type, exc, tb)` / `athrow(type, exc, tb)` → single exception argument;
- ambiguous numeric literal immediately followed by keywords (`0in`, `1or`, etc.);
- special conversion methods (`__index__`, `__int__`, `__float__`, `__complex__`) returning inappropriate subclasses/types;
- `complex(real=<complex>, imag=...)` style → pass a complex number as the single positional value where applicable;
- `calendar.January` / `calendar.February` → uppercase constants;
- `codecs.open()` → built-in `open()`;
- `datetime.utcnow()` → `datetime.now(datetime.UTC)`;
- `datetime.utcfromtimestamp(ts)` → `datetime.fromtimestamp(ts, datetime.UTC)`;
- `importlib.cache_from_source(debug_override=...)` → `optimization=`;
- deprecated tuple-like `importlib.metadata.EntryPoints` behavior;
- `logging.warn()` → `logging.warning()`;
- text/StringIO use in `mailbox` APIs → binary/BytesIO;
- `os.register_at_fork()` from an already multithreaded process;
- **`os.path.commonprefix()` for filesystem paths → `os.path.commonpath()`**; the former is lexical and can create path-traversal vulnerabilities when mistaken for containment;
- `shutil.rmtree(onerror=...)` → `onexc=`;
- old SSL protocols/options and `SSLContext()` without an explicit protocol;
- camelCase `threading` aliases (`notifyAll`, `isSet`, `currentThread`, etc.);
- `typing.Text` → `str`;
- non-`None` returns from `IsolatedAsyncioTestCase` tests;
- legacy `urllib.parse.split*()` helpers → `urlparse()`/structured parsing;
- truth-testing `xml.etree.ElementTree.Element` → `elem is not None` or `len(elem)` depending intent;
- `sys._clear_type_cache()` → `sys._clear_internal_caches()`.

# 19. PEP 829 packaging/startup migration: `.start` files and the `.pth` retirement path

If any script collection has its own venv bootstrapper, system package, editable install, site customization, or packaging glue, inspect **site startup artifacts**, not only `.py` source.

Python historically allowed `.pth` files to both append paths and execute one-line `import` statements at every interpreter startup. Python 3.15 introduces `.start` files as the explicit code-execution mechanism and begins deprecating executable `.pth` lines.

## 19.1 Migration model

Conceptually separate:

- `.pth`: static path configuration;
- `.start`: startup code.

If a corresponding `.start` file exists, executable import lines in the `.pth` are ignored in favor of the startup file. Static path lines remain meaningful.

Agent filesystem search:

```bash
python - <<'PY'
import site
print("system site:", site.getsitepackages())
print("user site:", site.getusersitepackages())
PY

find /path/to/venv -type f \( -name '*.pth' -o -name '*.start' \) -print
```

Then inspect `.pth` files for lines beginning with `import ` or `import\t`.

## 19.2 Do not abuse `.start` as a lazy-loading mechanism

Startup hooks are deliberately eager and affect every normal interpreter initialization where `site` runs. They are appropriate for environment bootstrap that **must** happen globally, not for optional dependencies or application features. Optional work belongs behind ordinary call boundaries or explicit lazy imports.

## 19.3 Timeline to encode in long-lived tooling

- 3.15–3.17: executable `import` lines in `.pth` are still processed unless a matching `.start` exists, in which case those executable lines are ignored.
- 3.18–3.19: executable `import` lines in `.pth` are silently ignored.
- 3.20+: executable `import` lines remain ignored and Python emits warnings about their presence.
- Independently, PEP 829 deprecates locale-fallback decoding for `.pth`; after its transition, `.pth` files must be decodable as `utf-8-sig`. **`.start` files are specified as UTF-8 in 3.15**; do not transfer the `.pth` `utf-8-sig` wording onto `.start` files.

If your deployment tooling emits `.pth` today, make the format change now rather than carrying version-conditional behavior.

# 20. C-extension and native-extension modernization

Skip this section only if the entire script estate is pure Python **and** none of its locally maintained dependencies contains C/C++/Rust extension code. On Arch, locally built extensions are common enough that the agent should at least inventory `.so` dependencies.

```bash
python - <<'PY'
import pkgutil
from pathlib import Path

seen = set()
for m in pkgutil.iter_modules():
    try:
        spec = m.module_finder.find_spec(m.name)
    except Exception:
        continue
    origin = getattr(spec, "origin", None)
    if origin and origin.endswith((".so", ".pyd")) and origin not in seen:
        seen.add(origin)
        print(origin)
PY
```

## 20.1 PEP 803: Stable ABI for free-threaded builds (`abi3t`)

Python 3.15 establishes a Stable ABI track for free-threaded CPython. Native projects that want one wheel/binary across compatible free-threaded 3.x releases should study PEP 803 rather than assuming conventional `abi3` automatically covers the free-threaded runtime.

Key implications for an agent maintaining extension code:

- detect free-threaded build support via the runtime/build APIs described in §12;
- keep conventional `abi3` and free-threaded `abi3t` artifacts conceptually distinct;
- avoid poking directly into CPython object layouts that the Stable ABI does not promise;
- prefer module state and modern heap-type APIs;
- audit global mutable C state for real parallel execution;
- audit borrowed-reference lifetimes and “the GIL protects this” assumptions;
- use critical-section APIs where object-level synchronization is required;
- verify the build backend/wheel tooling actually understands the intended ABI tag before claiming it.

PEP 803's free-threaded Stable ABI is especially valuable for distribution; it does **not** make unsafe extension code thread-safe by declaration.

## 20.2 PEP 788: protecting the C API from interpreter finalization

PEP 788 addresses a concrete native-extension failure mode: attaching a thread state while an interpreter is finalizing can hang the thread, while racing an interpreter deletion can crash. It introduces three related API families:

- **`PyInterpreterGuard`**: keeps an interpreter from entering finalization while a native caller needs it;
- **`PyInterpreterView`**: provides thread-safe access to an interpreter that may otherwise be concurrently finalizing/deleted;
- **thread-state attach/detach APIs** such as `PyThreadState_Ensure()`, `PyThreadState_EnsureFromView()`, and `PyThreadState_Release()`.

Illustrative shape:

```c
PyInterpreterView *view = /* retained view */;
PyThreadStateToken *token = PyThreadState_EnsureFromView(view);
if (token == NULL) {
    /* interpreter unavailable/finalizing or another documented failure */
    return;
}

/* Safe C-API/Python interaction while attached. */

PyThreadState_Release(token);
```

The exact ownership rules for guards/views/tokens are API contracts, not stylistic details. In particular, a guard that is never closed can itself prevent interpreter finalization indefinitely.

If an extension embeds Python, creates foreign/native threads, stores thread-state/interpreter handles, receives callbacks after shutdown has begun, or uses `PyGILState_*` as a blanket solution, perform a dedicated lifecycle audit.

The `PyGILState_*` family is **soft-deprecated**: there is no announced removal, existing code continues to work, and the point is that new functionality is moving to the thread-state/view/guard APIs. Do not mechanically rename calls; migrate only when lifecycle semantics are understood and tested.

## 20.3 PEP 782: `PyBytesWriter` for efficient byte construction

Python 3.15 adds a public `PyBytesWriter` API for constructing bytes incrementally without the legacy pattern of creating an uninitialized bytes object and resizing it through private APIs.

New family includes creation, growth/resize, writing, data/size access, discard, and finish operations (`PyBytesWriter_Create`, `..._Grow`, `..._WriteBytes`, `..._Finish`, etc.).

Use it when native code currently does repeated concatenation, manual temporary-buffer growth, or:

```c
PyBytes_FromStringAndSize(NULL, len)
_PyBytes_Resize(...)
```

Those old construction patterns are soft-deprecated. The writer makes ownership/failure handling clearer and gives CPython room to optimize growth.

## 20.4 PEP 820: unified `PySlot` system

Python 3.15 introduces `PySlot` and `PyType_FromSlots()` as a unified slot-definition system. New slot IDs cover nested/subslots plus type metadata such as name, basic size, extra basic size, item size, flags, metaclass, and module; convenience macros encode typed slot data.

The older type/module-from-spec family (`PyType_FromSpec*`, `PyModule_FromDefAndSpec*`, `PyModule_ExecDef`, etc.) is **soft-deprecated**, not abruptly removed. For new 3.15-targeted extension architecture, prefer the unified slot system. For a stable mature extension, migrate when it materially simplifies code or is needed for modern ABI patterns rather than generating churn blindly.

## 20.5 PEP 793 module export hooks

`PyModExport_*` provides a module export hook for native modules and integrates with the newer slot machinery. This matters to advanced extension architecture and runtime/tooling integration; ordinary scripts need no source edit.

## 20.6 New/changed C APIs worth knowing

Notable additions include:

- `PyArg_ParseArray()` / `PyArg_ParseArrayAndKeywords()` for `METH_FASTCALL` argument arrays;
- `PyAnyDict_Check*`, `PyFrozenDict_Check*`, `PyFrozenDict_New()` for the new mapping type;
- `PyObject_CallFinalizerFromDealloc()` in the limited API;
- `PySys_GetAttr*` / `PySys_GetOptionalAttr*` as modern replacements for `PySys_GetObject()` access patterns;
- `PyUnstable_Unicode_GET_CACHED_HASH()` for specialized performance code, with unstable-API caveats;
- ABI compatibility declarations/checks through `Py_mod_abi`, `PyABIInfo_Check()`, `PyABIInfo_VAR`;
- `PyCriticalSection` family added to the Stable ABI;
- `PyImport_CreateModuleFromInitfunc()`;
- `PyTuple_FromArray()`;
- GC-traversal-safe accessors for use inside `tp_traverse`, including `PyObject_GetTypeData_DuringGC()`, `PyObject_GetItemData_DuringGC()`, `PyType_GetModuleState_DuringGC()`, `PyModule_GetState_DuringGC()`, `PyModule_GetToken_DuringGC()`, `PyType_GetBaseByToken_DuringGC()`, `PyType_GetModule_DuringGC()`, and `PyType_GetModuleByToken_DuringGC()`;
- `PyObject_Dump()` for debugging;
- unstable stack-protection, immortality, and traceback-dump APIs.

Changed assumptions:

- if a type sets `Py_TPFLAGS_MANAGED_DICT` or `Py_TPFLAGS_MANAGED_WEAKREF`, it must also set `Py_TPFLAGS_HAVE_GC`;
- `PyDateTime_IMPORT` is thread-safe; code directly testing `PyDateTimeAPI == NULL` should call the import macro instead.

## 20.7 Removed C APIs: hard failures for old extensions

Audit for:

- old `PyUnicode_AsDecodedObject`, `PyUnicode_AsDecodedUnicode`, `PyUnicode_AsEncodedObject`, `PyUnicode_AsEncodedUnicode` → codec APIs;
- `PyImport_ImportModuleNoBlock()` → `PyImport_ImportModule()`;
- `PyWeakref_GetObject()` / `PyWeakref_GET_OBJECT` → `PyWeakref_GetRef()`;
- `PySys_ResetWarnOptions()` → clear `sys.warnoptions` and `warnings.filters` through supported mechanisms;
- initialization getters `Py_GetExecPrefix`, `Py_GetPath`, `Py_GetPrefix`, `Py_GetProgramFullPath`, `Py_GetProgramName`, `Py_GetPythonHome` → `PyConfig_Get(...)` equivalents.

For maintaining one C source across older Python releases, consider the `pythoncapi-compat` project where the official docs recommend it.

## 20.8 C-API deprecations to eliminate opportunistically

- external string-hash algorithm injection (`Py_HASH_EXTERNAL`) → removal 3.19;
- out-of-range coercion through unsigned `PyArg_ParseTuple` formats is becoming stricter;
- `_PyObject_CallMethodId`, `_PyObject_GetAttrId`, `_PyUnicode_FromId` → intern a `PyUnicode` key in module state and use public calls; removal 3.20;
- direct `PyComplexObject.cval` → `PyComplex_AsCComplex()` / `PyComplex_FromCComplex()`;
- private `_Py_c_*` arithmetic helpers are soft-deprecated;
- `Py_INFINITY` → C11 `INFINITY`;
- Python compatibility macros duplicating standard C (`Py_ALIGNED`, integer typedef/limit macros, `Py_VA_COPY`, etc.) are soft-deprecated in favor of C99/C11 facilities;
- `Py_MATH_El` / `Py_MATH_PIl` → standard/library constants or direct literals as appropriate; removal 3.20.

# 21. Build-level and Arch/Linux-specific opportunities

## 21.1 Frame pointers are now the default: preserve them end-to-end

CPython 3.15 builds with frame pointers by default on supported targets. This dramatically improves low-overhead native stack profiling because tools such as `perf` can unwind mixed Python/native stacks more reliably.

For locally compiled extension modules or helper libraries, preserve the unwind chain. If a custom build system discards CPython's flags, add the equivalent of:

```text
-fno-omit-frame-pointer
-mno-omit-leaf-frame-pointer    # where applicable/supported
```

Use `sysconfig` to obtain the actual build flags rather than hard-coding architecture assumptions.

The exact flags are architecture/toolchain-specific. The familiar `-fno-omit-frame-pointer` / `-mno-omit-leaf-frame-pointer` pair describes common GCC/Clang targets, but other architectures can use different mechanisms. Treat `sysconfig` and the build documentation as authoritative for the deployed Arch architecture.

Linux workflow:

```bash
# Record workload
perf record -g -- python your_script.py

# Inspect stacks
perf report

# Or export folded stacks/flamegraphs using your preferred tooling.
```

If a system Python build was configured `--without-frame-pointers`, this benefit is absent; detect the actual package/build rather than inferring from the version string.

## 21.2 `--with-pymalloc-hugepages`: benchmark, never assume

A CPython build can enable huge pages for pymalloc arenas. On Linux, arenas become 2 MiB and allocation attempts `MAP_HUGETLB`, falling back to ordinary pages; runtime use must then be explicitly enabled with:

```bash
PYTHON_PYMALLOC_HUGEPAGES=1 python workload.py
```

This is a **build + runtime experiment**, not a source-code modernization. Huge pages can trade TLB behavior against memory granularity, availability, privileges/system configuration, fragmentation, and workload characteristics. Use only after representative RSS/latency/throughput benchmarks.

**Linux failure-mode warning:** with `MAP_HUGETLB`, exhaustion of the huge-page pool can surface later as `SIGBUS` on a page fault—including copy-on-write faults after `fork()`—and terminate the process. That risk makes huge pages inappropriate as a blind “performance on” switch for general scripts or fork-heavy services.

## 21.3 `libmpdec` configuration changed

CPython no longer silently falls back to a bundled `libmpdec`. Source builds/distribution packaging must explicitly choose system versus bundled behavior through configure options. For normal Arch package consumers this is a distributor concern; it matters if you maintain a custom CPython build recipe.

## 21.4 Missing-stdlib distributor metadata

`--with-missing-stdlib-config=FILE` lets distributors describe stdlib modules that are unavailable or packaged separately and provide custom diagnostics. Useful to distro/toolchain maintainers, not ordinary application scripts.

## 21.5 Linux anonymous-mmap annotations

On Linux 5.17+ with `PR_SET_VMA_ANON_NAME` support, CPython can annotate anonymous mappings in `/proc/<pid>/maps` under `-X dev` or debug builds. This improves memory forensics and should be exploited by low-level profiling/debugging workflows rather than imitated in application code.

## 21.6 New `--disable-epoll` build switch

CPython 3.15 adds `./configure --disable-epoll`, which deliberately builds without `select.epoll()` even when the build host exposes `epoll_create()`/`epoll_create1()`. This is intended for platforms whose apparent epoll API is not compatible with Linux semantics. A normal Arch Linux CPython build should **not** disable epoll: doing so removes an efficient event-notification backend and can change selector/async-I/O behavior. Treat this as a portability/toolchain option, not a performance tweak.

## 21.7 Platform-specific release optimization not applicable to Arch

Official 64-bit Windows builds produced with sufficiently new MSVC use the tail-calling interpreter and report substantial Windows-specific speedups. This is a release/build change rather than a portable Python-source optimization. Do not attribute it to an Arch/Linux 3.15 interpreter, and do not rewrite Python code in an attempt to reproduce it. Benchmark the Linux build actually deployed.

# 22. Changelog-derived changes: how the agent should consume them

The What's New document is the curated public-API map; the **3.15 changelog is the exhaustive stream of nontrivial implementation, bug-fix, platform, build, security, and test changes**. For a large existing script estate, two classes of changelog entries matter disproportionately:

1. fixes that invalidate old workarounds or tighten security/correctness behavior;
2. optimizations that arrive automatically and therefore should prevent pointless source rewrites.

Primary source: <https://docs.python.org/3.15/whatsnew/changelog.html#changelog>

## 22.1 Security/correctness changes worth inheriting rather than reimplementing

The 3.15 release line includes, among many others:

- bounded per-read decompression for `zipfile` members using bzip2, LZMA, or Zstandard, preventing unexpectedly unbounded allocation from small compressed members;
- `tarfile` extraction-filter fixes for traversal patterns that leave and re-enter the destination path;
- stricter/correct `stringprep` and IDNA behavior around RFC 3454's Unicode tables;
- CVE-2026-15806 fix scoping `urllib.request.HTTPPasswordMgr` credentials to URL scheme, so HTTPS credentials are not reused for matching HTTP URLs;
- Expat updates and XML amplification defenses;
- `tarfile.data_filter()` symbolic-link target normalization and related extraction hardening carried in the 3.15 line;
- source/bytecode loader fixes such as routing sourceless `.pyc` opening through `io.open_code()` where required by the security model.

**Agent policy:** never “simplify away” stdlib validation/filtering because a local implementation appears faster. If code has a workaround for a historical stdlib bug, reproduce the old failing case under 3.15; delete the workaround only when behavior is demonstrably fixed and the workaround is no longer part of the application's external contract.

## 22.2 Changelog micro-optimizations: generally free wins

The 3.15 development changelog contains many interpreter/compiler micro-optimizations beyond the headline What's New list, including optimizations around generator-expression consumption, constant indexing, call/refcount paths, and buffer-based integer conversion. These are normally **not codemod targets**.

Rule:

> If the same clear Python source becomes faster because CPython 3.15 emits better bytecode, specializes a path, removes reference-count traffic, vectorizes/rewrites C code, or improves an internal algorithm, keep the clear source unless measurement shows an application-level bottleneck remains.

This protects the codebase from cargo-cult “optimization” that fights the interpreter.

## 22.3 Changelog review protocol for a local agent

For each script/package, map its imported stdlib modules, then query the 3.15 changelog for those module names and classify matching entries:

```text
SECURITY      -> tests + threat-model review, usually no emulation
BEHAVIOR      -> compatibility test / possible source edit
PERFORMANCE   -> benchmark before source rewrite; likely automatic
DEPRECATION   -> source edit if within project scope
BUILD/C API   -> inspect only if native extension/toolchain involved
TEST/DOC      -> usually informational
```

Do not ask the model to memorize the entire NEWS stream. Give it the changelog URL and this classification policy, then require evidence for any changelog-derived source edit.


## 22.4 Late release-line changes worth encoding in tests

A few late 3.15 entries are useful because they change what defensive code should exist around security-sensitive or edge-case behavior:

### TLS hostname validation

`asyncio` TLS connection paths now validate `server_hostname` when an `ssl.SSLContext` with `check_hostname=True` is supplied. `SSLContext.wrap_bio()` likewise validates `server_side`, `server_hostname`, and `session` consistently with `wrap_socket()`; in particular, hostname checking can no longer silently degrade into certificate-chain validation without peer-name validation merely because no hostname was supplied.

**Agent consequence:** do not preserve code that intentionally relies on the weaker behavior. Tests should assert that hostname-verifying contexts have an explicit valid hostname where required.

### Extreme float/complex format precision

Formatting a `float` or `complex` with precision near the platform `INT_MAX` is guarded so the runtime raises `ValueError` rather than crashing or producing invalid output.

**Agent consequence:** user-controlled format precision still requires application-level bounds for resource policy, but do not retain private crash-avoidance hacks aimed solely at the fixed runtime failure.

### Reverse-dictionary iterator mutation and free-threaded correctness

The release line contains many concurrency and mutation correctness fixes. Treat such changes as reasons to **retest old synchronization/workaround code**, not reasons to delete synchronization indiscriminately. In particular, behavior made stricter to match existing iterator mutation rules should be handled by fixing mutation-during-iteration logic rather than depending on a version-specific accident.

### Free-threaded scaling/correctness fixes are automatic

Late changes include data-race/deadlock corrections and scaling work in areas such as interned strings, attribute operations, GC counters, type metadata, and reference-count merging. These usually require **no Python source transformation**. Their practical effect is that old benchmark conclusions about free-threaded bottlenecks should be reproduced on the 3.15 deployment binary before architecture is changed around them.

# 23. Additional 3.15 language/runtime details to avoid overlooking

## 23.1 UTF-8 mode becomes the default (PEP 686)

Python now defaults to UTF-8 mode. This affects filesystem/text defaults, environment-derived behavior, and subprocess/file handling that previously inherited a locale-dependent encoding.

Modernization rule: **still specify an encoding for data formats and persistent files**. Default UTF-8 does not make implicit encoding a good protocol specification.

```python
with open(path, "r", encoding="utf-8") as f:
    ...
```

For migration testing, explicitly disabling UTF-8 mode (`-X utf8=0` or `PYTHONUTF8=0`) can reveal code whose behavior unintentionally depends on the new default. Conversely, `-X warn_default_encoding` remains useful for locating implicit text encoding.

## 23.2 Better import exceptions

`repr()` for `ImportError` / `ModuleNotFoundError` now includes useful `name`/`path` information. Logging/error-reporting code should usually preserve exception objects/tracebacks rather than manually rebuilding inferior import diagnostics.

## 23.3 Compilation/introspection APIs can carry module names

Compilation/AST/symbol-table/source-to-code paths accept module-name context in more places. Tooling that generates or analyzes code should pass meaningful names where supported so diagnostics and introspection are better attributed.

## 23.4 Interactive-shell completion/color changes

The default interactive shell gains richer color/completion behavior, including improved `from ... import ...` completion that can import modules to discover names. `PYTHON_BASIC_COMPLETER` can request simpler completion behavior. This is mostly developer ergonomics; **do not design production code around REPL completion side effects**.

## 23.5 macOS-only `webbrowser` change

`webbrowser.MacOS` replaces the old AppleScript-based path with `/usr/bin/open` behavior and better URL routing. On Arch/Linux, no source optimization follows from this; retain only for cross-platform awareness and remove `MacOSXOSAScript` dependencies as described in §18.

## 23.6 Improved error messages

Python 3.15 expands human-oriented diagnostics, particularly for `AttributeError`:

- missing attributes can suggest reaching the name through a member object (for example, suggesting `.inner.area` rather than `.area`);
- common method names from JavaScript/Java/Ruby/C# can map to Python equivalents, such as `list.push(...)` → `list.append(...)` and `str.toUpperCase()` → `str.upper()`;
- when the Python equivalent is syntax rather than a method, the diagnostic can say so (for example, a Java-style dictionary `put` call may suggest item assignment);
- mutating-method mistakes on immutable builtins can suggest the mutable counterpart (for example, tuple versus list);
- failed `delattr`/`del obj.attr` operations can suggest a nearby existing attribute name.

These are developer-productivity improvements, not APIs. **Do not assert exact interpreter error prose in tests unless the prose itself is the contract of the software under test.** Prefer exception type, structured attributes, and application-owned messages; CPython is free to refine diagnostic wording.

# 24. Concrete modernization heuristics for the local AI agent

The local agent should operate like a conservative compiler engineer, not a style bot. Every candidate transformation should carry a **reason**, **semantic precondition**, and **verification test**.

## 24.1 High-value static searches

Run an initial inventory similar to:

```bash
rg -n --glob '*.py' \
  -e '^\s*(from|import) ' \
  -e 'importlib\.import_module|__import__' \
  -e 'bytearray\(' \
  -e 'bytes\([^)]*bytearray|bytes\([^)]*buffer' \
  -e '\.clear\(\)' \
  -e 'TaskGroup\(' \
  -e 'create_task\(' \
  -e 'threading|ThreadPoolExecutor' \
  -e 'TypedDict|Protocol|TypeVarTuple|NamedTuple' \
  -e 'datetime\.(utcnow|utcfromtimestamp|strptime)' \
  -e 'codecs\.open|commonprefix|logging\.warn' \
  -e '\.load_module\(' \
  -e 'ByteString|no_type_check_decorator' \
  -e 'CGIHTTPRequestHandler|--cgi' \
  -e 'co_lnotab|sre_(compile|constants|parse)' \
  -e 'sqlite3\.connect|create_function|create_aggregate' \
  -e 'url_safe_b64decode|urlsafe_b64decode' \
  .
```

For C/C++:

```bash
rg -n --glob '*.{c,h,cc,cpp,hpp}' \
  -e 'Py_Get(Path|Prefix|ExecPrefix|ProgramFullPath|ProgramName|PythonHome)' \
  -e 'PyWeakref_(GetObject|GET_OBJECT)' \
  -e 'PyImport_ImportModuleNoBlock' \
  -e 'PySys_ResetWarnOptions' \
  -e 'PyBytes_FromStringAndSize\s*\(\s*NULL' \
  -e '_PyBytes_Resize' \
  -e 'PyComplexObject|\.cval' \
  -e 'PyType_From(Spec|SpecWithBases|ModuleAndSpec|Metaclass)' \
  -e 'PyModule_(FromDefAndSpec|ExecDef)' \
  .
```

These searches produce **candidates**, not edits.

## 24.2 Lazy-import decision tree

For each top-level import:

1. Is its imported object needed on the hot startup path? If yes, keep eager.
2. Is import time measurable/nontrivial? If no, laziness may add complexity without meaningful gain.
3. Does import execute registration, monkey-patching, logging configuration, environment setup, signal setup, codec registration, ORM model registration, warnings filters, native runtime initialization, or dependency validation? If yes, default to eager until behavior is redesigned/tested.
4. Is the import only needed by a rare command/subcommand/backend/file type? Strong lazy candidate.
5. Does delayed failure improve or worsen UX? Decide explicitly.
6. Can the dependency be first touched from `__del__`, `weakref.finalize`, `atexit`, signal cleanup, logging shutdown, or another teardown path? If yes, default to eager.
7. Can first use participate in a cycle that would surface as `ImportCycleError`? If yes, redesign the dependency direction or keep the stabilizing import eager.
8. Benchmark startup/RSS with the candidate lazy and validate the cold feature path that first forces it.

Prefer top-level explicit `lazy import` over moving imports into functions when 3.15-only code wants **deferred binding while retaining declarative module dependencies**. Function-local imports remain appropriate for compatibility or genuinely dynamic control flow.

## 24.3 Copy-elimination decision tree

When seeing mutable byte accumulation followed by conversion:

- if the buffer is no longer needed, prefer ownership-transfer APIs such as `bytearray.take_bytes()`;
- if only a prefix is complete, use the operation that extracts/transfers that portion rather than slicing + deleting + copying;
- if the original mutable contents must remain independently usable, **do not** use destructive transfer;
- profile allocation/peak RSS on large payloads, not just tiny microbenchmarks.

Likewise, prefer checksum composition (`zlib.*_combine`) when the true problem is combining independently checksummed chunks; do not concatenate megabytes merely to recompute a checksum.

## 24.4 Immutability decision tree

Consider `frozendict` when:

- mapping values are logically immutable after construction;
- hashability enables memoization/interning/set membership;
- defensive copying is currently used only to prevent mutation;
- a configuration/JSON tree should be immutable end-to-end.

Do **not** replace every `dict` with `frozendict`; mutation-heavy local working state remains a `dict`. Update type checks from concrete `dict` to `Mapping` only when callers genuinely accept all mapping implementations.

## 24.5 Concurrency decision tree

If ordinary CPython with the GIL is used, do not rewrite CPU-bound code merely because 3.15 has free-threading support in some builds. If a free-threaded build is actually deployed:

- identify mutable shared state;
- verify extension compatibility;
- distinguish algorithmic parallelism from thread-safety wrappers;
- use `threading.serialize_iterator()` only when preserving a single iterator's serialized advancement is the intended semantics;
- use `concurrent_tee()` when multiple consumers each need the full stream;
- benchmark because free-threaded overhead and scaling are workload-specific.

## 24.6 JIT decision tree

- if `sys._jit.is_available()` is false: no JIT-specific tuning;
- if available but disabled: benchmark both states before changing source;
- if enabled: optimize algorithms/data movement first; only then investigate code shape;
- never branch business logic on `sys._jit.is_active()`;
- report regression cases because 3.15 JIT speedups have a wide distribution, including regressions for some workloads.

# 25. Validation matrix: what every nontrivial migration should survive

For each package/script changed substantially, execute the relevant rows below.

| Dimension | Baseline | Additional mode | Purpose |
|---|---|---|---|
| Tests | normal 3.15 | `-X dev -W default` | expose resource/deprecation/runtime warnings |
| Encoding | normal UTF-8 default | `-X utf8=0`, `-X warn_default_encoding` where useful | find accidental default-encoding dependencies |
| Imports | normal | `-X importtime`; explicit lazy candidates | quantify startup and force cold paths |
| Lazy policy | normal | `-X lazy_imports=all` as diagnostic | expose import-side-effect assumptions |
| JIT | build default | `PYTHON_JIT=0/1` when supported | A/B real workload |
| Concurrency | GIL build/state | free-threaded build/state when deployment uses it | find races/extension incompatibility |
| Profiling | `/usr/bin/time`, Tachyon | `perf`/frame-pointer sampling | explain wall time/RSS instead of guessing |
| Packaging | clean venv | inspect `.pth`/`.start`, editable installs | startup/bootstrap correctness |
| Native | normal extension import/tests | ABI/free-threaded wheel as applicable | binary compatibility/thread safety |

## 25.1 Mandatory lazy-import tests

For every lazified dependency, test at least:

```text
A. importing the owning module but never using the dependency
B. first successful use
C. missing dependency / broken dependency path
D. repeated use after resolution
E. relevant multiprocessing/threading startup if the app uses it
F. plugin/registry behavior
G. serialization/introspection if the lazy binding can be inspected
H. import-cycle behavior (`ImportCycleError`) where package topology is nontrivial
I. finalizer/atexit/shutdown path if the dependency can be reached during teardown
```

Measure startup/RSS before and after. Revert laziness if the gain is immaterial and semantic complexity increases.

## 25.2 Mandatory performance patch evidence

Each optimization patch should record:

```text
workload:
baseline command:
modified command:
Python build + JIT/GIL state:
median / p95 wall time:
CPU time:
peak RSS:
profile evidence:
correctness tests:
reason for retained/reverted change:
```

A local AI agent should not claim “faster” from source appearance.

# 26. Suggested agent execution order for a repository

Use this ordering because it minimizes wasted work and catches semantic hazards before micro-optimization:

1. **Establish interpreter/build facts** — Python build, JIT, free-threaded state, compiler flags, native extensions.
2. **Make tests clean under 3.15** — fix hard removals and porting changes.
3. **Eliminate deprecation debt** — especially items already scheduled for 3.16–3.20.
4. **Measure startup/import graph** — `-X importtime`, wall time, RSS.
5. **Apply selective lazy imports** — only cold, side-effect-safe dependencies.
6. **Profile representative steady-state workloads** — Tachyon and/or `perf`.
7. **Remove copies/allocations** — `bytearray.take_bytes`, checksum combination, streaming APIs, correct buffer protocol use.
8. **Adopt semantic primitives** — `frozendict`, `sentinel`, newer typing constructs, `math.integer`, unpacking comprehensions when they make intent materially better.
9. **Modernize structured concurrency** — `TaskGroup.cancel`, iterator synchronization/tee semantics where required.
10. **Evaluate JIT/free-threading** — only against the deployment build and real workloads.
11. **Audit packaging startup** — `.pth` → `.start`, clean-venv install/import.
12. **Retest, reprofile, document evidence** — retain only changes that improve the intended axis without regression.

# 27. “Do not transform” rules — guardrails against an over-eager AI

The following are explicit prohibitions unless measurements/tests justify an exception:

- Do **not** convert every import to `lazy import`.
- Do **not** lazify imports whose side effects are part of initialization without redesigning that contract.
- Do **not** move all imports inside functions as a substitute for understanding import cost.
- Do **not** replace all `dict` values with `frozendict`.
- Do **not** replace all `re.match()` calls merely because `prefixmatch()` is the new preferred name when backward compatibility matters.
- Do **not** rewrite clean comprehensions to unpacking comprehensions under a blanket assumption of speed.
- Do **not** write source code conditional on JIT active-state internals.
- Do **not** assume Python 3.15 implies a JIT-enabled or free-threaded interpreter.
- Do **not** assume free-threading makes shared mutable objects or third-party extensions safe.
- Do **not** remove locks solely because an operation appears atomic on a GIL build.
- Do **not** enable pymalloc huge pages globally without workload measurements.
- Do **not** disable XML/tar/zip security limits for performance without an explicit trusted-input threat model.
- Do **not** preserve deprecated `.pth` executable lines in newly generated environments.
- Do **not** use private CPython APIs where a new public 3.15 API exists unless the project explicitly accepts version coupling.
- Do **not** “optimize” code paths already accelerated automatically by 3.15 until profiling shows they remain material.
- Do **not** treat microbenchmarks as proof of whole-application improvement.

# 28. Authoritative resources

Use primary sources first. The following links are suitable for feeding directly to a coding agent when it needs deeper semantics than this note contains.

## 28.1 Core release references

- Python 3.15 What's New: <https://docs.python.org/3.15/whatsnew/3.15.html>
- Full 3.15 changelog / NEWS stream: <https://docs.python.org/3.15/whatsnew/changelog.html#changelog>
- Python 3.15 library reference: <https://docs.python.org/3.15/library/index.html>
- Python 3.15 language reference: <https://docs.python.org/3.15/reference/index.html>
- Python 3.15 C API: <https://docs.python.org/3.15/c-api/index.html>
- Python build/configuration documentation: <https://docs.python.org/3.15/using/configure.html>
- Python command-line/environment options: <https://docs.python.org/3.15/using/cmdline.html>

## 28.2 Major 3.15 PEPs

- PEP 810 — Explicit lazy imports: <https://peps.python.org/pep-0810/>
- PEP 814 — `frozendict`: <https://peps.python.org/pep-0814/>
- PEP 661 — Sentinel values: <https://peps.python.org/pep-0661/>
- PEP 686 — Make UTF-8 mode default: <https://peps.python.org/pep-0686/>
- PEP 798 — Unpacking in comprehensions: <https://peps.python.org/pep-0798/>
- PEP 829 — Package startup configuration files: <https://peps.python.org/pep-0829/>
- PEP 803 — Stable ABI for free-threaded builds: <https://peps.python.org/pep-0803/>
- PEP 799 — New profiling package / sampling profiler: <https://peps.python.org/pep-0799/>
- PEP 831 — Frame pointers by default: <https://peps.python.org/pep-0831/>
- PEP 728 — TypedDict closed/extra items: <https://peps.python.org/pep-0728/>
- PEP 747 — `TypeForm`: <https://peps.python.org/pep-0747/>
- PEP 800 — Disjoint type bases: <https://peps.python.org/pep-0800/>
- PEP 782 — `PyBytesWriter`: <https://peps.python.org/pep-0782/>
- PEP 788 — C API for thread-state/interpreter attachment: <https://peps.python.org/pep-0788/>
- PEP 793 — PyModExport API: <https://peps.python.org/pep-0793/>
- PEP 820 — Unified C API slot system: <https://peps.python.org/pep-0820/>
- PEP 791 — `math.integer`: <https://peps.python.org/pep-0791/>

## 28.3 High-value library/runtime pages

- `sys` (lazy imports, JIT/GIL/build state): <https://docs.python.org/3.15/library/sys.html>
- `profiling`: <https://docs.python.org/3.15/library/profiling.html>
- `threading`: <https://docs.python.org/3.15/library/threading.html>
- `asyncio`: <https://docs.python.org/3.15/library/asyncio.html>
- built-in types (`frozendict`, bytes/bytearray): <https://docs.python.org/3.15/library/stdtypes.html>
- `types`: <https://docs.python.org/3.15/library/types.html>
- built-in exceptions (`ImportCycleError`): <https://docs.python.org/3.15/builtins/exceptions.html>
- simple statements / lazy-import language semantics: <https://docs.python.org/3.15/reference/simple_stmts.html#the-import-statement>
- Tachyon / `profiling.sampling`: <https://docs.python.org/3.15/library/profiling.sampling.html>
- free-threading HOWTO: <https://docs.python.org/3.15/howto/free-threading-python.html>
- C API import/lazy-import controls: <https://docs.python.org/3.15/c-api/import.html>
- `typing`: <https://docs.python.org/3.15/library/typing.html>
- `site`: <https://docs.python.org/3.15/library/site.html>
- `importlib`: <https://docs.python.org/3.15/library/importlib.html>
- `sqlite3`: <https://docs.python.org/3.15/library/sqlite3.html>
- `base64`: <https://docs.python.org/3.15/library/base64.html>
- `os`: <https://docs.python.org/3.15/library/os.html>
- `os.path`: <https://docs.python.org/3.15/library/os.path.html>
- `unicodedata`: <https://docs.python.org/3.15/library/unicodedata.html>

# 29. Compact machine-oriented migration checklist

The following block is intentionally terse enough to paste into another agent's system/context prompt.

```text
TARGET: CPython 3.15+ on Arch Linux.

1. Inventory interpreter:
   - sys.version, sys.abi_info, sys._is_gil_enabled(), Py_GIL_DISABLED
   - sys._jit.is_available()/is_enabled()
   - sysconfig CFLAGS/PY_CFLAGS
   - native .so dependencies

2. Establish baseline:
   - tests
   - python -X dev -W default
   - python -X warn_default_encoding
   - python -X importtime
   - /usr/bin/time -v
   - Tachyon/perf for representative workloads
   - use Tachyon wall vs cpu, --native, --subprocesses, and --blocking only for the question each mode answers

3. Mandatory compatibility scan:
   - removed load_module, sre_*, co_lnotab, CGIHTTPRequestHandler, old typing forms
   - datetime.strptime day-without-year
   - sqlite3 parameter-kind changes
   - argparse single-dash-long dest inference
   - base64 urlsafe padding semantics
   - iterparse resource cleanup
   - deprecations scheduled 3.16-3.20

4. Startup optimization:
   - profile imports first
   - explicit lazy import only for cold/side-effect-safe modules
   - keep required dependency validation/eager registration eager
   - test first-use failures and cold feature paths
   - use -X lazy_imports=all diagnostically
   - test ImportCycleError/cold first-use failures
   - keep finalizer/atexit/shutdown dependencies eager unless proven safe
   - treat sys.lazy_modules as debug/introspection state, not application logic
   - inspect .pth/.start startup hooks; .start is UTF-8, .pth has utf-8-sig transition rules

5. Allocation/data movement:
   - locate bytearray -> bytes + clear/delete patterns
   - use bytearray.take_bytes() when ownership can transfer
   - use zlib checksum combination for chunked checksums
   - preserve buffer semantics; do not destroy buffers still needed

6. Semantic modernization:
   - frozendict for logically immutable mappings/hashable mapping keys
   - sentinel() for unique missing/default markers
   - unpacking comprehensions where clearer
   - math.integer for integer combinatorics helpers
   - re.prefixmatch in 3.15-only new code where clearer
   - closed/extra-items TypedDict, TypeForm, disjoint_base as appropriate

7. Concurrency:
   - TaskGroup.cancel() for non-exceptional early group termination
   - free-threaded decisions only when deployment build is free-threaded
   - serialize_iterator/synchronized_iterator for serialized shared iterator
   - concurrent_tee when each consumer needs full stream
   - audit extension thread safety/global mutable state

8. Runtime-level free wins (usually NO rewrite):
   - faster Base64/Base32/Base85/Z85
   - faster csv.Sniffer
   - mimalloc raw allocation improvements
   - event-driven Popen.wait(timeout) where supported
   - import locking/runtime/compiler/JIT improvements
   - frame pointers for profiling

9. JIT:
   - detect, never assume
   - remember upstream x86-64 Linux pyperformance is ~7-8% geomean but individual cases range from regressions to >2x
   - A/B PYTHON_JIT=0 vs 1 on real workloads
   - never branch application logic on is_active()

10. Native extensions:
   - evaluate abi3t / PEP 803
   - PyBytesWriter instead of private resize construction
   - PySlot / modern module state for new code
   - replace removed C APIs
   - preserve frame pointers in custom native builds

11. Acceptance:
   - correctness/tests unchanged or intentionally updated
   - warnings clean
   - measurable startup/CPU/RSS improvement for performance claims
   - no import-side-effect regression
   - clean venv install/import
   - document before/after evidence

GUARDRAIL: Do not mass-modernize syntax for novelty. Every transformation must have a performance, correctness, compatibility, concurrency, security, or maintainability justification and a validating test/measurement.
```

---

## Final directive to the migration agent

Treat this document as a **candidate-generation and verification specification**, not permission for indiscriminate rewrites. Python 3.15's biggest practical gains come from combining three disciplines: exploit interpreter improvements that are already free, selectively opt into the new mechanisms that remove real startup/allocation/concurrency costs, and retire compatibility debt before it becomes breakage. The dominant optimization loop is therefore:

```text
measure -> identify mechanism -> transform minimally -> test semantics -> re-measure -> retain or revert
```

For any disputed or edge-case behavior, defer first to the **current Python 3.15 language/library/C-API/configuration documentation and changelog**, then use the linked PEP for design rationale. Do not infer shipped semantics from older Python versions, pre-implementation PEP wording, blog posts, or model memory.
