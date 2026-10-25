---
title: "Looking Glass — Muxless Passthrough (Native <shmem>)"
tags:
  - kvm
  - vfio
  - looking-glass
  - arch
  - wayland
---

# Looking Glass — Muxless Passthrough (Native `<shmem>`, Aug 2026)

> [!info] Stack
> **Arch · Kernel 7.3+ · systemd 262 · libvirt 12.6+ · QEMU 11.1 · Looking Glass IDD Development Stack · Windows 10/11**
> Even on MUX-laptops, follow this muxless path — works across modes.

```
Windows Guest (NVIDIA pass-through)          Kernel SHM                           Host
NVIDIA → Looking Glass IDD (Root\LGIdd)  →  /dev/shm/looking-glass (ivshmem-plain)  →  looking-glass-client (Wayland/EGL)
Direct kernel-level frame output            Zero-copy RAM buffer
(LGIddHelper Windows Service)
```

| Component | Role |
|---|---|
| **Looking Glass IDD** | Modern unified Indirect Display Driver (`Root\LGIdd`) + direct input (`Root\LGInput`) |
| **`LGIddHelper`** | Windows service managing frame synchronization into IVSHMEM |
| **`/dev/shm/looking-glass`** | Host `tmpfs` `ivshmem-plain` region — zero-copy buffer |
| **`looking-glass-client`** | Reads shm, renders via EGL/OpenGL on Wayland (Hyprland) |
| **`wlfreerdp` / `xfreerdp3`** | Rescue RDP while emulated display disabled (`freerdp` pkg) |

> [!note] IDD Driver vs Legacy B7 Host
> Previous B7 stable releases used a separate user-space `Looking Glass (host)` capture application paired with a third-party Virtual Display Driver (MttVDD). The modern Looking Glass development stack replaces both with the official **Looking Glass IDD driver** (`looking-glass-idd-setup.exe`). The IDD driver renders directly into IVSHMEM without requiring `devcon.exe`, `vdd_settings.xml`, or the old background capture host service.

## Prereqs

- [ ] VFIO bound (`vfio-pci` per [[Host PC  Preparation for GPU isolation]])
- [ ] VM is Windows 10/11 libvirt (`qemu:///system`)
- [ ] `kvm` group: `groups | grep kvm`
- [ ] packages: `sudo pacman -S --needed linux-headers` (+ `dkms` if you ever used `kvmfr`)

## Phase 1 — Host: shm sizing & persistence

### 1.1 Packages

```bash
paru -S --needed looking-glass freerdp   # 25_looking_glass.py:install_packages fallback paru
# Windows side: download paired looking-glass-idd-setup.exe from https://looking-glass.io/downloads
# Note: Linux client and Windows IDD installer MUST come from the exact same build/commit!
```

### 1.2 Size (SDR, double-buffer + 10 MiB, power-of-two)

`size = round_up_pow2(width × height × 4 × 2 + 10 MiB)` — `ivshmem-plain` requires power-of-two.

| Target | Frame pair +10 | Final |
|---|---|---|
| 1920×1080 | 16.6 +10 → 25.8 | **32 MiB** (33554432) |
| 2560×1440 | 29.5 +10 → 38.1 | **64 MiB** (67108864) ← laptop 1440p recommendation |
| 3840×2160 | 66.4 +10 → 73.3 | **128 MiB** (134217728) |

> Windows 10 guests lack HDR — keep **SDR**; HDR would overflow static buffer.

### 1.3 tmpfiles (boot-persistent, `f` = create-if-missing, never truncate live guest)

```bash
sudo tee /etc/tmpfiles.d/10-looking-glass.conf <<'EOF'
# Managed by Arsonix (Phase 5)
f /dev/shm/looking-glass 0660 new kvm - -
EOF
# replace `new` with your $USER
```

### 1.4 Create & pre-allocate (bypass `fs.protected_regular=1`)

Do **not** `ftruncate`/sparse — pipeline uses unconditional `posix_fallocate` (tmpfs on 7.1 guarantees it) + `O_EXCL|O_NOFOLLOW` (symlink TOCTOU defense):

```bash
sudo rm -f /dev/shm/looking-glass                         # clear ownership lock
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
# 64 MiB for 1440p (match table above):
sudo fallocate -l 64M /dev/shm/looking-glass
# pipeline does: fd = os.open(/dev/shm/... , O_CREAT|O_EXCL|O_RDWR|O_NOFOLLOW); posix_fallocate; fchown new:kvm; fchmod 0660
sudo chown new:kvm /dev/shm/looking-glass
sudo chmod 0660 /dev/shm/looking-glass
ls -lh /dev/shm/looking-glass   # 64M new kvm
```

