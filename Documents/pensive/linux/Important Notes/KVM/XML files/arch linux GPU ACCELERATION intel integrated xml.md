---
title: "Snippet — Intel iGPU Guest Accel (Virtio + SPICE GL)"
tags:
  - kvm
  - arch
  - gpu
  - accel
  - xml
---

# Snippet — Intel iGPU Guest Accel (Virtio + SPICE GL)

> [!note] Linux guest review pending
> This page retains older Linux/Boxes or example-XML instructions. It can
> conflict with the current Hyprland/Wayland-only baseline, including older
> clipboard/display recipes. Use [[+ MOC KVM]] for the reviewed
> shared host setup; this page is not yet the current Linux guest guide.

> [!abstract] Goal
> Paravirtual guest GPU (`virtio` + `virgl`) backed by host Intel iGPU via `rendernode`, NOT VFIO passthrough. For actual dGPU passthrough use [[Host PC  Preparation for GPU isolation]] + `<shmem>` + `video none`.

## Channels

### QEMU Guest Agent (`org.qemu.guest_agent.0`)

```xml
<channel type="unix">
  <target type="virtio" name="org.qemu.guest_agent.0"/>
  <address type="virtio-serial" controller="0" bus="0" port="1"/>
</channel>
```

### SPICE agent (`com.redhat.spice.0`)

```xml
<channel type="spicevmc">
  <target type="virtio" name="com.redhat.spice.0"/>
  <address type="virtio-serial" controller="0" bus="0" port="2"/>
</channel>
```

## Display — SPICE with GL

```xml
<graphics type="spice">
  <listen type="none"/>
  <image compression="off"/>
  <gl enable="yes" rendernode="/dev/dri/renderD128"/>
</graphics>
```

- `rendernode` = host `i915`/`xe` render node (`ls /dev/dri/`). For AMD: `/dev/dri/renderD128` still (amdgpu), or `renderD129` if dual GPUs.
- Requires guest `virtio-gpu` driver + host `qemu-desktop` with `virgl`.

## Video — Virtio with 3D

```xml
<video>
  <model type="virtio" heads="1" primary="yes">
    <acceleration accel3d="yes"/>
  </model>
  <address type="pci" domain="0x0000" bus="0x00" slot="0x01" function="0x0"/>
</video>
```

> [!info] `virtio` vs `qxl`
> `qxl` is CPU-rendered fallback. `virtio` + `accel3d=yes` + `spice gl` offloads via `virgl` → host iGPU. On QEMU 11.1 this path uses `io_uring` for `virtio-gpu` fences efficiently.

## Network note (kept)

```ini
virbr0   # actually: <interface type='network'><source network='default'/> (virbr0 is host bridge name; guest XML uses network name)
```

Related: [[arch GPU ACCELERATION reference xml]] (full), `30_kvm_vm_deploy.py` (`--video virtio --graphics spice,gl.enable=yes`).

> [!warning] Legacy — `accel3d` on `qxl`
> Never combine `accel3d` with `qxl` — `qxl` ignores it. `accel3d` only applies to `virtio`/`virgl`.
