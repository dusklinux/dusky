# Dusky kernel compiler

Build a stock upstream Linux kernel for the exact x86-64 hardware that will run it. Requires Arch Linux, Python 3.15+, Linux 7.3+ (including explicitly selected 7.3 RCs), and LLVM 21+ or the current GCC toolchain. Keep the compiler, schema, storage/runtime/NVIDIA helpers and `compat/` assets together.

## 1. Prepare the target

Run `python dusky_kernal_compile.py` and choose **Prepare**. This installs build tools and enables the modprobed-db user service. Use the target normally with its relevant peripherals, network/VPN, sound and graphics enabled before building. Strict pruning uses the accumulated target module census; it cannot infer hardware that has never been used.

`--doctor` reports hardware and installed tools. The bundled `modules/modprobed.db` is never selected automatically.

## 2. Select and build

Choose **Build** in the menu, review the profile and release, then proceed. Choose **edit** at the review if needed; `--wizard` opens all advanced settings directly. Profiles store only differences from defaults; `--show --dump-toml -p NAME` displays the complete resolved configuration, and `--spec` explains every field.

| Profile | Purpose | Main choices |
|---|---|---|
| battery | Laptop battery use | 300 Hz, lazy preemption/RCU, power EPP, automatic THP disabled |
| performance | Responsive desktop | 1000 Hz, full preemption, performance EPP, THP on request |
| extreme_power | Aggressive power saving | 100 Hz, lazy RCU, aggressive ASPM |
| low_memory | Memory-constrained desktop | Size optimization, 250 Hz, ZSTD swap, automatic THP disabled; no hibernation |

All use strict census pruning and native local CPU targeting. None has a vendor-specific keep list or blanket VM driver overrides. The battery profile no longer enables sched_ext/BTF solely for an unused daemon or forces PCIe ASPM against firmware restrictions. CPU mitigations and other existing security preferences remain profile settings.

All four profiles retain 32-bit x86 application support and NTSync as a module for compatible Wine/Proton applications. An unloaded NTSync module occupies disk space, not resident driver memory, and creates no background polling. IA32 compatibility adds built-in code; disabling it can reduce the kernel footprint, but the supplied profiles preserve application compatibility instead.

```sh
python dusky_kernal_compile.py -p battery --no-install
python dusky_kernal_compile.py -p battery --configure-only --print-matrix
# Until a stable 7.3+ release is available, explicitly select the newest mainline RC:
python dusky_kernal_compile.py -p battery --channel mainline --allow-rc --no-install
# Or select an exact version (7.3-rc5 was latest at the audit):
python dusky_kernal_compile.py -p battery --pin 7.3-rc5 --allow-rc --no-install
```

Stable remains the default channel. The interactive picker permits explicit RC selection; unattended RC builds require `--allow-rc`. `--pin VERSION` bypasses the release picker. No kernel older than the 7.3 series is accepted. Release snapshots have no kernel.org archive signature/checksum; the script reports this limitation.

No scheduler/enhancement/ACS/Polly patches or custom HZ injection are applied. The NVIDIA repair described below uses a DKMS overlay; package-owned sources stay unchanged. Old profile patch fields are rejected as unknown settings: remove them rather than expecting silent fallback. Adjacent old patch artifacts remain on disk but are unused. Timer choices are upstream's 100/250/300/1000 Hz. The kernel stays EEVDF; optional sched_ext uses the in-tree BPF interface.

ThinLTO, native tuning and stripped/compressed module packaging are retained. All supplied profiles use ZSTD for the kernel image, modules and compressed swap. Package compression follows makepkg settings (Arch defaults to ZSTD); the audited packages are `.pkg.tar.zst`. Exported hardware bundles use ZSTD through Python 3.14. Existing initramfs settings are respected (mkinitcpio defaults to ZSTD). Upstream hibernation supports only LZO/LZ4, so its existing LZ4 setting remains; firmware and archive readers retain other codecs for compatibility. O2 remains the performance default; O3 and full LTO have workload-dependent tradeoffs. `thp="never"` disables automatic THP allocation while retaining support required by upstream GPU SVM helpers; only selected ZRAM compression backends are compiled. Single-node targets omit NUMA even when the profile permits it. Strict pruning restores census-listed Intel generated platform drivers missed by upstream localmodconfig. Explicit `modules.keep_symbols` and `dusky.extra_config` remain available for deliberate workload features.

A kernel package contains its resolved profile and optional kernel-specific boot tuning service. Runtime tuning runs once, only on the matching kernel; another power manager can subsequently override it. Existing ZRAM swap is left to its current manager. Selecting recompression algorithms does not schedule periodic recompression.

## 3. Install or transfer