> [!warning] Why `posix_fallocate` unconditionally
> Sparse `fallocate` fallback masked real tmpfs ENOSPC. On 7.1 `tmpfs` `fallocate` always succeeds; any failure should abort (host OOM risk), not silently create sparse file causing mid-frame faults. `25_*.py:stage_shm` enforces this.

## Phase 2 — Domain XML: native `<shmem>` (no `xmlns:qemu` hack)

We previously used `<qemu:commandline>` raw `-device ivshmem-plain` / `-object memory-backend-file`. **Deleted** — bypasses libvirt device model, breaks validation/migration, desyncs from `<memballoon>`/NUMA, needs `xmlns:qemu` hatch. Libvirt emits identical QEMU args from `<shmem>` and **creates/labels** `/dev/shm/<name>` itself.

> [!abstract] The ONLY XML needed (inside `<devices>`)
> ```xml
> <shmem name='looking-glass'>
>   <model type='ivshmem-plain'/>
>   <size unit='M'>64</size>
> </shmem>
> <memballoon model='none'/>
> ```
> No `xmlns:qemu`, no `<qemu:commandline>`, no `kvmfr` module. Pipeline helper `25_*.py:transform_domain` strips any legacy `<qemu:commandline>` with `looking-glass|kvmfr|ivshmem` before injecting this.

### 2.1 Edit

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system dumpxml --inactive win_10_dusky > /tmp/old.xml
# interactively:
virsh -c qemu:///system edit win_10_dusky
```

### 2.2 Add `<shmem>` + fix companions

Inside `<devices>`:

```xml
<shmem name="looking-glass">
  <model type="ivshmem-plain"/>
  <size unit="M">64</size>  <!-- match Phase 1 -->
</shmem>
```

Also:

- `<memballoon model='none'/>` (balloon steals DMA-linear memory → latency)
- `<cpu mode='host-passthrough' …><topology sockets='1' dies='1' cores='X' threads='Y'/></cpu>` where `X×Y = vcpu` (`25_*.py:apply_cpu_topology` derives `threads = host_smt if vcpu%host_smt==0 else 1`)
- `<channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>` (clipboard)

Pipeline redefines → `virsh define`; if guest `running`, power-cycle needed (reboot not enough — old QEMU device model still resident).

```bash
virsh -c qemu:///system define /tmp/new.xml
virsh -c qemu:///system start win_10_dusky
virsh -c qemu:///system domstate win_10_dusky  # running
```

## Phase 3 — Windows: Drivers & Modern Looking Glass IDD Setup

Via RDP (rescue) or temporary SPICE display console:

### 3.1 IP & RDP Connection

```bash
virsh -c qemu:///system domifaddr win_10_dusky --source lease   # 192.168.122.x
# Wayland native (FreeRDP 3.x):
wlfreerdp /v:192.168.122.45 /u:Administrator /cert:ignore /gfx:AVC444 /network:auto +clipboard /dynamic-resolution
# Or xfreerdp3 fallback:
xfreerdp3 /v:192.168.122.45 /u:Administrator /cert:ignore /dynamic-resolution
```

### 3.2 Inside Guest: Drivers & IDD Installation

1. **VirtIO drivers**: Mount `virtio-win.iso` → run `virtio-win-guest-tools.exe` (installs `vioinput`, `viostor`, `NetKVM`, `spice-agent`, and `QEMU Guest Agent`).
2. **NVIDIA driver**: Install standard GeForce / Quadro driver for your pass-through GPU → reboot.
3. **Looking Glass IDD Driver (Modern Stack)**:
   - Place `looking-glass-idd-setup.exe` inside the guest (from the **same upstream build/commit** as the Linux client).
   - In Administrator PowerShell, trust the installer publisher and install silently with IVSHMEM:
     ```powershell
     # Trust installer signature to prevent driver prompt hang:
     $sig = Get-AuthenticodeSignature .\looking-glass-idd-setup.exe
     if ($sig.Status -eq 'Valid' -and $sig.SignerCertificate) {
         $store = [Security.Cryptography.X509Certificates.X509Store]::new('TrustedPublisher', 'LocalMachine')
         $store.Open('ReadWrite')
         $store.Add($sig.SignerCertificate)
         $store.Close()
     }
     # Stop legacy host service if upgrading from B7:
     $old = Get-Service -Name 'Looking Glass (host)' -ErrorAction SilentlyContinue
     if ($old) { Stop-Service -InputObject $old -Force; Set-Service -InputObject $old -StartupType Disabled }

     # Install Looking Glass IDD & bundled IVSHMEM driver:
     Start-Process -FilePath .\looking-glass-idd-setup.exe -ArgumentList @('/S', '/ivshmem') -Wait
     ```
   - Reboot Windows when prompted (exit code 3010).
4. **Verify Devices & Service**:
   ```powershell
   Get-PnpDevice -Class Display,HIDClass -PresentOnly | Where-Object { $_.InstanceId -match 'Root\\LGIdd|Root\\LGInput' }
   Get-Service -Name LGIddHelper
   ```
   Both display and input devices should show status `OK`, and `LGIddHelper` should be `Running`.

> [!important] Disable Emulated Display Only After IDD Is Active
> In `devmgmt.msc` → **Display adapters**, right-click **Red Hat QXL** / **Microsoft Basic Display Adapter** → **Disable device**. The desktop output will immediately switch to the Looking Glass IDD display on the NVIDIA GPU.

## Phase 4 — Host Wayland (Hyprland) `client.ini`

Pipeline `60_configure_client_ini.py` creates/merges `~/.config/looking-glass/client.ini` with mode `0644`/`0660` for the operator user.

Manual configuration:

```bash
mkdir -p ~/.config/looking-glass
nvim ~/.config/looking-glass/client.ini
```
```ini
; Looking Glass Client — Hyprland / Wayland / Kernel 7.3+ (Aug 2026)
[lgmp]
; Canonical key is [lgmp] shmDevice. [app] shmFile is deprecated, and app:shmDevice is ignored!
shmDevice=/dev/shm/looking-glass
allowDMA=yes

