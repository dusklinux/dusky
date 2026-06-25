# 🖥️ Ultimate Guide: Muxless Laptop GPU Passthrough

> [!abstract] Objective
> 
> Goal: Achieve near-native gaming performance in a Windows 11 KVM Guest on an Arch Linux Host.
> 
> **The Challenge:** Laptops route the high-performance NVIDIA GPU through the weaker Intel iGPU in hybrid/optimus mode. When we pass the NVIDIA card to a VM, it becomes "Headless"—it has no physical video output connected to it.
> 
> **The Solution:**
> 
> 1. **Virtual Display Driver (IDD):** Tricks Windows into thinking a monitor is plugged in.
>     
> 2. **IVSHMEM (Shared Memory):** A block of RAM shared between Linux and Windows. Windows copies video frames here.
>     
> 3. **Looking Glass:** A Linux application that reads that RAM and puts the Windows screen on your Linux desktop.
>     

## 🏗️ Phase 1: Host Preparation (Arch Linux)

We need the viewer application (`looking-glass`) and a generic remote desktop tool (`freerdp`) to access the VM while we configure the video drivers.

### 1. Install Dependencies

Run the following in your host terminal:

```bash
# 1. Install Looking Glass Client (AUR)
# This is the high-performance viewer we will use for gaming.
paru -S --needed looking-glass

# 2. Install FreeRDP v3 (Official Repo)
# We need this as a "Rescue Bridge" to access the VM later when we disable
# the default video adapter. Without it, you would see a black screen.
sudo pacman -S --needed freerdp
```

### 2. Configure Shared Memory

Looking Glass uses a file in RAM (`/dev/shm`) as a "whiteboard." Windows draws on it, Linux reads it. By default, regular users cannot access this memory, so we must create a permission rule.

Create a systemd temporary file configuration for the user `new` (replace with your username if different):

```bash
sudo rm -f /dev/shm/looking-glass
echo "f /dev/shm/looking-glass 0660 new kvm -" | sudo tee /etc/tmpfiles.d/10-looking-glass.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/10-looking-glass.conf
```

To avoid Out-Of-Memory (OOM) latency, pre-allocate the physical RAM space (e.g. 64M for 1440p SDR):

```bash
sudo fallocate -l 64M /dev/shm/looking-glass
sudo chown new:kvm /dev/shm/looking-glass
sudo chmod 0660 /dev/shm/looking-glass
```

> [!check] Verify the File
> Ensure the file exists with the correct size and ownership:
> ```bash
> ls -lh /dev/shm/looking-glass
> ```
> Output should show `64M` owned by `new kvm`.

## 🔌 Phase 2: The XML Bridge (QEMU Configuration)

Now we must tell the Virtual Machine to map the shared memory file we just prepared. This acts as the physical link between the two systems.

### 1. Edit the VM Configuration

Open your VM configuration file in the terminal. Replace `win_10_dusky` with your VM name if different.

```bash
# Check your VM name
sudo virsh list --all

# Edit the XML using Neovim (or your default editor)
sudo EDITOR=nvim virsh edit win_10_dusky
```

### 2. Add the Shared Memory Device and XML Optimizations

Scroll to the bottom of the `<devices>` section. We must declare the `qemu` namespace on the root `<domain>` tag and add the mapped shared memory block under a `<qemu:commandline>` section at the very bottom.

Also, set `<memballoon model='none'/>` to eliminate latency.

```xml
<!-- Declare QEMU namespace on root domain tag -->
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  ...
  <devices>
    ...
    <memballoon model='none'/>
  </devices>

  <!-- Add at the very bottom of the XML file, right before </domain> -->
  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
  </qemu:commandline>
</domain>
```

### 3. The "Clean Slate" Reset

XML changes regarding memory are not applied on a simple reboot. You must perform a **Hard Reset** and clear the old file to prevent permission errors.

```bash
# 1. Kill the VM
sudo virsh destroy win_10_dusky

# 2. Delete the old 0-byte/bad file.
# If we don't do this, QEMU might fail to resize it or inherit bad permissions.
sudo rm -f /dev/shm/looking-glass

# 3. Start the VM
sudo virsh start win_10_dusky
```

### 4. Verify the Hardware Link

Check the file size on the host again. This confirms QEMU successfully allocated the memory.

```bash
ls -lh /dev/shm/looking-glass
```

