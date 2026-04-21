# 🚀 The Ultimate btop++ Mastery Guide

> [!abstract] Overview
> `btop++` is a highly optimized, C++ system resource monitor. It provides total visibility into system execution, hardware states, and network traffic without the heavy overhead of GUI monitors. This guide breaks down the interface, the exact keyboard shortcuts, and the mental models required to use it for high-level system diagnostics in an Arch/Hyprland environment.

---

## 🏗️ The Domains (Layout & Display)

Your dashboard is highly modular. You can toggle specific domains on and off to focus exactly on what you are debugging.

- **`1` (CPU):** Core utilization, temperatures, frequency, and system load averages.
- **`2` (MEM):** Physical RAM, Swap space, and Disks.
- **`3` (NET):** Global network traffic.
- **`4` (PROC):** The process list. Your diagnostic hunting ground.
- **`5` (GPU):** Toggle GPU monitoring (if enabled/compiled). `0` toggles all GPUs.
- **`p` / `Shift + p`:** Cycle forwards and backwards through your view presets.

---

## ⌨️ Global Navigation & Vim Keys

> [!success] The Vim Advantage
> Because you are a Vim user, enable `vim_keys = true` in your `~/.config/btop/btop.conf`. This unlocks `h, j, k, l` for directional control, keeping your hands right on the home row where they belong. *(Note: Conflicting keys like `h` for help and `k` for kill are accessed by holding `Shift` when Vim keys are active).*

| Key | Action |
| :--- | :--- |
| **`Esc` / `m`** | Toggles the main menu |
| **`F2` / `o`** | Shows options / config menu |
| **`F1` / `?` / `h`** | Shows the help window |
| **`q` / `ctrl + c`** | Quits the program |
| **`ctrl + z`** | Sleep program and put in background |
| **`ctrl + r`** | Reloads config file from disk |
| **`+` / `-`** | Add / Subtract 100ms to/from the update timer |

---

## 🧠 Deep Dive: The Process Box (PROC)

The Process box is where you hunt down memory leaks, rogue scripts, and zombie processes. 

### Core Sorting & Capabilities
You can sort the process list by shifting the active column. Use the **`Left`** and **`Right`** arrow keys (or `<` / `>`) to cycle through the available metrics.

**Available Sorting Metrics:**
- `cpu lazy` (Recommended: averages CPU slightly for a stable, readable list)
- `cpu direct` (Raw, instant, highly volatile CPU reporting)
- `memory` (RAM usage)
- `pid`, `program`, `arguments`, `threads`, `user`

> [!warning] Architectural Limitation: Network & I/O Sorting
> `btop++` **cannot** sort the main process list by Network bandwidth or Disk I/O. It can show you global I/O and Network usage in their respective boxes, and it can show you per-process I/O if you press `Enter` on a specific script, but it cannot rank the list by them. Use `iotop` for disk I/O sorting and `nethogs` for network sorting.

### 🕸️ The Browser Problem: Aggregating Child Processes
Modern browsers (Firefox) and Electron apps spawn dozens of isolated child processes. By default, `btop` shows the memory of each individual sandboxed tab/extension. 

**To see the TOTAL Memory and CPU usage of the entire application:**
1. Press **`o`** (or `F2`) to open the Options menu.
2. Press **`6`** to navigate to the `[proc]` tab.
3. Scroll down to **`Proc aggregate`** and set it to **`True`**. (Alternatively, set `proc_aggregate = true` in your `btop.conf`).
4. Exit the menu (`Esc`).
5. Ensure you are in **Tree View** (press **`e`**). 

> *Result:* The parent `firefox` process will now display the accumulated, combined total of all its child processes' CPU and Memory. You can press `Spacebar` or `-` to collapse the tree and cleanly read the total footprint of the app.

### Process Toggles & Filters
- **`r` (Reverse):** Reverse the sorting order (High-to-Low vs Low-to-High).
- **`c` (Per-Core):** Toggles per-core CPU usage math. Scales multi-threaded CPU calculations so the entire system caps at `100%`.
- **`%`:** Toggles memory display mode in the processes box (Percent vs Bytes).
- **`F`:** Pause the process list entirely (freezes the UI to inspect a highly volatile list).
- **`f` / `/` (Filter):** The sniper rifle. Type `python` or `waybar` and hit `Enter` to isolate processes. Start with `!` to use regex. Press `Delete` to clear.

### The Corporate Hierarchy: Tree View (`e`)
> [!info] Understanding Tree View
> In normal mode, processes are sorted purely by metric. Pressing **`e`** toggles **Tree View**, grouping child processes under their parent. Combined with `Proc aggregate`, this is the most powerful way to read system usage.
- **`Spacebar` / `+` / `-`:** Expand or collapse the selected process in Tree View.
- **`u`:** Expand/collapse the selected process's children.

### Interrogation & Execution
Once you have highlighted a suspect process using `Up/Down` (or `j/k`):
- **`Enter` (Detailed Info):** Opens a massive, dedicated sub-dashboard for *only* that process. **This is where you see per-process I/O stats.**
- **`N` (Nice Value):** Select a new nice value for the process (change its CPU scheduling priority).
- **`s` (Signal):** Select or enter a specific Linux signal to send to the process.
- **`t` (Terminate):** Sends `SIGTERM - 15`. Politely asks the application to save data and shut down gracefully.
- **`k` (Kill):** Sends `SIGKILL - 9`. The kernel instantly destroys the process. Use this for frozen Wayland/Hyprland applications.

---

## 💽 Memory & Network Diagnostics

Manipulate the MEM and NET boxes to extract hardware-level diagnostic data.

### The Memory Box (MEM)
- **`d` (Toggle Disks):** Hides the disks view inside the MEM box, expanding your RAM and Swap graphs.
- **`i` (IO Mode):** Toggles disks IO mode. This replaces standard disk usage bars with massive, dedicated graphs showing real-time global disk Read/Write speeds. Essential for diagnosing storage bottlenecks.

### The Network Box (NET)
- **`b` / `n`:** Cycle previous or next network device (e.g., switch from `wlan0` to `eth0` or `tailscale0`).
- **`y` (Sync Scaling):** Forces the Upload and Download graphs to share the exact same Y-axis scale, giving you a true visual representation of inbound vs. outbound traffic symmetry.
- **`a` (Auto Scaling):** Toggles auto-scaling for the network graphs.
- **`z` (Zero Totals):** Resets the total transfer counters for the current network device. Extremely useful right before you trigger a script to measure exactly how much data it pushes/pulls.