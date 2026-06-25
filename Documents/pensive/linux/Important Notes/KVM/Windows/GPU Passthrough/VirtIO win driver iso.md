# 🚀 KVM Performance: The VirtIO Drivers

Windows, by default, does not understand how to talk directly to KVM's hardware. If you run Windows without these drivers, it will use generic emulation (IDE/SATA), which is significantly **slower**.

To get near-native performance, we need to teach Windows how to speak "Linux KVM." We do this by mounting a specific ISO file containing the **VirtIO drivers** during installation.

> [!abstract] Goal
> 
> We need to download the virtio-win.iso file. This acts as a "driver CD" that we will insert into the Windows VM later.

## Step 1: Download the Drivers

Choose **one** of the following options.

### Option A: Via AUR (Recommended)

This is the easiest method if you are using an Arch-based system with an AUR helper like `paru`. It manages updates automatically.

```
paru -S --needed virtio-win
```

> [!check] Verification
> 
> Once installed, the system usually places the image in one of these two locations. Run these commands to verify the file exists:
> 
> **Location 1 (Standard):**
> 
> ```bash
> ls -lah /var/lib/libvirt/images
> ```
> 
> **Location 2 (Alternative):**
> 
> ```bash
> ls -lah /usr/share/virtio
> ```

### Option B: Manual Download

If you prefer not to use the AUR, you can download the stable ISO directly from the Fedora project (the upstream developers for KVM drivers).

We will download this into your temporary `zram` mount point.

```bash
cd /mnt/zram1/
wget https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso
```

> [!info] File Location
> 
> Using this method, your ISO file is located at: /mnt/zram1/virtio-win.iso

## 🔮 Preview: How we will use this

_You don't need to do this yet. This is just an explanation of why we downloaded the file._

When we configure the Virtual Machine in a later step, we will use this ISO to make the hard drive and internet speed fast.

> [!example] Workflow Preview
> 
> 1. **Disk Bus:** We will set the Windows hard drive to use **VirtIO** (instead of SATA).
>     
> 2. **Network:** We will set the Internet adapter to use **VirtIO**.
>     
> 3. **The Driver CD:** We will attach the `virtio-win.iso` we just downloaded as a **CD-ROM**.
>     
> 4. **Installation:** When the Windows installer complains it "cannot find a hard drive," we will click **Load Driver** and point it to this CD.
>