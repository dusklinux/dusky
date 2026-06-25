# KVM & Virt-Manager Setup Guide

This guide outlines the step-by-step process for setting up a Kernel-based Virtual Machine (KVM) using `virt-manager` on Arch Linux. These steps are designed to be followed in order.

> [!INFO] Goal
> 
> By the end of this guide, you will have a fully functional virtualization environment capable of running Windows or Linux guests with high performance.

## 1. Prerequisites & Verification

Before installing anything, we must ensure the hardware supports virtualization.

- [ ] [[Verify VT-x and Kernel Modules and IOMMU]]
    

## 2. Kernel Modules

Load the necessary modules to allow the Linux kernel to act as a hypervisor.

- [ ] [[KVM Loading Kernel Module]]
    

### Optional: Performance Tweaks

> [!TIP] Temporary Storage Optimization
> 
> Only use this if you need a temporary, high-speed storage solution in RAM.

- [ ] [[Symbolic link to zram for image file]]
    

## 3. Service Configuration (Daemon Setup)

You must choose **ONE** method for managing the virtualization services. Do not do both.

> [!QUESTION] Which one should I choose?
> 
> - **Modular Daemon:** (Recommended for most modern setups but we're gonna use the monolith option instead) Runs specific services only when needed. Saves resources.
>     
> - **Monolithic Daemon:** The classic way. Runs one giant service (`libvirtd`) that handles everything. Easier to troubleshoot for legacy tutorials.
>     

- [ ] **Option A (Recommended):** [[libvert Modular daemon enable]]
    
- [ ] **Option B (Classic):** [[KVM Services]] 

## 4. System Optimization

> [!WARNING] Conflict Warning
> 
> Do NOT use the TuneD method if you are already using TLP for power management (common on laptops). They will conflict and cause system instability.

- [ ] [[Optimize the Host with TuneD]] ( _Skip if using TLP_ )
    

## 5. Network Configuration

Ensure your virtual machines can connect to the internet.

- [ ] [[Activating Network and Setting it to Autostart]]
    

### 6.  Networking

> [!example] Web Hosting / Bridging
> 
> Follow this (OPTION 3 Within the note) only if you need your VM to appear as a separate physical device on your router (useful for web hosting or LAN gaming).

- [ ] [[Network Bridging for LAN access]] 
    

## 6. Permissions & Access

Allow your standard user account to manage virtual machines without needing `sudo` for every command.

- [ ] [[Give the User System-Wide Permission]]
    

### Optional: Storage Permissions

> [!TIP] Custom Directory Access
> 
> Only required if you are storing your VM disk images in a non-standard directory (outside of /var/lib/libvirt/images).

- [ ] [[Set ACL on the Image Directory]]