[egl]
vsync=yes

[opengl]
vsync=no
preventBuffer=yes
mipmap=yes

[wayland]
fractionScale=no
warpSupport=yes

[win]
; autoResize=yes FATALLY conflicts with setGuestRes=yes in modern IDD clients! Keep autoResize=no:
autoResize=no
setGuestRes=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes

[input]
escapeKey=64   ; KEY_F6
rawMouse=yes
hideCursor=yes

[spice]
enable=yes
clipboard=yes
audio=no
```

### Launch

```bash
looking-glass-client
# or explicit device flag:
looking-glass-client -f /dev/shm/looking-glass -m KEY_F6
```
**Hotkeys:** `F6` capture/release, `F6+Q` quit, `F6+F` fullscreen, `F6+D` show FPS, `F6+O` overlay menu.

## Phase 5 — Troubleshoot

| Symptom | Cause | Fix |
|---|---|---|
| **Black screen / splash remains** | Display sleeping or handoff pending | Focus LG window, press a key or move cursor to wake IDD; verify `LGIddHelper` is running in guest |
| **Fatal config conflict on launch** | `autoResize=yes` set with `setGuestRes=yes` | In `client.ini` set `autoResize=no` under `[win]` |
| **Permission denied `/dev/shm/...`** | Host `fs.protected_regular` or stale owner | `sudo rm -f /dev/shm/looking-glass && sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf && sudo fallocate -l 64M /dev/shm/looking-glass && sudo chown $USER:kvm /dev/shm/looking-glass && sudo chmod 0660 /dev/shm/looking-glass` |
| **Size mismatch** | XML `<size>` vs `/dev/shm` capacity mismatch | Re-verify power-of-two sizing (64 MiB for 1440p / 4K SDR) |
| **Clipboard broken** | `spice-agent` inactive in Windows | Verify `Get-Service spice-agent` is Running; verify SPICE channel in domain XML |
| **`shmDevice` ignored** | Placed under `[app]` instead of `[lgmp]` | Ensure `shmDevice=/dev/shm/...` is declared under section `[lgmp]` |

## Appendix — Legacy `qemu:commandline` (archived)

> [!warning] Historical path — do not use
> Old notes (`Looking Glass_old.md`) declared `xmlns:qemu` on `<domain>` +:
> ```xml
> <qemu:commandline>
>   <qemu:arg value="-device"/><qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
>   <qemu:arg value="-object"/><qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
> </qemu:commandline>
> ```
> Replaced by native `<shmem>` (same QEMU args, validated by libvirt, `/dev/shm` managed, no xmlns escape hatch). Pipeline strips this block.

See: `25_looking_glass.py`, `60_configure_client_ini.py`, [[The RDP method to disable display driver]].
