# Muxless Laptop GPU Passthrough with Looking Glass

> **Target Stack — June 2026**
> 
> Arch Linux · Kernel 7.x · systemd 260 · QEMU ≥ 9.x · libvirt ≥ 10.x · Windows 10 Guest (De-bloated)

## Architecture: The Full Pipeline

> [!INFO] ASUS TUF F15
> even though i have a mux capable laptop this is the method to follow, it works well

On a muxless (Optimus) laptop the NVIDIA GPU has no physical video output — all

pixels are routed through the weaker Intel/AMD iGPU. When you pass the NVIDIA

card to a KVM guest, it becomes headless. The following three-component stack

solves this entirely in software:

```
Windows Guest                   Kernel / Shared Memory             Arch Host
─────────────────────────────   ─────────────────────────────────  ─────────────────────
NVIDIA GPU (passed through)  →  Shared Memory  →                   looking-glass-client
VDD (fake monitor)               /dev/shm/looking-glass              (renders on your display)
LG Host App (frame capture)      (RAM device, zero-copy)
```

|   |   |
|---|---|
|**Component**|**Role**|
|**VDD** (Virtual Display Driver)|Creates a ghost monitor for the NVIDIA GPU so Windows has a render target|
|**Looking Glass Host**|Runs in Windows; captures the NVIDIA framebuffer and writes it to shared memory|
|**Shared Memory (IVSHMEM)**|RAM-backed shared memory file (`/dev/shm/looking-glass`) — provides a true zero-copy window between guest and host|
|**looking-glass-client**|Reads `/dev/shm/looking-glass` on the host and renders the Windows desktop in a window|
|**xfreerdp3**|FreeRDP v3 rescue bridge — used to configure Windows drivers while the emulated display is disabled|

## Prerequisites Checklist

Before starting, confirm your system meets these requirements:

- [ ] PCI passthrough (IOMMU, VFIO) is already working — the NVIDIA GPU is
    
    bound to `vfio-pci` and assigned to your VM
    
- [ ] Your VM is a **Windows 10** guest managed by **libvirt** (virt-manager is fine; it
    
    uses libvirt as its back end)
    
- [ ] You are a member of the `kvm` group: `groups $USER | grep kvm`
    
- [ ] `dkms` and `linux-headers` (matching your running kernel) are installed
    

## Phase 1 — Host Layer: Shared Memory Setup (/dev/shm)

To avoid compilation overhead and build failures on kernel updates, we use a RAM-backed shared-memory file located at `/dev/shm/looking-glass` instead of the DKMS KVMFR module.

### 1.1 Install Packages

Install the Looking Glass client and FreeRDP v3 rescue bridge.

```bash
paru -S --needed looking-glass freerdp
```

> [!Note]+ Windows Host Application
> Make sure to download the matching Windows host application build! It must match the client version exactly.
> Get it from: https://looking-glass.io/downloads

### 1.2 Calculate Your IVSHMEM Memory Size

Choose a size based on your target resolution. It must be consistent across the host memory allocation and the libvirt XML configuration.

Because this environment targets a de-bloated Windows 10 guest, advanced IDD capabilities like HDR/HDR+ (which strictly require Windows 11 22H2+) are unsupported. All memory calculations **must** be rigidly locked to 32-bit Standard Dynamic Range (SDR) to prevent allocating memory that Windows 10 cannot utilize.

**Formula (SDR):** `width × height × 4 × 2 ÷ 1024 ÷ 1024 + 10`, then round up to the nearest power of 2.

| Display Target | Raw Frame Size (Bytes) | Base + 10 MiB Overhead | Final Allocation |
| :--- | :--- | :--- | :--- |
| 1920×1080 (1080p) | 16,588,800 | 25.82 MiB | **32 MiB** (`33554432` bytes) |
| 1920×1200 (1200p) | 18,432,000 | 27.58 MiB | **32 MiB** (`33554432` bytes) |
| 2560×1440 (1440p) | 29,491,200 | 38.12 MiB | **64 MiB** (`67108864` bytes) |
| 3840×2160 (4K) | 66,355,200 | 73.28 MiB | **128 MiB** (`134217728` bytes) |

