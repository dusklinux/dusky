# Muxless Laptop GPU Passthrough with Looking Glass

> **Target Stack — June 2026**
> 
> Arch Linux · Kernel 7.x · systemd 260 · QEMU ≥ 9.x · libvirt ≥ 10.x · Windows 10 Guest (De-bloated)

## Architecture: The Full Pipeline

On a muxless (Optimus) laptop the NVIDIA GPU has no physical video output — all

pixels are routed through the weaker Intel/AMD iGPU. When you pass the NVIDIA

card to a KVM guest, it becomes headless. The following three-component stack

solves this entirely in software:

```
Windows Guest                   Kernel / Shared Memory             Arch Host
─────────────────────────────   ─────────────────────────────────  ─────────────────────
NVIDIA GPU (passed through)  →  KVMFR module  →  /dev/kvmfr0  →  looking-glass-client
VDD (fake monitor)               (DMA char device, zero-copy)        (renders on your display)
LG Host App (frame capture)
```

|   |   |
|---|---|
|**Component**|**Role**|
|**VDD** (Virtual Display Driver)|Creates a ghost monitor for the NVIDIA GPU so Windows has a render target|
|**Looking Glass Host**|Runs in Windows; captures the NVIDIA framebuffer and writes it to shared memory|
|**KVMFR module**|Kernel character device (`/dev/kvmfr0`) — provides a true DMA zero-copy window between guest and host|
|**looking-glass-client**|Reads `/dev/kvmfr0` on the host and renders the Windows desktop in a window|
|**xfreerdp3**|FreeRDP v3 rescue bridge — used to configure Windows drivers while the emulated display is disabled|

## Prerequisites Checklist

Before starting, confirm your system meets these requirements:

- [ ] PCI passthrough (IOMMU, VFIO) is already working — the NVIDIA GPU is
    
    bound to `vfio-pci` and assigned to your VM
    
- [ ] Your VM is a **Windows 10** guest managed by **libvirt** (virt-manager is fine; it
    
    uses libvirt as its back end)
    
- [ ] You are a member of the `kvm` group: `groups $USER | grep kvm`
    
- [ ] `dkms` and `linux-headers` (matching your running kernel) are installed
    

## Phase 1 — Host Kernel Layer: The KVMFR Module

The KVMFR (KVM Frame Relay) kernel module replaces the legacy `/dev/shm` POSIX

shared-memory approach entirely. It exposes `/dev/kvmfr0` as a proper character

device that the NVIDIA GPU's DMA engine can write directly, cutting out all

intermediate copies. There is no race condition on startup and no manual `chown`

needed after the VM launches.

### 1.1 Install Packages

```
# AUR: Looking Glass client (bleeding-edge git build)
paru -S --needed looking-glass-git

# AUR: KVMFR DKMS kernel module (git, matches the client source tree)
paru -S --needed looking-glass-module-dkms-git

# Official repos: FreeRDP v3 rescue bridge + DKMS framework
sudo pacman -S --needed freerdp dkms
```

