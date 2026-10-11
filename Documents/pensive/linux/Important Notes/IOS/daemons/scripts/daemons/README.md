# Daemon Manager reference config

The maintained SwiftUI app is at [dusklinux/daemon_manager](https://github.com/dusklinux/daemon_manager), checked out locally at `/mnt/zram1/daemon_manager-main`. App source, build instructions, and the CI workflow belong there. The old standalone daemonmanager and processkiller instructions are retired.

This directory contains `daemon.cfg` and device-use notes. The config mirrors the repository's root `daemon.cfg` and its 504 builtin targets: **324 off, 180 on**. `yes` means disable; `no` means keep enabled. A recommendation is not a guarantee that every optional feature or device is safe with a service off.

The 2026-10-11 revision keeps these services enabled and shows red recommendations in the app:

| Service | Reason |
| --- | --- |
| `com.apple.searchd` | Photos logged repeated failed Spotlight queries with sustained CPU during broad live unloading; exact single-service causality remains unproven. |
| `com.apple.biomed` | SpringBoard and keyboard clients logged Biome metadata/lockfile failures. |
| `com.apple.syncdefaultsd` | Provides `com.apple.kvsd`; multiple apps logged failed synchronized key-value-store access. |
| `com.apple.mobile.lockdown` | Paired USB tools failed when disabled and worked after restoration; needed for pairing/computer backup workflows. |

Critical bootloop/CPU/access warnings remain separate. Every entry remains manually configurable. Existing explicitly customized target differences are retained when migrating the old full-copy preference format; entries that matched old builtin values adopt the revised defaults. The old format cannot distinguish an explicit choice equal to an inherited default. New choices are stored separately from builtins.

The app saves launchctl overrides and reports command/verification failures. It does not hot-unload services: restart userspace to apply loaded-process changes fully. This addresses both demand restarts seen in testing and disruptive intermediate dependency states. The plist must retain root:wheel ownership and mode `0644` for the tested persistence workflow.

RAM display explicitly says **free + speculative**, in MiB. This is not the same as physical free RAM. No >600 MiB idle gain or broad battery-life improvement was verified.

Read [instructions.md](instructions.md) for usage/install notes and `../../daemons based on ios 16.md` for the full reference, test results, and limitations. No scripts, IPA builds, raw test logs, or mirrored app source are kept here.