> **Practical recommendation:** Use **64 MiB** to perfectly match a standard 1440p high-end laptop display panel target. Do not over-provision memory for HDR on a Windows 10 guest.

### 1.3 Configure systemd-tmpfiles for Boot Persistence

To ensure the shared memory file is created automatically with correct permissions upon host boot, configure a systemd temporary file.

Create the configuration file `/etc/tmpfiles.d/10-looking-glass.conf`:

```
sudo tee /etc/tmpfiles.d/10-looking-glass.conf << 'EOF'
# Create looking-glass shared memory file on boot
# Type | Path | Mode | User | Group | Age | Argument
f /dev/shm/looking-glass 0660 new kvm - -
EOF
```

> [!NOTE]
> Replace `new` with your host username if different.

### 1.4 Pre-allocate Memory & Resolve fs.protected_regular Controls

Host systems with `fs.protected_regular = 1` block writing to or truncating an existing `/dev/shm/looking-glass` file owned by another user (such as if QEMU recreated it as the `qemu` user). 

To safely initialize permissions and allocate memory:

1. **Delete any existing file** to clear ownership locks:
   ```bash
   sudo rm -f /dev/shm/looking-glass
   ```

2. **Trigger systemd-tmpfiles** to create the file structure:
   ```bash
   sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
   ```

3. **Pre-allocate physical RAM** to prevent Out-Of-Memory (OOM) latency during frame sharing. Match the size to your resolution target (e.g. `64M` / `67108864` bytes):
   ```bash
   sudo fallocate -l 64M /dev/shm/looking-glass
   ```
   *(Or alternatively: `sudo truncate -s 64M /dev/shm/looking-glass`)*

4. **Claim ownership and set permissions**:
   ```bash
   sudo chown new:kvm /dev/shm/looking-glass
   sudo chmod 0660 /dev/shm/looking-glass
   ```

### 1.5 Verify Shared Memory Allocation

Verify the file size and ownership details:

```bash
ls -lh /dev/shm/looking-glass
```

## Phase 2 — VM XML: Wiring the IVSHMEM Bridge & Guest Optimizations

We need to make several critical configuration edits to the libvirt domain XML:

1. Declare the `qemu` XML namespace on the root `<domain>` tag.
2. Inject a `<qemu:commandline>` block at the bottom to map `/dev/shm/looking-glass` into the guest.
3. Configure the CPU topology to eliminate Windows performance warning prompts.
4. Disable memory ballooning to eliminate DMA latency.
5. Add the SPICE agent channel for native clipboard sharing.

These **must** be added in a single editing session. Saving after adding only the namespace (but before the commandline block) will cause libvirt to reject the edit.

### 2.1 Open the VM XML

```bash
# Confirm your VM name first
sudo virsh list --all

# Open the XML — replace win_10_dusky with your VM name
sudo EDITOR=nvim virsh edit win_10_dusky
```

### 2.2 Add the QEMU Namespace to the Root Domain Tag

Locate the first line of the document — the `<domain>` opening tag:

```xml
<domain type='kvm'>
```

Modify it to declare the QEMU namespace:

```xml
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
```

### 2.3 Add the Looking Glass Shared Memory Block

Scroll to the **very bottom** of the file, just before the closing `</domain>` tag (outside the `</devices>` section). Paste the following command-line mapping:

```xml
  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
  </qemu:commandline>
```

> **Size field:** The `'size'` value is in **bytes**. It must match your allocated shared memory file size exactly (e.g. `67108864` for 64 MiB).

### 2.4 Disable Memory Ballooning

The VirtIO memory balloon device dynamically claims guest memory, which breaks continuous memory geometries and causes severe frame latency in Looking Glass environments.

Find the `<memballoon>` tag in the `<devices>` section and set its model to `none`:

```xml
<memballoon model='none'/>
```