A normal local build installs the resulting packages and refreshes supported bootloader integration. `--no-install` builds packages only. Saved packages should be installed through this tool so it checks DKMS, initramfs and boot integration:

```sh
python dusky_kernal_compile.py --install-pkg /path/to/linux-dusky-*.pkg.tar.zst
```

Headers are built automatically when the target needs DKMS, or explicitly with `--headers always`. An unsuccessful DKMS audit stops boot-entry promotion. Strict NVIDIA targets with DKMS omit Nouveau on stable and RC kernels; portable builds and targets without NVIDIA DKMS retain its existing fallback.

On installation targets with NVIDIA hardware and the tested nvidia-open-dkms 615.78.08 source, the compiler automatically prepares the [RTD3 compatibility overlay](compat/README.md) before pacman's DKMS hooks run. It verifies the actual installed module's ELF import after DKMS, rebuilds only if that fix is absent, and stops before initramfs/boot promotion if repair fails. Existing DKMS overrides are preserved. AMD/Intel-only hosts skip the NVIDIA path, and building/exporting for another machine never patches the builder's drivers. Other driver versions/flavors are not automatically patched; new releases still require validation.

After boot, `--doctor` or `--doctor --json` includes passive NVIDIA power status, using discovered PCI addresses rather than unstable DRM card numbers. An active display or workload may legitimately keep a GPU in D0; this snapshot is diagnostic, not a universal requirement to power off every NVIDIA GPU.

`boot.write_entries` and `boot.set_default` separately control systemd-boot entries and default selection. GRUB/rEFInd/Limine/UKI command-line integration still depends on the target setup. No service starts on the build computer during compilation.

## Build on a stronger computer

Export from the target (including a chosen tuning profile):

```sh
python dusky_kernal_compile.py -p low_memory --export-bundle ~/target.tar.zst
```

Import and build on the stronger x86-64 Arch computer:

```sh
python dusky_kernal_compile.py --import-bundle target.tar.zst
python dusky_kernal_compile.py -p remote_HOST_low_memory --channel mainline --allow-rc --no-install
```

Use the exact profile name reported by import. `-p NAME --import-bundle FILE` chooses local tuning instead of the bundled profile. Bundles retain target CPU, topology, GPU, filesystem, VM and module information; they never prune against the builder's modules. Import replaces `native` with the target compiler CPU name or its ISA baseline and disables installation on the builder. Build jobs use the builder's resources. Copy packages back and use **Install** on the target. Guest targets use the same export/import workflow.

## RAM workspace and persistence

Builds automatically prefer `/mnt/zram1/dusky_kernel`, beneath the existing `/mnt/zram1` mount. If the mount is absent or measured filesystem/memory capacity is insufficient, they use persistent disk. The script does not create or format a RAM device and no longer asks a RAM-storage question.

`kernel_profiles/settings/kernel_settings.toml` controls storage paths. Empty persistent paths use `$XDG_CACHE_HOME/dusky-kernel` (normally `~/.cache/dusky-kernel`). `--build-dir DIR` overrides the persistent root. Completed packages always go to disk.

Capacity planning allows 22 GiB (30 GiB for full LTO), the existing selected tree when larger, shared caches, and the configured memory reserve. Completed RAM contents are credited for reuse. This is an estimate, not a memory/disk limit; unusually large configurations or concurrent workloads can still exhaust resources.

Only the selected source/object tree and shared caches are restored into RAM. Checkpoints save changed files to disk after success, failure or Ctrl-C, preserving other persistent builds. Let checkpointing finish before rebooting. A failed checkpoint leaves `.unsaved` to prevent overwriting newer RAM work with older disk data. Power loss or forced termination before saving can lose RAM-only work. Tmpfs can swap, so RAM builds do not guarantee zero SSD writes.

Stock-source cache identity includes the resolved profile and target hardware/toolchain facts. Changes to UI or orchestration code reuse the tree; configuration and compiler command changes are tracked by Make. Runtime payloads are repackaged each build.

## Validation and optional FDO

```sh
python -W error::ResourceWarning -m unittest discover -s tests -v
```

For optional AutoFDO, set `compiler.fdo="autofdo"` for an initial profile-collection build, boot that kernel, then run `--fdo-record SECONDS` with a representative workload. Missing feedback retains AutoFDO line information without passing a nonexistent profile to Clang. Conversion uses the installed `llvm-profgen`, or `create_llvm_prof` when available. Propeller additionally needs `create_llvm_prof`; recording needs supported hardware branch sampling.

See [the current audit](audit/STOCK_AUDIT_2026-10-04.md) for measured checks and remaining limits. Configuration/build tests do not establish optimal speed, battery life, or compatibility with every target's external drivers.
