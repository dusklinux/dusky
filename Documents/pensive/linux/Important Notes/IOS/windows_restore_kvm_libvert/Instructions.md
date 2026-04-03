# Virt-Manager: iPhone iTunes Restore Hot-Swap

**The Problem:** During an iTunes restore or update inside a virtual machine, the iPhone intentionally drops its USB connection and reconnects under different hardware states (Normal Mode → Recovery Mode → DFU Mode). Because the USB Product ID changes, libvirt treats it as a completely new device, while holding onto the dead connection. The VM loses the phone.

**The Fix: Manual GUI Hot-Swapping**
When the iPhone disconnects during the restore process and iTunes is waiting for it, manually cycle the hardware in the Virt-Manager GUI:

DO NOT TURN OFF THE VM, DO IT ALL WHILE THE VM IS ON AND THE RESTORRING IS CARRYIGN THROUGH
1. Go to the VM's hardware details window (Lightbulb icon).
2. **Clear the Stale Connection:** Locate the currently passed-through iPhone under the left hardware list (`USB Host Device` / `USB 05ac:...`). Select it and click **Remove**.
3. **Attach the New State:** Click **Add Hardware** at the bottom left.
4. Select **USB Host Device** from the sidebar.
5. Find the newly connected iPhone in the list (it will likely have a new bus/device number, e.g., `003:017 Apple, Inc. iPhone...`, and may explicitly say "Recovery").
6. Click **Finish** to inject it live into the VM.

*Note for future reference:* You will need to repeat this `Remove -> Add Hardware` cycle every single time the iPhone reboots and changes modes during the Apple restore pipeline.