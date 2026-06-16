# Libvirt Connection & Permissions (Modern Modular Architecture)

In this step, we will configure how your user connects to the virtualization hypervisor. By default, Linux tries to run VMs as a "Session" (User) process, but for advanced features like **PCI/GPU Passthrough**, we need to connect to the "System" (Root) process while maintaining regular user privileges for daily management.

## 1. Understanding Connection Modes

Libvirt provides two methods for connecting to the local QEMU/KVM hypervisor. It is critical to know the difference.

### ❌ The User Session (`qemu:///session`)

This is the default mode when running a virtual machine as a regular user.

- **Pros:** Completely rootless and isolated; zero system-level configuration required.
    
- **Cons:** Networking is restricted (bridging requires complex helpers), and **hardware/GPU** Passthrough is not **supported.**
    

### ✅ The System Instance (`qemu:///system`)

This connects to the hypervisor daemon (`virtqemud`) running as system/root.

- **Pros:** Complete access to host resources, advanced bridged networking, and mandatory for PCI/VGA/GPU Passthrough.
    
- **Cons:** Requires Polkit authorization to access the UNIX control socket as a regular user without typing `sudo`.
    

> [!ABSTRACT] Goal
> 
> We want to seamlessly connect to the System Instance (`qemu:///system`) as a Regular User via Polkit authorization to get the best of both worlds: tight security and maximum performance.

## 2. Polkit & Socket Permissions (The 2026 Standard)

Modern Libvirt operates on a strict client-server architecture using **modular daemons**. Your management tools (`virt-manager`, `virsh`) are the _clients_. They need to talk to the QEMU management daemon (`virtqemud`) via a UNIX socket.

Because the daemon runs as root—and securely handles all dangerous hardware passthrough, disk access, and KVM acceleration itself—your regular user **only needs Polkit permission to access the control socket.**

Run the following command to add your user to the `libvirt` group, which Polkit natively recognizes for authorization:

```
sudo usermod -aG libvirt "$(id -un)"
```

> [!WARNING] Security Notice: Legacy Groups
> 
> Do **NOT** add your user to the `disk`, `input`, or `kvm` groups as older guides suggest.
> 
> - `disk`: Allows your user account to raw-write over the host's root partition.
>     
> - `input`: Creates a vulnerability allowing global keylogging bypassing Wayland/X11.
>     
> - `kvm`: Access to `/dev/kvm` is automatically granted to the active seated user via modern `systemd` uaccess rules, and the `virtqemud` daemon handles KVM access for system VMs anyway.
>     

> [!IMPORTANT] Relogin Required
> 
> Group changes do not apply to the currently running session.
> 
> You must completely log out and log back in (or restart your computer) for the `libvirt` group to be applied.

## 3. Enable the Modular Daemons

Arch Linux utilizes modular daemons to keep the virtualization stack lightweight. Ensure the QEMU socket is enabled so your client can connect:

```
sudo systemctl enable --now virtqemud.socket
```

## 4. Configuring the Default URI

To make life easier, we tell the system that whenever we run a virtualization command in the terminal, we imply `qemu:///system` by default.

> [!TIP] Specific Configuration Note
> 
> If you are using the Dusk / UWSM configuration files, this environment variable is already set for you automatically in the UWSM env file. You can skip manually editing your RC files.

**For manual setup**, add this to your shell profile (`.zshrc` or `.bashrc`):

```
echo "export LIBVIRT_DEFAULT_URI='qemu:///system'" >> ~/.zshrc
source ~/.zshrc
```

## 5. Verifying the Connection

Once you have re-logged, verify that your user has Polkit authorization and is targeting the correct instance by default.

Run this command as your **regular user** (do NOT use sudo):

```
virsh uri
```

> [!SUCCESS] Expected Output
> 
> The terminal should respond with:
> 
> ```
> qemu:///system
> ```

### Troubleshooting

If the output says `qemu:///session` or you get a "Permission denied" error on the socket:

1. Verify you are actually in the group by typing `groups`. You must see `libvirt`.
    
2. Ensure the modern modular socket is active: `systemctl status virtqemud.socket`.
    
3. Force the connection manually to test socket permissions:
    

```
virt-manager --connect qemu:///system
```

_Avoid using `virt-manager --connect qemu:///session` unless you specifically intend to create a restricted, isolated, non-passthrough VM._