> **Package note:** `looking-glass-git` builds the Linux **client** viewer.
> 
> `looking-glass-module-dkms-git` builds the **KVMFR kernel module** and
> 
> registers it with DKMS so it auto-rebuilds on every kernel upgrade. Both are
> 
> required; neither includes the other.
> 
> The Looking Glass **host application** (the Windows-side frame capturer) must
> 
> version-match the client exactly. For a `-git` client, grab the matching host
> 
> binary from the Looking Glass CI artifacts or build it from the same commit.
> 
> See: [https://looking-glass.io/downloads](https://looking-glass.io/downloads "null")

### 1.2 Calculate Your IVSHMEM Memory Size

Choose a size based on your target resolution. **Get this right before proceeding** — it must be consistent across the kernel module config, the libvirt

XML, and determines how much contiguous RAM is reserved.

Because this environment targets a de-bloated Windows 10 guest, advanced IDD capabilities like HDR/HDR+ (which strictly require Windows 11 22H2+) are unsupported. All memory calculations **must** be rigidly locked to 32-bit Standard Dynamic Range (SDR) to prevent allocating memory that Windows 10 cannot utilize.

**Formula (SDR):** `width × height × 4 × 2 ÷ 1024 ÷ 1024 + 10`, then round up

to the nearest power of 2.

|   |   |   |   |
|---|---|---|---|
|**Display Target**|**Raw Frame Size (Bytes)**|**Base + 10 MiB Overhead**|**Final KVMFR Allocation**|
|1920×1080 (1080p)|16,588,800|25.82 MiB|**32 MiB**|
|1920×1200 (1200p)|18,432,000|27.58 MiB|**32 MiB**|
|2560×1440 (1440p)|29,491,200|38.12 MiB|**64 MiB**|
|3840×2160 (4K)|66,355,200|73.28 MiB|**128 MiB**|

> **Practical recommendation:** Use **64 MiB** to perfectly match a standard 1440p high-end laptop display panel target. Do not over-provision memory for HDR on a Windows 10 guest.

### 1.3 Configure the Module

Create the modprobe options file. The example below uses `64` for a 1440p SDR target:

```
sudo tee /etc/modprobe.d/kvmfr.conf << 'EOF'
# KVMFR Looking Glass — static IVSHMEM device size
# Must match the 'size' field (in bytes) in the libvirt XML qemu:commandline block.
# Byte equivalent: size_MiB × 1048576
options kvmfr static_size_mb=64
EOF
```

Create the systemd-modules-load entry so the module is always loaded at boot

**before** your VM can start:

```
sudo tee /etc/modules-load.d/kvmfr.conf << 'EOF'
# Load KVMFR before any VM that uses it
kvmfr
EOF
```

### 1.4 Configure udev Permissions

The udev rule grants the `kvm` group read/write access to `/dev/kvmfr0` and

sets the `uaccess` tag so the currently logged-in seat user also gets access

automatically — no hardcoded username required.

> **Critical:** The rule file must sort lexically **before** `73-seat-late.rules`
> 
> for the `uaccess` tag to be processed correctly. The filename `70-kvmfr.rules`
> 
> satisfies this requirement for systemd 260.

```
sudo tee /etc/udev/rules.d/70-kvmfr.rules << 'EOF'
SUBSYSTEM=="kvmfr", GROUP="kvm", MODE="0660", TAG+="uaccess"
EOF
```

### 1.5 Load and Verify

Load the module immediately for this session without requiring a reboot:

```
sudo modprobe kvmfr
```

Verify the character device was created correctly:

```
ls -l /dev/kvmfr0
```

Expected output — look for the `c` at the start (character device):

```
crw-rw---- 1 root kvm 242, 0 Jun 16 10:00 /dev/kvmfr0
```

Confirm the module announcement in dmesg:

```
dmesg | grep kvmfr
# Expected: kvmfr: creating 1 static devices
```

> **Warning — regular file trap:** If QEMU ever starts before the KVMFR module
> 
> is loaded, it will create `/dev/kvmfr0` as a regular file instead of a
> 
> character device. The symptom is `ls -l` showing a permissions string that
> 
> starts with `-` (not `c`) or a non-zero file size. If this happens:
> 
> ```
> sudo rm /dev/kvmfr0
> sudo modprobe kvmfr
> ```
> 
> The correct fix is ensuring the module is loaded at boot (step 1.3) so QEMU
> 
> never races against it.

### 1.6 Configure libvirt cgroups Device ACL

libvirt uses cgroups to restrict which device files QEMU processes can open.

`/dev/kvmfr0` must be explicitly whitelisted or the VM will fail to start.

Open the libvirt QEMU configuration file:

```
sudo nvim /etc/libvirt/qemu.conf
```

Find the commented-out `cgroup_device_acl` block (search for `cgroup_device_acl`)

and replace it with the following uncommented version. Preserve any devices

already listed in your file — the list below is a safe superset of the defaults:

```
cgroup_device_acl = [
    "/dev/null", "/dev/full", "/dev/zero",
    "/dev/random", "/dev/urandom",
    "/dev/ptmx", "/dev/kvm",
    "/dev/kvmfr0"
]
```

Apply the change:

```
sudo systemctl restart libvirtd.service
```

## Phase 2 — VM XML: Wiring the IVSHMEM Bridge

We need to add two things to the libvirt domain XML:

1. The `qemu` XML namespace declaration on the root `<domain>` tag
    
2. A `<qemu:commandline>` block that passes the KVMFR device to QEMU
    

These **must** be added in a single editing session. Saving after adding only

the namespace (but before the commandline block) will cause libvirt to reject

the edit.

### 2.1 Open the VM XML

```
# Confirm your VM name first
sudo virsh list --all

# Open the XML — replace win10 with your VM name
sudo EDITOR=nvim virsh edit win10
```

### 2.2 Add the QEMU Namespace to the Root Domain Tag

Locate the first line of the document — the `<domain>` opening tag. It will

look something like:

```
<domain type='kvm'>
```

Modify it to declare the QEMU namespace:

```
<domain type='kvm' xmlns:qemu='[http://libvirt.org/schemas/domain/qemu/1.0](http://libvirt.org/schemas/domain/qemu/1.0)'>
```

### 2.3 Add the KVMFR Command-Line Block

Scroll to the **very bottom** of the file, just before the closing `</domain>`

tag (after `</devices>`). Paste the following strictly serialized JSON block:

```
  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':67108864,'share':true}"/>
  </qemu:commandline>
```

> **Size field:** The `'size'` value is in **bytes**. It must match your
> 
> `static_size_mb` setting from Phase 1 exactly.
> 
> |   |   |
> |---|---|
> |**static_size_mb**|**'size' in bytes**|
> |32|`33554432`|
> |**64**|**`67108864`**|
> |128|`134217728`|
> 
> This guide uses 64 MiB (1440p) → `67108864`. Adjust both files to match if you
> 
> chose a different size.

> **Legacy syntax warning:** If you use the old flat-string QEMU syntax
> 
> (`ivshmem-plain,id=shmem0,...`) on QEMU ≥ 6.2 with libvirt ≥ 7.9, QEMU will
> 
> abort with a `PCI: slot 1 function 0 not available` error. The JSON
> 
> single-quote syntax shown above is the correct modern QOM form.

### 2.4 Where the Block Goes (Context)

```
      <!-- ... rest of your <devices> section ... -->
      <memballoon model="none"/>
    </devices>

    <!-- ↓ PASTE qemu:commandline HERE — outside </devices>, inside </domain> ↓ -->
    <qemu:commandline>
      <qemu:arg value="-device"/>
      <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
      <qemu:arg value="-object"/>
      <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':67108864,'share':true}"/>
    </qemu:commandline>

  </domain>
```

> **memballoon:** The `<memballoon model="none"/>` shown above is strongly
> 
> recommended for all GPU passthrough setups. The VirtIO memory balloon device
> 
> causes significant latency in KVMFR environments by breaking continuous memory geometries.
> 
> Find the existing `<memballoon>` tag in your XML and change its model attribute to `none`.

### 2.5 Recommended: VirtIO Input Devices

For proper keyboard and mouse handling through the SPICE channel (which Looking

Glass uses for input), ensure your `<devices>` section contains:

```
<!-- Replace or supplement any existing input devices with these -->
<input type='mouse' bus='virtio'/>
<input type='keyboard' bus='virtio'/>
```

Remove any `<input type='tablet'/>` device, as the emulated absolute pointing device will directly conflict with Looking Glass Wayland constraints. The VirtIO mouse driver requires the **vioinput** driver from the `virtio-win` package installed in the Windows guest.

### 2.6 Apply and Test

Save the XML and exit the editor. libvirt will validate the file on save. If

it rejects it, re-open and check that both the namespace and the commandline

block are present.

Start the VM:

```
sudo virsh start win10
```

Confirm the VM started without errors:

```
sudo virsh domstate win10
# Expected: running
```

## Phase 3 — Windows Guest: Drivers and Virtual Display

### 3.1 Find the VM's IP Address

```
# Wait a few seconds for the guest DHCP lease to appear
sudo virsh domifaddr win10
```

Note the IPv4 address (e.g., `192.168.122.45`). You will use this for RDP.

### 3.2 Connect via FreeRDP v3 (Rescue Bridge)

The Arch Linux `freerdp` package ships the v3 binary as `xfreerdp3` (with

binary versioning enabled at build time to coexist with the legacy `freerdp2`

package):

```
xfreerdp3 \
  /u:"Administrator" \
  /v:192.168.122.45 \
  /dynamic-resolution \
  /size:1920x1080 \
  /cert:ignore
```

Replace the IP and credentials as appropriate. This RDP session is your rescue

bridge — you will use it to configure drivers while the emulated display is

disabled.

### 3.3 Install VIRTIO-WIN Drivers

Inside the RDP session, if you have not already done so, install the VirtIO

Windows drivers. These are required for the VirtIO keyboard and mouse inputs

configured in Phase 2.

Download the ISO from: [https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/](https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/ "null")

Mount it and run `virtio-win-guest-tools.exe` to install all drivers at once,

including **vioinput** (VirtIO keyboard/mouse) and **SPICE Guest Agent**

(clipboard synchronization).

### 3.4 Install the NVIDIA Guest Driver

Install the standard NVIDIA driver for your GPU within the Windows VM via RDP.

Download from [https://www.nvidia.com/Download/index.aspx](https://www.nvidia.com/Download/index.aspx "null") and run the

installer normally. A reboot will be required.

### 3.5 Install the Looking Glass Host Application

The Looking Glass **host** application runs in Windows and is responsible for

capturing the NVIDIA framebuffer and writing it to the KVMFR device. Its

version **must exactly match** the client you installed on the host.

For a `looking-glass-git` client, obtain the corresponding host binary:

- **Option A (recommended):** Download the matching nightly host binary from
    
    the Looking Glass CI: [https://looking-glass.io/downloads](https://looking-glass.io/downloads "null")
    
- **Option B:** Build from source using the same git commit
    

Install it to `C:\Program Files\Looking Glass (host)\` and configure it to run

at startup (e.g., as a Scheduled Task or via `looking-glass-host.ini`).

### 3.6 Disable the Emulated Display Adapter

The emulated QXLAN/Microsoft Basic Display Adapter must be violently disabled to force

Windows to use the passed-through NVIDIA GPU as its primary render target. Do

this via RDP so you retain display access after the emulated adapter goes dark.

Inside the RDP session:

1. Open **Device Manager** (`devmgmt.msc`)
    
2. Expand **Display Adapters**
    
3. Right-click **Red Hat QXL controller** (or **Microsoft Basic Display Adapter**
    
    if QXL is not present)
    
4. Select **Disable device → Yes**
    

Windows will lose the emulated display and scan for the next available GPU. The

NVIDIA driver should activate and Windows will render to the NVIDIA card. Your

RDP session may stutter briefly but will remain active since RDP is an

independent channel.

### 3.7 Install VDD — Virtual Display Driver

With no physical monitor connected to the NVIDIA GPU, Windows will not have a

render target and the GPU will go idle (Code 43 error in some cases). The VDD

(Virtual Display Driver) solves this by presenting a fake monitor to Windows

via the IddCx class extension framework.

**Download:** [https://github.com/VirtualDrivers/Virtual-Display-Driver/releases](https://github.com/VirtualDrivers/Virtual-Display-Driver/releases "null")

Download the latest release zip. Inside the RDP session, extract it and install

the driver. Two methods are available:

**Method A — VDC (Virtual Driver Control) GUI (recommended):**

Run `VirtualDriverControl.exe` from the release package. Use the GUI to install

the driver and confirm a virtual monitor appears.

**Method B — Manual INF install:**

Right-click `VirtualDisplayDriver.inf` → **Install**. Windows will install the

driver certificate and activate the virtual monitor. (You must accept the cert warning).

Verify success: Right-click the desktop → **Display Settings**. You should see

two displays: your RDP session and the new virtual monitor attached to the NVIDIA

GPU.

### 3.8 Configure vdd_settings.xml (SDR Strict Restraint)

The VDD configuration file lives at `C:\VirtualDisplayDriver\vdd_settings.xml`.

The default manifest is heavily polluted with unhinged resolutions (up to 8K) and extreme refresh rates that will instantly overflow your statically allocated 64 MiB host buffer.

Furthermore, Windows 10 **cannot** process the HDR/HDR+ capabilities injected into the newer VDD releases. You must aggressively purge the XML file down to a rigid SDR layout that perfectly matches your KVMFR memory size.

Open the file in a text editor and replace it entirely with the following structure (adjusting strictly the Width/Height to match your SDR target):

```
<?xml version='1.0' encoding='utf-8'?>
<VirtualDisplaySettings>
   <Monitors>1</Monitors>
   <Resolution>
       <Width>2560</Width>
       <Height>1440</Height>
       <RefreshRate>144</RefreshRate>
   </Resolution>
</VirtualDisplaySettings>
```

After editing, manually disable and re-enable the Virtual Display Device in the Windows Device Manager to purge the DWM cache and commit the new SDR constraints to the registry.

> **Set the virtual monitor as primary:** In Display Settings, drag the VDD
> 
> monitor to the left so it is Monitor 1. Confirm the NVIDIA adapter is shown
> 
> as the associated GPU. This ensures the Looking Glass host captures the correct
> 
> output.

## Phase 4 — Arch Linux Host: Hyprland Wayland Integration

### 4.1 Create the Configuration File

The `looking-glass-client` binary reads its settings from

`~/.config/looking-glass/client.ini`. Running Hyprland on an Optimus/NVIDIA host requires a highly specialized configuration to prevent explicit sync failures (EGL flickering) and Wayland fractional scaling distortions.

```
mkdir -p ~/.config/looking-glass
nvim ~/.config/looking-glass/client.ini
```

Paste the following tailored block:

```
; Looking Glass Client Configuration
; June 2026 — Hyprland / Wayland / Kernel 7.x

[app]
; Point to the KVMFR character device
shmFile=/dev/kvmfr0
; Allow zero-copy hardware transfers
allowDMA=yes
; FORCE OpenGL. The EGL renderer under Wayland/NVIDIA causes catastrophic explicit sync flickering
renderer=opengl

[opengl]
; Defer Vblank timing to Hyprland's atomic mode setting to kill double-vsync input lag
vsync=no
; Disable driver-level frame queuing to dispatch DXGI frames immediately
preventBuffer=yes
mipmap=yes
; Vital fail-safe optimization if your iGPU is an AMD Ryzen chip
amdPinnedMem=yes

[wayland]
; Reject Hyprland's wp_fractional_scale_v1 protocol to maintain absolute 1:1 pixel mapping
fractionScale=no
; Enable strict pointer constraints for 3D camera panning and containment
warpSupport=yes

[win]
autoResize=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes

[input]
; escapeKey uses Linux input event codes (97 = KEY_RIGHTCTRL)
escapeKey=97
; Use raw mouse input — essential for accurate gaming
rawMouse=yes
hideCursor=yes
```

### 4.2 Launch the Client

```
looking-glass-client
```

No CLI flags are required. The client will connect to `/dev/kvmfr0` automatically and utilize the OpenGL Wayland pipelines to render the Windows 10 desktop.

**Default key bindings (escape key = Right Ctrl):**

|   |   |
|---|---|
|**Combo**|**Action**|
|`RCtrl`|Toggle mouse/keyboard capture mode|
|`RCtrl` + `Q`|Quit Looking Glass|
|`RCtrl` + `F`|Toggle fullscreen|
|`RCtrl` + `D`|Toggle FPS overlay|
|`RCtrl` + `O`|Enter overlay/configuration mode|
|`RCtrl` + `I`|Toggle SPICE input|

## Phase 5 — Troubleshooting

### Black Screen on Connect

Looking Glass opens but the window is black. The NVIDIA GPU is not sending

frames because no active display output is configured.

**Fix:**

1. Force shutdown the VM: `sudo virsh destroy win10`
    
2. Start it again: `sudo virsh start win10`
    
3. Launch the client: `looking-glass-client`
    
4. Click the black LG window to focus it
    
5. Press `Right Ctrl` to enter capture mode (cursor disappears)
    
6. Blindly send `Win` + `P`, wait 1 second, then press `Down`, `Down`, `Enter`
    

This navigates the Windows "Project" menu from "PC screen only" to "Extend",

waking the NVIDIA driver and starting frame output into the KVMFR buffer.

### `/dev/kvmfr0` Is a Regular File (Not a Character Device)

Symptom: `ls -l /dev/kvmfr0` shows `-rw` instead of `crw`.

QEMU started before the KVMFR module was loaded and created a regular file at

that path. Fix:

```
sudo virsh destroy win10
sudo rm /dev/kvmfr0
sudo modprobe kvmfr
sudo virsh start win10
```

To prevent recurrence, ensure `/etc/modules-load.d/kvmfr.conf` is in place

(Phase 1.3) so the module is always loaded before any VM starts.

### VM Fails to Start: `cgroup` Permission Denied

libvirt's cgroups policy is blocking QEMU from opening `/dev/kvmfr0`. Confirm

the device is in `cgroup_device_acl` in `/etc/libvirt/qemu.conf` and that

`libvirtd` was restarted afterward (Phase 1.6).

### Looking Glass Reports Wrong Memory Size

The `size` value in the `qemu:commandline` JSON block does not match

`static_size_mb` in `/etc/modprobe.d/kvmfr.conf`. Both must agree. Recalculate

from the table in Phase 1.2 and update both files, then reload the module and

restart the VM.

### xfreerdp3: "Command Not Found"

The Arch `freerdp` package ≥ 3.4.0-5 uses versioned binary names. The correct

binary is `xfreerdp3`, not `xfreerdp`. Confirm:

```
which xfreerdp3
# Expected: /usr/bin/xfreerdp3
```

If the command is missing entirely, confirm `freerdp` (not `freerdp2`) is

installed: `pacman -Q freerdp`.

### KVMFR DKMS Fails to Build After Kernel Upgrade

On kernels ≥ 7.0, the KVMFR module requires rapid-fire API patch backports for the memory management tree. Ensure you are strictly using `looking-glass-module-dkms-git` and pull the latest AUR updates:

```
paru -Syu looking-glass-module-dkms-git
```

## Appendix: Technical Reference

### Full Pipeline Component Summary

|   |   |   |   |
|---|---|---|---|
|**Component**|**Location**|**Role**|**Failure Symptom**|
|**KVMFR module**|Host kernel (`/dev/kvmfr0`)|DMA frame relay bus between guest GPU and host|`crw` not present; LG fails to open device|
|**`/etc/modprobe.d/kvmfr.conf`**|Host|Configures IVSHMEM size at module load|Module loads but `/dev/kvmfr0` is wrong size|
|**`/etc/udev/rules.d/70-kvmfr.rules`**|Host|Grants `kvm` group + seat user access|Permission denied when LG client opens device|
|**`cgroup_device_acl`**|`/etc/libvirt/qemu.conf`|Allows QEMU to open the char device|VM fails to start with cgroup policy error|
|**`qemu:commandline` JSON**|libvirt XML|Passes KVMFR device to QEMU via QOM|LG host in guest cannot find IVSHMEM PCI device|
|**VDD**|Windows 10 guest|Provides NVIDIA GPU with an SDR virtual monitor|GPU goes idle; no frames captured (Code 43)|
|**LG Host App**|Windows 10 guest|Captures NVIDIA framebuffer → KVMFR|Black screen; no frames in shared memory|
|**`client.ini`**|`~/.config/looking-glass/`|Hyprland-specific Wayland/OpenGL constraints|Severe explicit sync flickering; blurry scaling|
|**`xfreerdp3`**|Host (`/usr/bin/xfreerdp3`)|RDP rescue bridge for Windows config|Cannot access Windows when emulated display is off|

### Memory Size Quick Reference (SDR Strict)

|   |   |   |
|---|---|---|
|**static_size_mb**|**qemu:commandline 'size' (Bytes)**|**Max SDR resolution**|
|32|33554432|1080p / 1200p|
|**64**|**67108864**|**1440p (Standard Recommendation)**|
|128|134217728|4K|