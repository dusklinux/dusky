# 🌐 KVM Networking: Bridging & LAN Access

This guide dictates how to properly attach your Virtual Machine (VM) to a network. Your choice depends entirely on whether the VM just needs generic internet access, or if it needs a dedicated IP address on your physical home network (LAN).

> [!abstract] Prerequisite: Identify Your Hardware
> 
> Before changing any network settings, you must know the true name of your physical network cards.
> 
> Open your terminal and run the NetworkManager device check:
> 
> ```
> nmcli device status
> ```
> 
> Look for the device that says **connected**.
> 
> - **Ethernet** cards usually look like: `enp3s0`, `eno1`, or `enx...`
>     
> - **Wi-Fi** cards usually look like: `wlan0`, `wlp2s0`, or `wlo1`
>     
> 
> Write down the name of your active connection.

## 🚀 Mandatory Performance Check: Use Virtio

Regardless of which network option you choose below, you **must** use the `virtio` driver. It bypasses heavy hardware emulation and talks directly to the kernel, providing near-native network throughput.

1. Open **Virtual Machine Manager** (`virt-manager`).
    
2. Open your VM and click **Show virtual hardware details** (the lightbulb icon 💡).
    
3. Select **NIC** (Network Interface) on the left panel.
    
4. Set **Device model** to: `virtio`.
    
5. Click **Apply**.
    

## Option 1: Basic Internet & Host Access (NAT)

**Use Case:** The VM needs internet access, and you want to SSH into it from your host machine. You do _not_ need other devices (like your phone or a laptop in the living room) to communicate directly with the VM.

This is the cleanest, most secure, and most common setup.

1. Go to your VM's **NIC** settings.
    
2. Set **Network source** to: `Virtual network 'default' : NAT`.
    
3. Click **Apply**.
    

> [!error] Network not showing up?
> 
> If the `default` network is missing, inactive, or fails to start, your KVM default network is broken. Follow the pristine repair steps in [[Activating Network and Setting it to Autostart]].

## Option 2: Full LAN Access (Wi-Fi Host)

**Use Case:** Your host computer is connected to the internet via **Wi-Fi**, and you want the VM to get its own IP address directly from your home router.

> [!danger] Wi-Fi Bridging Limitations
> 
> The IEEE 802.11 Wi-Fi standard strictly prohibits multiple MAC addresses from communicating over a single wireless client connection. You _cannot_ create a standard system bridge on a Wi-Fi card.
> 
> **The Workaround:** We use a `macvtap` device.
> 
> **The Catch:** Due to kernel security preventing routing loops (hairpin mode), your Host PC and the VM will **not** be able to talk to each other. However, the VM _will_ be able to talk to the internet and any other device on your home network.

1. Go to your VM's **NIC** settings.
    
2. Set **Network** source to: `Macvtap device`.
    
3. In **Device name**, type your physical Wi-Fi card name (e.g., `wlan0`).
    
4. Set **Source mode** to: `Bridge`.
    
5. Click **Apply**.
    

## Option 3: Full LAN Access (Ethernet Host)

**Use Case:** Your host computer is connected via **Ethernet**, and you want the VM to get its own IP address directly from your home router. Both the Host and the VM will be able to communicate flawlessly.

This requires creating a **System Bridge** (`br0`). We will use `nmcli` (NetworkManager), which is the standard on modern Arch desktop environments.

### Step 1: Create the Bridge (Host Terminal)

Replace `enp3s0` in the commands below with your actual Ethernet interface name from the Prerequisite step.

```
# 1. Create a virtual bridge interface named 'br0' (and disable STP for faster handshakes)
sudo nmcli con add type bridge ifname br0 con-name br0 bridge.stp no

# 2. Bind your physical ethernet card to the bridge
# ⚠️ REPLACE 'enp3s0' WITH YOUR ACTUAL ETHERNET NAME!
sudo nmcli con add type ethernet ifname enp3s0 master br0 con-name bridge-slave-enp3s0

# 3. Bring up the bridge (Your internet will drop for about 2-5 seconds, then return)
sudo nmcli con up br0
```

### Step 2: Configure UFW for the Bridge

Because you use UFW, the Arch kernel might filter traffic passing through the bridge, preventing your VMs from getting an IP address. Explicitly allow the bridge traffic:

```
sudo ufw route allow in on br0
sudo ufw route allow out on br0
sudo ufw reload
```

### Step 3: Attach the VM

1. Open **Virtual Machine Manager**.
    
2. Go to the VM's **NIC** settings.
    
3. Set **Network source** to: `Bridge device`.
    
4. In **Device name**, type: `br0`.
    
5. Click **Apply**.
    

When you start the VM, it will bypass the host's NAT and request a standard home LAN IP (e.g., `192.168.1.50`) directly from your physical router.

### 🌟 Advanced: Add Bridge to Virt-Manager Dropdown

_(Optional: Do this AFTER completing Option 3)_

If you don't want to type `br0` manually every time you make a VM, you can tell libvirt to track the bridge so it appears in your network dropdown list.

```
# 1. Inject a clean XML wrapper into a temporary file
cat <<EOF > /tmp/host-bridge.xml
<network>
  <name>host-bridge</name>
  <forward mode='bridge'/>
  <bridge name='br0'/>
</network>
EOF

# 2. Define and start the wrapper in libvirt
sudo virsh net-define /tmp/host-bridge.xml
sudo virsh net-start host-bridge
sudo virsh net-autostart host-bridge

# 3. Clean up
rm /tmp/host-bridge.xml
```

If you run `sudo virsh net-list --all`, you will now see `host-bridge`. You can now select this from the "Network source" dropdown in Virt-Manager!

## 🆘 Disaster Recovery: Reverting Option 3

If you messed up the Ethernet bridge creation and lost internet on your Host PC, do not panic. Run this block to instantly destroy the bridge and restore your physical connection to normal:

```
# 1. Bring down the broken bridge
sudo nmcli connection down br0

# 2. Delete the bridge definition entirely
sudo nmcli connection del br0

# 3. Delete the slave definition
# (This unbinds your physical ethernet card from the destroyed bridge)
sudo nmcli connection del bridge-slave-enp3s0 

# 4. Restart NetworkManager to auto-detect your standard wired connection again
sudo systemctl restart NetworkManager
```