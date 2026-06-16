# Enabling Modular Libvirt Daemons

In this step, we are configuring the "engine" that runs your virtual machines. We are switching from the old-school "Monolithic" mode to the modern "Modular" mode.

## Why are we doing this?

By default, older setups used a **Monolithic** daemon (`libvirtd`). This is like having one giant manager trying to do everyone's job at once—it handles storage, networks, and the VMs themselves. It works, but it unnecessarily hogs system memory.

We are switching to **Modular** daemons using **Systemd Socket Activation**. This is like having a team of specialists. Because we are only enabling their _sockets_ (the communication doorways), the actual daemons stay completely asleep. If you aren't touching the network, the network manager uses 0MB of RAM. It only wakes up the microsecond it receives a request, making your system incredibly efficient.

> [!INFO] Service Breakdown: What are we enabling?
> 
> Here is exactly what each specialist piece of the puzzle does:
> 
> - **`virtqemud`**: The core compute daemon (QEMU/KVM). Manages CPU/RAM allocation.
>     
> - **`virtnetworkd`**: Creates virtual networks (NAT/Bridging) for VM internet access.
>     
> - **`virtnodedevd`**: Handles physical hardware passthrough (PCIe/USB/GPU).
>     
> - **`virtstoraged`**: Manages the virtual hard drives (.qcow2) and storage pools.
>     
> - **`virtinterfaced`**: Manages physical host network interfaces.
>     
> - **`virtnwfilterd`**: Acts like a firewall, controlling network traffic rules.
>     
> - **`virtsecretd`**: Safely stores passwords and encryption keys needed by your VMs.
>     
> - **`virtproxyd`**: The translator. Routes commands from older tools that still look for the monolithic `libvirtd` socket.
>     
> - **`virtlogd`**: Extremely critical. Handles console logging. VMs will fail to start if they cannot write logs here.
>     
> - **`virtlockd`**: Prevents data corruption by locking virtual disks so two VMs don't write to the same file at once.
>     
> - **`virtlxcd` / `virtvboxd` / `virtchd`**: Alternative hypervisor support (LXC, VirtualBox, Cloud-Hypervisor) that sit entirely asleep until requested.
>     

## Step 1: Kill the Monolithic Daemon Securely

Before starting the modular specialists, we must completely eradicate the old manager and its listening sockets (including TCP/TLS remnants) to prevent port conflicts.

> [!NOTE] Expected Terminal Output
> 
> If you see a warning stating `The unit files have no installation config`, or `Unit is masked, ignoring`, this is completely normal and confirms the legacy daemon is effectively dead.

```
# Stop, disable, and mask the service and ALL legacy sockets
sudo systemctl stop libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl disable libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl mask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
```

## Step 2: Enable and Start the Modular Sockets

We need to enable the connection points (`.socket`) for every driver.

> [!WARNING] Critical Systemd Rule
> 
> Do **NOT** enable the `.service` units directly. If you enable the services, they will run 24/7. By only enabling the `.socket` units, systemd handles waking them up automatically when a client requests them.

Copy and paste these loops into your terminal. We do this in two batches because logging/locking daemons have a different socket structure than the main drivers.

**First, Enable all sockets to persist across reboots:**

```
# Enable the primary modular sockets (including alternative hypervisors)
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do \
  sudo systemctl enable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; \
done

# Enable the logging and locking sockets
for drv in log lock; do \
  sudo systemctl enable virt${drv}d.socket virt${drv}d-admin.socket; \
done
```

**Second, Start the sockets for the current session:**

```
# Start the primary modular sockets
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do \
  sudo systemctl start virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; \
done

# Start the logging and locking sockets
for drv in log lock; do \
  sudo systemctl start virt${drv}d.socket virt${drv}d-admin.socket; \
done
```

## Step 3: Enable Graceful VM Shutdowns (Data Safety)

To prevent your virtual machines from being brutally hard-killed (which corrupts data) when you shut down or reboot your host computer, you must enable the `libvirt-guests` service. This tells systemd to gracefully pause or shut down your VMs when the host turns off.

```
sudo systemctl enable --now libvirt-guests.service
```

## Step 4: Apply Changes

For systemd to clean up the IPC namespaces and transition to the modular architecture smoothly, reboot your computer.

```
systemctl reboot
```

## Appendix: How to Undo (Disable)

> [!WARNING] Reverting
> 
> If you ever need to revert to the old monolithic mode, stop and disable all modular sockets first.

**1. Stop the running sockets & services:**

```
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox log lock; do \
  sudo systemctl stop virt${drv}d.service virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket 2>/dev/null; \
done
```

**2. Disable them from starting on boot:**

```
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox log lock; do \
  sudo systemctl disable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket 2>/dev/null; \
done
```

**3. Unmask the legacy daemon:**

```
sudo systemctl unmask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
```