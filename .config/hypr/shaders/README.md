# Screen shaders

Open the usual `shader_menu.sh` menu. Files `20`–`39` are the new collection;
the original `01`–`19` effects are unchanged. Up/Down previews, Enter accepts,
and Escape restores the shader and rendering settings from before the preview.

| File | Effect | Input | Texture samples per pixel |
| --- | --- | --- | --- |
| `20_cursor_ripple.glsl` | Local water ripples that settle after movement stops | Pointer + movement age + time | 1 |
| `21_cursor_lens.glsl` | Clear glass magnifier, about 1.4× at its center | Pointer | 1 |
| `22_cursor_vortex.glsl` | Breathing whirlpool; movement strengthens the twist | Pointer + movement age + time | 1 |
| `23_cursor_prism.glsl` | Magnifying bubble with rotating color dispersion | Pointer + time | 1 outside, 3 inside |
| `24_cursor_jelly.glsl` | Soft cross-wave wobble that settles when the pointer rests | Pointer + movement age + time | 1 |
| `25_click_shockwaves.glsl` | Expanding wave packets from the six most recent clicks/touches | Native press history | 1 |
| `26_roaming_glass.glsl` | Two broad glass bubbles wandering on different orbits | Time | 1 |
| `27_water_interference.glsl` | Overlapping ripples from two moving sources | Time | 1 |
| `28_heat_haze.glsl` | Fine rising shimmer with a slow atmospheric envelope | Time | 1 |
| `29_liquid_marble.glsl` | Smooth swirling eddies from an analytic curl field | Time | 1 |
| `30_prismatic_tide.glsl` | Broad ocean swells combined with color dispersion | Time | 3 |
| `31_cursor_kaleidoscope.glsl` | A rotating portal of six mirrored sectors | Pointer + time | 1 outside, 2 inside |
| `32_cursor_black_hole.glsl` | Strong gravitational twist, dark core, and a spectral rim | Pointer + time | 1 outside, 3 inside |
| `33_wormhole.glsl` | The desktop folds into a spiraling tunnel with moving walls | Time | 1 |
| `34_fractured_glass.glsl` | Triangular shards slide independently along fine cracks | Time | 1 |
| `35_glitch_storm.glsl` | Bursts of displaced scan bands and separated RGB channels | Time, intentionally discrete 12 Hz ticks | 3 |
| `36_cursor_reality_tear.glsl` | A bending rift pulls opposite sides of the desktop apart | Pointer + time | 1 |
| `37_digital_melt.glsl` | Columns of pixels liquefy, sink, and recover independently | Time | 1 |
| `38_orbital_spheres.glsl` | Three orbiting glass spheres carry inverted desktop reflections | Time | 1 outside, 2 inside |
| `39_mirror_dimension.glsl` | Full-screen mirrored desktop tiles rotate and bend together | Time | 1 |

For gentle effects, start with `20`, `21`, `26`, or `28`. For stronger distortion,
try `22`, `23`, `29`, or `31`. The constants near the top of each file control
strength, radius, wavelength, and timing. Distances ending in `_PX` use source
texture pixels rather than logical desktop coordinates. Screen shape and
texture size are discovered at render time.

For the extreme collection, start with `32` (black hole), `33` (wormhole), `35`
(glitch storm), `36` (reality tear), or `39` (mirror dimension). These intentionally
make large areas of the desktop distorted or unreadable. `34` and `37` introduce
sharp boundaries between independently moving shards or columns. `39` replaces
the entire screen with reflected tiles, including the edges. There is no feedback
buffer: repeated images always come from the current desktop frame.

The new effects use GLSL ES **3.20** and Hyprland's native screen-shader uniforms;
they require an OpenGL ES 3.2 renderer. They were verified against installed
Hyprland **0.57.0**, commit `0aa6745f705f2c4b0a5004eb019ef76e5bbdb426`, and Mesa
26.2.6 on Intel Iris Xe. The extreme collection was also rendered on the installed
NVIDIA GeForce RTX 3050 Ti. Confirm these interfaces and GPU support in the final
ISO build rather than assuming every companion package has the same version.
The renderer explicitly selects its ES 3.20 vertex shader when the file starts
with `#version 320 es`.

Native `pointer_position` and `pointer_pressed_positions` are normalized,
monitor-local coordinates with a top-left origin. Hyprland supplies monitor
offset/scale conversion. Cursor-centered effects disappear when the pointer is
hidden or outside that monitor. Click waves remain at their original positions
until they expire; press ages are independent of the shader's `time`. The
native history holds 32 entries; `25` uses its six newest entries. These are
current-frame deformations, with no persistent cursor trail or simulation buffer.

Every new shader carries `// dusky: animated`, including those driven only by
the pointer or press history. The existing menu consequently sets
`debug:damage_tracking = 0` and `debug:vfr = false` while applying them, as it
already does for ripple and soft warp. This keeps the entire output updating
at the monitor's refresh rate. No polling process or plugin is required.

Sampling stays within half-texel texture bounds. Most warps fade at display edges;
the mirror dimension intentionally reflects across the whole display. Glitch
storm and digital melt mirror displaced coordinates before bounded sampling.
RGBA moves together, and dispersive effects recombine channels using the
center sample's premultiplied alpha. The new shaders do not clip RGB to SDR.
Smooth time-based effects use closed loops with integer temporal harmonics,
avoiding reset seams. Glitch storm deliberately changes discontinuously at its
12 Hz tick boundaries, including its loop boundary. Host float timestamp
precision can still limit motion after very
long uninterrupted runs; applying a shader resets its native `time` origin.

Validation included glslang compilation and GPU linking/rendering of all 39
shaders with Hyprland's vertex shader. New effects were exercised at landscape,
portrait, ultrawide, and small resolutions, plus a transparent fixture, with
checks for finite output, premultiplied alpha, cursor boundaries/visibility,
movement settling, expired/overlapping clicks, and matching loop endpoints.
Physical rotated outputs and other GPU drivers require their own validation.
All eight extreme effects also passed live application/readback through the
existing menu's apply function; the prior shader and redraw settings were restored.
In offscreen 1080p RGBA32F tests, `39` was the heaviest effect on both GPUs.
Those timings include CPU submission and GPU work, and do not measure compositor
frame rate or the cost with its usual framebuffer formats.

Native interface references:
[screen-shader loading and uniform uploads](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/render/OpenGL.cpp),
[uniform names](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/render/Shader.cpp),
[press history length](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/macros.hpp).