### 2.5 Configure CPU Topology

Windows will display performance warning prompts if the virtualized CPU topology is reported as flat or mismatched. Align your `<cpu>` block topology settings to match the total vCPU allocation.

For example, if assigning 16 vCPUs (e.g., `<vcpu placement='static'>16</vcpu>`):

```xml
  <cpu mode='host-passthrough' check='none' migratable='on'>
    <topology sockets='1' dies='1' clusters='1' cores='8' threads='2'/>
  </cpu>
```
Ensure that `sockets × dies × clusters × cores × threads` exactly equals the total vCPU count.

### 2.6 Inject SPICE Clipboard Sharing Channel

To enable native host-guest clipboard synchronization, define the SPICE guest agent channel inside the `<devices>` section:

```xml
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>
```

### 2.7 Recommended: VirtIO Input Devices

For proper keyboard and mouse handling through the SPICE channel, ensure your `<devices>` section contains:

```xml
    <input type='mouse' bus='virtio'/>
    <input type='keyboard' bus='virtio'/>
```

Ensure you remove any `<input type='tablet'/>` device, as absolute pointing devices conflict with Looking Glass pointer constraints. (Requires the **vioinput** driver inside Windows).

### 2.8 Apply and Test

Save the XML and exit the editor. libvirt will validate the syntax. 

Start the VM to test:
```bash
sudo virsh start win_10_dusky
```

Confirm it boots successfully:
```bash
sudo virsh domstate win_10_dusky
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

The Looking Glass **host** application runs in Windows and is responsible for capturing the NVIDIA framebuffer and writing it to the shared memory device. Its version **must exactly match** the client you installed on the host.

For a standard or git client, obtain the corresponding host binary:

- **Option A (recommended):** Download the matching host binary from the Looking Glass website: [https://looking-glass.io/downloads](https://looking-glass.io/downloads)
- **Option B:** Build from source using the same git commit.

Install it to `C:\Program Files\Looking Glass (host)\` and configure it to run at startup (e.g., as a Scheduled Task or via `looking-glass-host.ini`).

### 3.6 Disable the Emulated Display Adapter

The emulated QXL/Microsoft Basic Display Adapter must be disabled to force Windows to use the passed-through NVIDIA GPU as its primary render target. Do this via RDP so you retain display access after the emulated adapter goes dark.

Inside the RDP session:

1. Open **Device Manager** (`devmgmt.msc`).
2. Expand **Display Adapters**.
3. Right-click **Red Hat QXL controller** (or **Microsoft Basic Display Adapter** if QXL is not present).
4. Select **Disable device → Yes**.

Windows will lose the emulated display and scan for the next available GPU. The NVIDIA driver should activate and Windows will render to the NVIDIA card. Your RDP session may stutter briefly but will remain active since RDP is an independent channel.

### 3.7 Install VDD — Virtual Display Driver

With no physical monitor connected to the NVIDIA GPU, Windows will not have a render target and the GPU will go idle (Code 43 error in some cases). The VDD (Virtual Display Driver) solves this by presenting a fake monitor to Windows via the IddCx class extension framework.

**Download:** [https://github.com/VirtualDrivers/Virtual-Display-Driver/releases](https://github.com/VirtualDrivers/Virtual-Display-Driver/releases)

Download the latest release zip. Inside the RDP session, extract it and install the driver. Two methods are available:

**Method A — VDC (Virtual Driver Control) GUI (recommended):**

Run `VirtualDriverControl.exe` from the release package. Use the GUI to install the driver and confirm a virtual monitor appears.

**Method B — Manual INF install:**

Right-click `VirtualDisplayDriver.inf` → **Install**. Windows will install the driver certificate and activate the virtual monitor. (You must accept the cert warning).

Verify success: Right-click the desktop → **Display Settings**. You should see two displays: your RDP session and the new virtual monitor attached to the NVIDIA GPU.

### 3.8 Configure vdd_settings.xml (SDR Strict Restraint)

The VDD configuration file lives at `C:\VirtualDisplayDriver\vdd_settings.xml`.

The default settings file is heavily polluted with unhinged resolutions (up to 8K) and extreme refresh rates that will instantly overflow your statically allocated 64 MiB host buffer.

Furthermore, Windows 10 **cannot** process the HDR/HDR+ capabilities injected into the newer VDD releases. You must aggressively purge the XML file down to a rigid SDR layout that perfectly matches your allocated shared memory size.

Open the file in a text editor and replace it entirely with the following structure (adjusting strictly the Width/Height to match your SDR target):

```xml
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

