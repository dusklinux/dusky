# Libvirt Connection & Permissions

In this step, we will configure how your user connects to the virtualization hypervisor. By default, Linux tries to run VMs as a "Session" (User) process, but for advanced features like **GPU Passthrough**, we need to connect to the "System" (Root) process while maintaining regular user privileges.

## 1. Understanding Connection Modes

Libvirt provides two methods for connecting to the local QEMU/KVM hypervisor. It is important to know the difference.

### ❌ The User Session (`qemu:///session`)

This is the default mode when running a virtual machine as a regular user.

- **Pros:** easy to set up.
    
- **Cons:** Networking is difficult to bridge, and **PCI/GPU Passthrough is not supported.**
    

### ✅ The System Instance (`qemu:///system`)

This connects to the hypervisor as the root user.

- **Pros:** Complete access to host resources, hardware acceleration, and necessary for GPU Passthrough.
    
- **Cons:** Requires group configuration to access as a regular user (which we will do below).
    

> [!ABSTRACT] Goal
> 
> We want to connect to the System Instance (qemu:///system) as a Regular User to get the best of both worlds: security and performance.

## 2. Group Permissions

To allow your regular user account to control the System Instance without typing `sudo` every time, we need to add your user to specific groups.

Run the following command in your terminal:

```
sudo usermod -aG libvirt,kvm,input,disk "$(id -un)"
```

### What do these groups do?

| **Group**     | **Description**                                                            |
| ------------- | -------------------------------------------------------------------------- |
| **`libvirt`** | Grants permission to manage system-level VMs.                              |
| **`kvm`**     | Grants access to the `/dev/kvm` hardware acceleration device.              |
| **`input`**   | _(Recommended)_ Allows input capture (keyboard/mouse) for advanced setups. |
| **`disk`**    | _(Optional)_ Helpful if managing raw disk images directly.                 |

> [!IMPORTANT] Reboot Required
> 
> Group changes do not apply to the currently running session.
> 
> You must log out and log back in (or restart your computer) for these changes to take effect.

## 3. Configuring the Default URI

To make life easier, we tell the system that whenever we run a virtualization command, we imply `qemu:///system` by default.

> [!TIP] specific Configuration Note
> 
> If you are using the Dusk / UWSM configuration files, this environment variable is already set for you automatically in the UWSM env file.
> 
> _You can skip manually editing your `.zshrc` or `.bashrc`._

**For reference only**, the command usually used to set this manually is:

```bash
# ONLY RUN THIS IF NOT USING DUSK CONFIGS
echo "export LIBVIRT_DEFAULT_URI='qemu:///system'" >> ~/.zshrc
source ~/.zshrc
```

## 4. Verifying the Connection

Once you have re-logged or restarted, verify that your user is targeting the correct instance by default.

Run this command as your **regular user** (do not use sudo):

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
## for reference only 
this should also show the root session
```bash
sudo virsh uri
```

### Troubleshooting

If the output says `qemu:///session`, your environment variable is not loaded. You can force the connection manually when opening the manager:

```bash
# Connect explicitly to the System instance (Recommended)
virt-manager --connect qemu:///system
```

## For reference only, to connect to the user session. 
```bash
virt-manager --connect qemu:///session
```

_Avoid using `virt-manager --connect qemu:///session` unless you specifically intend to create a restricted, non-passthrough VM._