# Project Context & AI Rules: Dusky VM Passthrough Setup

This workspace contains automated configuration scripts for setting up a GPU passthrough Windows 10/11 virtual machine (`win_10_dusky`) on Arch Linux with Looking Glass frame sharing.

---

## 1. Environment & Target System Details

- **Host OS**: Arch Linux
- **Host User**: (check)
- **Guest VM Name**: (check)
- **Guest IP Address**: (ask the user)
- **Guest OS**: Windows 10
- **Guest SSH Credentials**: User (ask the user), Password (ask the user) (default shell configured to PowerShell)
- **Shared Directory (VirtIO-FS)**: Host `/mnt/zram1` is mounted as drive `Z:` inside the Windows VM.

---

## 2. Arch Linux Host Setup & Scripts Directory

All host-side scripts are located in `/home/new/user_scripts/dusky_vm/passthrough/`:

1. `05_virtio_iso.py` — Configures the VirtIO ISO pool. Uses `paru --skipreview` to install needed AUR packages. Checks if `/mnt/zram1/virtio-win-0.1.285.iso` is available locally before attempting to stream/download.
2. `10_virt_modular_daemon.py` — Manages modular libvirt daemons.
3. `15_gpu_probing_kernal_param_mkinit.py` — Configures kernel parameters and mkinitcpio for GPU passthrough.
4. `20_networking_nmcli.py` — Configures host networking bridging/interfaces.
5. `windows/25_looking_glass.py` — Automates host shared memory creation (`/dev/shm/looking-glass` allocated dynamically at 64 MiB for 1440p target) and sets boot persistence via `/etc/tmpfiles.d/10-looking-glass.conf`.
6. `30_kvm_vm_deploy.py` — Deploys a new Windows VM. Prompts the user before building (defaulting to No).

---

## 3. Important Design Rules & Constraints (For AI)

If a new AI agent starts a conversation in this workspace, follow these strict rules to avoid regression:

### Rule 1: No Automated Guest Driver Installation Logic
- **Do not** write code to automate the download, staging, registry manipulation, or installation of the Virtual Display Driver (VDD) from the host or through the generic setup script (`windows/guest/setup_ssh.py`).
- The user prefers to handle Virtual Display Driver (VDD) installation and setup manually using the guide in `docs/windows/VDD_INSTALL_GUIDE.md`.

### Rule 2: Keep Scripts Standalone and Idempotent
- Do not assume `000_dusky_vm.sh` orchestrator is used. Each Python/Shell/PowerShell script must be completely self-contained and idempotent.

### Rule 3: Libvirt XML Integrity
When updating or modifying the VM XML configuration (specifically in `windows/25_looking_glass.py`):
1. **Disable Memory Ballooning**: Keep `<memballoon model='none'/>` to eliminate DMA latency.
2. **CPU Topology**: Ensure CPU topology (sockets, cores, threads) matches the total vCPUs to prevent warning prompts on Windows.
3. **SPICE Clipboard Sharing**: Ensure the SPICE guest agent channel (`com.redhat.spice.0`) is injected.
4. **Looking Glass Shared Memory**: Use QEMU commandline args to map `/dev/shm/looking-glass` into the guest:
   ```xml
   <qemu:commandline>
     <qemu:arg value="-device"/>
     <qemu:arg value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"/>
     <qemu:arg value="-object"/>
     <qemu:arg value="{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/shm/looking-glass','size':67108864,'share':true}"/>
   </qemu:commandline>
   ```

### Rule 4: Handling `/dev/shm` Permissions
- Host systems with `fs.protected_regular = 1` block writing to/truncating an existing `/dev/shm/looking-glass` file owned by another user.
- To safely manage permissions:
  1. Check if the file exists.
  2. If it does, delete it first using unlink/rmtree.
  3. Create/allocate physical size using `posix_fallocate` or `ftruncate`.
  4. Change ownership to the target user (e.g. `new`) and group `kvm`, and set mode to `0660`.