> **Set the virtual monitor as primary:** In Display Settings, drag the VDD monitor to the left so it is Monitor 1. Confirm the NVIDIA adapter is shown as the associated GPU. This ensures the Looking Glass host captures the correct output.

---

## Phase 4 — Arch Linux Host: Hyprland Wayland Integration

### 4.1 Create the Configuration File

The `looking-glass-client` binary reads its settings from `~/.config/looking-glass/client.ini`. Running Hyprland on an Optimus/NVIDIA host requires a highly specialized configuration to prevent explicit sync failures (EGL flickering) and Wayland fractional scaling distortions.

> [!TIP] Automated Client Configuration
> If you prefer not to configure this manually, you can run the following helper script to automatically create or merge your `client.ini` with all optimal settings (including `F6` escape key and SPICE clipboard sharing):
> ```bash
> /home/new/user_scripts/dusky_vm/passthrough/60_configure_client_ini.py
> ```

Otherwise, execute the manual setup below:

```bash
mkdir -p ~/.config/looking-glass
nvim ~/.config/looking-glass/client.ini
```

Paste the following tailored block:

```ini
; Looking Glass Client Configuration
; June 2026 — Hyprland / Wayland / Kernel 7.x

[app]
; Point to the shared memory file
shmFile=/dev/shm/looking-glass
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
; Enable pointer constraints for 3D camera panning and containment
warpSupport=yes

[win]
autoResize=yes
keepAspect=yes
dontUpscale=yes
noScreensaver=yes
borderless=yes

[input]
; escapeKey uses Linux input event codes (64 = KEY_F6)
escapeKey=64
; Use raw mouse input — essential for accurate gaming
rawMouse=yes
hideCursor=yes

[spice]
; Enable host/guest clipboard synchronization via SPICE
enable=yes
clipboard=yes
```

### 4.2 Launch the Client

```bash
looking-glass-client
```

No CLI flags are required. The client will connect to `/dev/shm/looking-glass` automatically and utilize the OpenGL Wayland pipelines to render the Windows desktop.

**Default key bindings (escape key = F6):**

| Combo | Action |
| :--- | :--- |
| `F6` | Toggle mouse/keyboard capture mode |
| `F6` + `Q` | Quit Looking Glass |
| `F6` + `F` | Toggle fullscreen |
| `F6` + `D` | Toggle FPS overlay |
| `F6` + `O` | Enter overlay/configuration mode |
| `F6` + `I` | Toggle SPICE input |

---

## Phase 5 — Troubleshooting

### Black Screen on Connect

Looking Glass opens but the window is black. The NVIDIA GPU is not sending frames because no active display output is configured.

**Fix:**

1. Force shutdown the VM: `sudo virsh destroy win_10_dusky`
2. Start it again: `sudo virsh start win_10_dusky`
3. Launch the client: `looking-glass-client`
4. Click the black LG window to focus it.
5. Press `F6` to enter capture mode (cursor disappears).
6. Blindly send `Win` + `P`, wait 1 second, then press `Down`, `Down`, `Enter`.

This navigates the Windows "Project" menu from "PC screen only" to "Extend", waking the NVIDIA driver and starting frame output into the shared memory.

### Permission Denied / Unable to Open `/dev/shm/looking-glass`

**Symptom:** Looking Glass client fails to start with "Permission Denied" or the VM fails to boot.