| Result | Size in Bytes | Status |
| :--- | :--- | :--- |
| ✅ **Success** | **~67,108,864** | (64MB) Ready for High Res SDR (1440p). |
| ❌ **Failure** | **4,194,304** | (4MB) XML size tag is missing/wrong. |
| ❌ **Failure** | **0** | VM is not running or XML is invalid. |

# Disable the Display driver (Two options)
### [[The RDP method to disable display driver]] (complicated but safe)

**OR** 
### Disable the Basic Adapter right from virt manager window (Easier but sometimes causes major issues if wrong display disabled (black screen))

1. Open **Device Manager**.
2. Expand **Display Adapters**.
3. Right-click **Microsoft Basic Display Adapter** (or Red Hat QXL).
4. Select **Disable Device**.

_Result:_ Windows stops rendering to the Virt-Manager window. It scans for the next available GPU and wakes up the NVIDIA card.

### 3. Wake the Virtual Monitor

If RDP is the _only_ monitor Windows sees, Looking Glass will see nothing. We need to activate the IDD (Virtual Display) driver you installed previously.

1. Open **Command Prompt (Admin)** inside Windows.
2. Run the IDD enable command (e.g., `deviceinstaller64 enableidd 1`).
3. **Verify:** Right-click desktop -> Display Settings. You should see **Monitor 1 (RDP)** and **Monitor 2 (Virtual/NVIDIA)**.

## 🚀 Phase 4: Launching Looking Glass

We are ready to view the shared memory buffer.

### 1. Fix Permissions (The Race Condition)

When QEMU starts, it may recreate the file owned by `root` or `libvirt-qemu`. To reclaim it manually for your user:

```bash
# Give ownership back to new
sudo chown new:kvm /dev/shm/looking-glass

# Ensure Group (kvm) can read/write
sudo chmod 0660 /dev/shm/looking-glass
```

> [!NOTE]
> If `fs.protected_regular = 1` is enabled on the host, you cannot just chown/chmod it while it's owned by another user. You must delete the file and recreate/allocate it properly as shown in Step 2 of Phase 1.

### 2. Launch Client

Since laptop keyboards often lack a **Scroll Lock** key (the default capture key), we remap the capture key to **F6**.

-f: Force use of the specific shared memory file
-m: Remap the "Capture Key" to Right Control
```bash
looking-glass-client -f /dev/shm/looking-glass -m KEY_F6
```

## 🧠 Phase 5: Troubleshooting

### "Black Screen" on Connect

If Looking Glass opens but the window remains black, Windows has "forgotten" to enable the Virtual Monitor output.

**The Fix:**

1. **Hard Reset:** Force shutdown the VM via Virt-Manager. Start it again.
    
2. **Launch:** Run the Looking Glass command (from Phase 4, Step 2).
    
3. **Focus:** Click the Looking Glass window (it will be black).
    
4. **Capture:** Press **F6** (to capture keyboard input).
    
5. **The Blind Shortcut:**
    
    - Press `Win` + `P`
        
    - Wait 1 second
        
    - Press `Down Arrow`
        
    - Press `Down Arrow`
        
    - Press `Enter`
        

> [!info] What did that do?
> 
> This blindly navigates the Windows "Project" menu to switch from "PC Screen Only" to "Extend" or "Duplicate". This forces the NVIDIA driver to wake up and start filling the shared memory with frames.

## 📚 Technical Summary (The "Why")

|   |   |   |
|---|---|---|
|**Component**|**Role**|**Why it fails**|
|**/dev/shm**|**RAM Disk.** Used for zero-copy data transfer between Linux and Windows.|If file is 0 bytes, XML `<size>` is missing. If "Permission Denied", `chown` is needed.|
|**IVSHMEM**|**Virtual PCI Device.** Connects Guest RAM to Host RAM.|Needs `ivshmem-plain` model in XML to function.|
|**IDD Driver**|**Fake Monitor.** Plugs a "ghost" monitor into the GPU.|Essential for Muxless laptops. Without it, the NVIDIA GPU goes to sleep (Code 43).|
|**RDP**|**Rescue Bridge.** Remote Desktop Protocol.|Used to configure Windows drivers when the main display is disabled.|
|**Basic Adapter**|**Emulated GPU.** The slow software graphics card.|Must be DISABLED to force games to run on the NVIDIA GPU.|
