### 1. VFR (Variable Framerate) - The Battery Savior
**What it is:** VFR is a software-level, compositor optimization built directly into Hyprland. It dictates how many frames Hyprland *chooses to render and send* to your monitor. 

If nothing on your screen is moving (e.g., you are reading a static web page or staring at code), VFR tells Hyprland to temporarily drop the rendering framerate to the bare minimum. Instead of pointlessly drawing the same static image 144 times a second, it might drop down to 10 or 20 frames per second until you move your mouse or type a key.

*   **The Analogy:** Imagine your GPU is a courtroom sketch artist drawing the scene in front of them. 
    *   **VFR OFF:** A strict boss forces the artist to redraw the exact same unmoving courtroom 144 times a second. The artist is exhausted, sweating, and burning massive amounts of energy for no reason. 
    *   **VFR ON:** The artist is allowed to stop drawing when everyone is sitting still. The moment someone moves, the artist instantly resumes sketching rapidly. 

### 2. VRR (Variable Refresh Rate) - The Smoothness Enforcer
**What it is:** VRR (commonly known as FreeSync or G-Sync) is a hardware-level synchronization protocol. It changes the physical *refresh rate (Hz)* of your monitor on the fly to perfectly match the framerate (FPS) your GPU is currently outputting. 

Its primary purpose is to eliminate screen tearing and stuttering, especially in video games where your framerate fluctuates wildly based on how demanding the scene is. 

*   **The Analogy:** Imagine your monitor is a conveyor belt and your GPU is the factory worker placing boxes (frames) onto it.
    *   **VRR OFF:** The conveyor belt moves at a rigid, fixed speed (e.g., 144Hz). If the worker is too slow to put a box on the belt, the belt moves a half-empty box (resulting in screen tearing). 
    *   **VRR ON:** The conveyor belt politely waits for the worker. It only moves forward the exact millisecond the worker has fully placed the box on the belt. The delivery is perfectly smooth.

---

### Power Consumption: Which uses more power?

**VFR dictates your power draw.** 
*   **VFR OFF uses significantly MORE power.** Your GPU is forced to run at maximum capacity constantly, draining battery life on laptops and generating heat on desktops, even when you are just staring at your wallpaper.
*   **VFR ON uses drastically LESS power.** It is the ultimate optimization for Wayland compositors. By skipping redundant work, your GPU goes idle when the screen is static.

**VRR is mostly power-neutral, but highly situational.**
VRR does not inherently save or burn massive amounts of power. However, by allowing the monitor's physical Hz to drop when framerates drop, it can save a marginal amount of power on the monitor side. 

---

### The Hyprland Dilemma: Why you must understand how they interact

In recent Hyprland updates, you must configure these carefully because **they can directly conflict with each other and ruin your experience.**

If you enable **both** VFR and VRR globally, you will likely experience horrible screen flickering (especially on OLED or VA panels). Here is why:
1. You stop moving your mouse. VFR kicks in and drops your framerate to 48 FPS to save power.
2. Because VRR is active, it forces your monitor to physically drop to 48Hz.
3. You twitch your mouse. VFR instantly ramps up to 144 FPS.
4. VRR violently forces your monitor back to 144Hz.
5. This rapid fluctuation in physical refresh rate causes the monitor's gamma/brightness to shift, resulting in a strobe-like flicker every time you touch your mouse.

### The Optimal Hyprland Configuration (Latest Best Practices)

To get the absolute best performance, zero tearing in games, and maximum power efficiency, here is exactly how you should set them in your `~/.config/hypr/hyprland.conf`:

```text
misc {
    # VFR is heavily recommended to be left ON to conserve resources.
    vfr = true

    # 0 = off, 1 = on, 2 = fullscreen only
    vrr = 2 
}
```

**Why this is the perfect setup:**
*   `vfr = true`: You get massive power savings and lower GPU temperatures while on your desktop.
*   `vrr = 2`: Variable Refresh Rate will **only** activate when a window is in fullscreen mode (like when you boot up a game). This gives you the tear-free gaming benefits of FreeSync/G-Sync without the annoying desktop flickering caused by VFR.

This setup gives you the best of both worlds, perfectly leveraging the hardware sync for gaming while letting the compositor save battery while idle.

