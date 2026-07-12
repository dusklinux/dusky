The "Absolute Zero" RAM Profile (Theoretical Maximum Starvation)

Target: Arch Linux (Kernel 7.x)
Status: Educational / Experimental Only
Warning: Applying these values will cause severe micro-stutters, high CPU usage, and SSD wear. Do not use in production.

Below is the theoretical limit of how far we can push the kernel to drop every single byte of RAM, which scripts control them, and what the catastrophic performance penalties would be.

1. The Kernel Slab & Cache Obliterator (Script 210: ZRAM Optimizer)

To get the absolute lowest RAM, we have to force the kernel to suffer "amnesia." The kernel naturally caches files and directory structures in RAM (Slab) so your hard drive doesn't have to spin up. We can force it to delete this instantly.

vm.vfs_cache_pressure

Current Optimized: 50

Absolute Lowest RAM: 10000 (Maximum)

What it does: Controls how aggressively the kernel deletes directory and inode caches (Slab memory).

The Result: Reclaims ~50 MB of RAM.

The Penalty: Every time you open your file manager, type ls, or search for a file, your CPU will spike to 100% and your NVMe/SSD will be hit with a massive read request because the kernel forgot where your files are located.

vm.swappiness

Current Optimized: 150

Absolute Lowest RAM: 200 (Kernel Maximum)

What it does: Controls how aggressively the kernel shoves application memory into swap.

The Result: Forces the system to constantly compress almost everything in userspace into the ZRAM pool, saving maybe 50–100 MB of physical footprint.

The Penalty: Your CPU will be endlessly stuck in a loop of zstd compression and decompression. Clicking between two open windows will cause a physical delay as the CPU has to decompress the app before drawing it on screen.

vm.watermark_scale_factor

Current Optimized: 10

Absolute Lowest RAM: 1 (Kernel Minimum)

What it does: Dictates the "buffer zone" of free RAM kswapd tries to maintain.

The Result: Forces the system to run directly on the edge of an Out-Of-Memory panic before it bothers to clean up memory.

The Penalty: Network spikes or sudden disk writes will completely stall the system because there is zero atomic memory reserve left to handle bursts of data.

vm.dirty_ratio & vm.dirty_background_ratio

Current Optimized: Unset (Kernel dynamic defaults)

Absolute Lowest RAM: 1

What it does: Controls how much RAM is allowed to hold "dirty" data (files waiting to be saved to the disk).

The Result: Saves a few megabytes of RAM.

The Penalty: Your SSD will be hammered with thousands of tiny, continuous writes instead of bundling them together in RAM first. This will severely degrade your SSD lifespan.

2. The THP Eradication (Script 212: THP Optimizer)

To get the lowest RAM footprint, we must absolutely forbid the kernel from trying to speed up the CPU with HugePages.

enabled

Current Optimized: always

Absolute Lowest RAM: never

What it does: Completely disables Transparent HugePages.

The Result: Recovers roughly 80–120 MB of RAM that was previously wasted on "empty padding" inside 2MB memory blocks.

The Penalty: Your RAM shatters into millions of tiny 4KB chunks. Your CPU's Translation Lookaside Buffer (TLB) will instantly overflow, causing your CPU cache hit-rate to plummet. Games will drop frames, and scrolling in web browsers will stutter heavily.

shmem_enabled

Current Optimized: within_size

Absolute Lowest RAM: never

What it does: Prevents Wayland (Hyprland) from using HugePages for pixel buffers.

The Result: Saves a few MBs of shared memory.

The Penalty: The desktop compositor will have to work much harder to draw windows, increasing GPU/CPU rendering latency.

3. The CPU Shield Removal (Script 210: ZRAM Optimizer / MGLRU)

lru_gen/min_ttl_ms

Current Optimized: 100

Absolute Lowest RAM: 0

What it does: The MGLRU Thrash Protection limit.

The Result: Does not inherently lower idle RAM, but under heavy load, it allows the kernel to instantly swap out memory you used just a millisecond ago.

The Penalty: "Thrashing." The system will literally lock up for seconds at a time if you open too many Chrome tabs because it is simultaneously compressing and decompressing the exact same memory blocks over and over.

4. Userspace Purge (System Level)

If you wanted to go beyond the scripts and attack the OS itself:

Delete the Polkit Agent: Uninstall hyprpolkitagent (Saves ~27 MB). You will no longer be able to run GUI apps as root or format drives via the GUI.

Nuke Python Daemons: Disable osd_lock.py (Saves ~36 MB). Your Caps Lock notification will disappear.

Black Screen: Kill awww-daemon (Saves ~15 MB). You will stare at a pure black void instead of a wallpaper.

Theoretical Result

If you applied every single one of these destructive parameters, your MemAvailable would likely surge to ~7.0 GiB (leaving only ~500-600 MB used).

You would have successfully created a mathematically tiny memory footprint—on a system that is completely miserable to actually use!
