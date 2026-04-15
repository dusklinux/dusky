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

### Core Sorting & Toggles
- **`Left` / `Right` Arrows:** Select the previous/next sorting column. This is how you switch between sorting by **MemB**, **Cpu%**, or **cpu lazy**. 
  > *Note: "cpu direct" updates instantly and jumps around. "cpu lazy" averages the CPU usage slightly over time so the list stays stable and readable.*
- **`r` (Reverse):** Reverse the sorting order in the processes box (High-to-Low vs Low-to-High).
- **`c` (Per-Core):** Toggles per-core CPU usage math. If your multi-threaded Python script is maxing out a 14-core CPU, standard math might show `1400%`. Pressing `c` scales the calculation so the entire system caps at `100%`.
- **`%`:** Toggles memory display mode in the processes box (Percent vs Bytes).
- **`F`:** Pause the process list entirely (freezes the UI to inspect a highly volatile list).

### The Sniper Rifle: Filtering (`f` / `/`)
Press **`f`** or **`/`** to enter a process filter. Type `python` or `waybar` and hit `Enter` to instantly isolate those specific processes. 
- **Pro-tip:** Start your filter string with `!` to use regex.
- **`Delete`:** Clears any entered filter.

### The Corporate Hierarchy: Tree View (`e`)
> [!info] Understanding Tree View
> In normal mode, processes are sorted purely by metric (e.g., who is using the most CPU). Pressing **`e`** toggles **Tree View**, grouping child processes under their parent. If your main script spawns a dozen worker threads, Tree View lets you see the exact execution hierarchy and trace a runaway thread right back to the parent process.
- **`Spacebar` / `+` / `-`:** Expand or collapse the selected process in Tree View.
- **`u`:** Expand/collapse the selected process's children.

### Interrogation & Execution
Once you have highlighted a suspect process using `Up/Down` (or `j/k`):
- **`Enter` (Detailed Info):** Opens a massive, dedicated sub-dashboard for *only* that process. It reveals its exact Disk Read/Write speeds, specific network connections, open handles, and a dedicated memory graph.
- **`N` (Nice Value):** Select a new nice value for the process (change its priority).
- **`s` (Signal):** Select or enter a specific Linux signal to send to the process.
- **`t` (Terminate):** Sends `SIGTERM - 15`. Politely asks the application to save its data, clean up its memory, and shut down gracefully.
- **`k` (Kill):** Sends `SIGKILL - 9`. The Linux kernel instantly destroys the process without letting it clean up. Use this for completely frozen applications.

---

## 💽 Memory & Network Diagnostics

You can manipulate the MEM and NET boxes to extract exactly the diagnostic data you need.

### The Memory Box (MEM)
- **`d` (Toggle Disks):** Hides the disks view inside the MEM box, giving your RAM and Swap graphs more screen real-estate.
- **`i` (IO Mode):** Toggles disks IO mode. This replaces the standard disk usage bars with massive, dedicated graphs showing real-time disk Read/Write speeds. Essential for diagnosing storage bottlenecks.

### The Network Box (NET)
- **`b` / `n`:** Select the previous or next network device (e.g., switch from `wlan0` to `eth0` or `tailscale0`).
- **`y` (Sync Scaling):** Toggles synced scaling mode. Forces the Upload and Download graphs to share the exact same Y-axis scale, giving you a true visual representation of inbound vs. outbound traffic symmetry.
- **`a` (Auto Scaling):** Toggles auto-scaling for the network graphs.
- **`z` (Zero Totals):** Resets the total transfer counters for the current network device. Extremely useful right before you trigger a script to measure exactly how much data it pushes/pulls.