**Fix:**
Host systems with `fs.protected_regular = 1` block writing to or truncating an existing `/dev/shm/looking-glass` file owned by another user.
1. Force stop the VM: `sudo virsh destroy win_10_dusky`
2. Delete the file: `sudo rm -f /dev/shm/looking-glass`
3. Re-create and pre-allocate the memory to restore proper permissions:
   ```bash
   sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
   sudo fallocate -l 64M /dev/shm/looking-glass
   sudo chown new:kvm /dev/shm/looking-glass
   sudo chmod 0660 /dev/shm/looking-glass
   ```

### Looking Glass Reports Wrong Memory Size

**Symptom:** Looking Glass client errors out claiming memory size mismatch.

**Fix:**
The size parameter in your XML command-line block (e.g. `size:67108864` for 64 MiB) must exactly match the size of the pre-allocated `/dev/shm/looking-glass` file on the host. If they do not match, delete the file and recreate it with the correct size using `fallocate -l`:
```bash
sudo rm -f /dev/shm/looking-glass
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
sudo fallocate -l 64M /dev/shm/looking-glass
sudo chown new:kvm /dev/shm/looking-glass
sudo chmod 0660 /dev/shm/looking-glass
```

### Shared Clipboard Not Working

**Symptom:** Copying and pasting text between the host and guest does not work, even with `[spice]` configured in `client.ini`.

**Fix:**
Clipboard sharing relies entirely on the SPICE Guest Agent service (`spice-agent`) running inside Windows.
1. **Check if the service is running:** Open PowerShell (as Administrator) in the VM and run:
   ```powershell
   Get-Service -Name "spice-agent"
   ```
2. **If missing or stopped:**
   * Mount the `virtio-win` ISO (or access it from your VirtIO-FS shared `Z:` drive).
   * Run the `virtio-win-guest-tools.exe` installer to install/repair the agent.
   * If you have a custom debloated ISO where it was removed, download and install `spice-guest-tools` manually.
3. **Relaunch the client:** Once the service is running, close (`F6 + Q`) and restart `looking-glass-client` on the host.

### xfreerdp3: "Command Not Found"

The Arch `freerdp` package uses versioned binary names. The correct binary is `xfreerdp3`, not `xfreerdp`. Confirm:

```bash
which xfreerdp3
# Expected: /usr/bin/xfreerdp3
```

If the command is missing entirely, confirm `freerdp` is installed: `pacman -Q freerdp`.

---

## Appendix: Technical Reference

### Full Pipeline Component Summary

| Component | Location | Role | Failure Symptom |
| :--- | :--- | :--- | :--- |
| **Shared Memory** | Host `/dev/shm/looking-glass` | RAM-backed zero-copy frame sharing bridge | File missing or wrong size; LG client fails to open |
| **`10-looking-glass.conf`** | Host `/etc/tmpfiles.d/` | Creates file with persistent permissions at boot | Permissions revert to root; client denied access |
| **`qemu:commandline` JSON** | libvirt XML | Maps shared memory to guest VM | Guest OS / Looking Glass Host cannot find IVSHMEM PCI device |
| **VDD** | Windows Guest | Provides NVIDIA GPU with an SDR virtual monitor | GPU goes idle; no frames captured (Code 43) |
| **LG Host App** | Windows Guest | Captures NVIDIA framebuffer → shared memory | Black screen; no frames shared |
| **`client.ini`** | Host `~/.config/looking-glass/` | Client integration and scaling controls | Sync flickering, bad mouse warp, scaling blur |
| **`xfreerdp3`** | Host `/usr/bin/xfreerdp3` | RDP rescue connection to configure guest | Cannot access Windows if main display goes black |

### Memory Size Quick Reference (SDR Strict)

| Pre-allocation Size | XML 'size' in bytes | Max SDR Resolution |
| :--- | :--- | :--- |
| `32M` | `33554432` | 1080p / 1200p |
| **`64M`** | **`67108864`** | **1440p (Standard Recommendation)** |
| `128M` | `134217728` | 4K |