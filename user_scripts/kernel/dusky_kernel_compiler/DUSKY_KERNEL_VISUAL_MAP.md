# Kernel build flow

```mermaid
flowchart TD
    A[Run Python compiler] --> B[Load TOML profile and schema]
    B --> C{Target hardware}
    C --> D[Inspect local CPU and hardware census]
    C --> E[Read complete exported target bundle]
    D --> F[Resolve dependencies and source release]
    E --> F
    F --> G[Lock build directory and identify source/config inputs]
    G --> H[Choose RAM by measured capacity or disk fallback]
    H --> I[Seed config and prune from target census]
    I --> J[Apply profile matrix and explicit overrides]
    J --> K[Resolve Kconfig and verify requested settings]
    K --> L{Configure only?}
    L -->|Yes| M[Stop with resolved configuration]
    L -->|No| N[Build using build-host resources]
    N --> O[Package kernel, headers and resolved profile]
    O --> P{Install requested locally?}
    P -->|No / remote target| Q[Save packages for target]
    P -->|Yes| R[Install, check DKMS and configure boot entries]
```

| File | Responsibility |
|---|---|
| `dusky_kernal_compile.py` | Hardware discovery, configuration, build and installation orchestration |
| `kernel_profiles/schema.py` | Field defaults, validation and wizard metadata |
| `kernel_profiles/*.toml` | Selectable tuning profiles |
| `patches/`, `extra/` | Historical artifacts; unused by the compiler |
| `compat/` | NVIDIA DKMS compatibility overlay (patches + version-specific config) |
| `kernel_profiles/settings/kernel_settings.toml` / `kernel_storage.py` | Machine storage policy and RAM restore/checkpoint handling |
| `kernel_runtime.py` | Optional packaged boot-time settings |
| `tests/test_kernel.py` | Isolated regression checks |

Remote hardware decides the kernel configuration; the build machine decides parallelism. Source/configuration checks precede compilation. Explicit configuration requirements fail when unresolved; dependency-gated preferences are reported separately.

See [usage](README.md), [profile fields](kernel_profiles/_SCHEMA_GUIDE.md), [current audit findings](audit/STOCK_AUDIT_2026-10-04.md). No build-speed, battery-life or boot-success guarantees are implied.
