# Screen shaders

Open the usual `shader_menu.sh` menu. Files `20`–`31` are the new collection;
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

For gentle effects, start with `20`, `21`, `26`, or `28`. For stronger distortion,
try `22`, `23`, `29`, or `31`. The constants near the top of each file control
strength, radius, wavelength, and timing. Distances ending in `_PX` use source
texture pixels rather than logical desktop coordinates. Screen shape and
texture size are discovered at render time.

The new effects use GLSL ES **3.20** and Hyprland's native screen-shader uniforms;
they require an OpenGL ES 3.2 renderer. They were verified against installed
Hyprland **0.57.0**, commit `0aa6745f705f2c4b0a5004eb019ef76e5bbdb426`, and Mesa
26.2.6 on Intel Iris Xe. Confirm these interfaces and GPU support in the final
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

Sampling stays within half-texel texture bounds. Warps fade at display edges;
RGBA moves together, and dispersive effects recombine channels using the
center sample's premultiplied alpha. The new shaders do not clip RGB to SDR.
Time-based effects use closed loops with integer temporal harmonics, avoiding
reset seams. Host float timestamp precision can still limit motion after very
long uninterrupted runs; applying a shader resets its native `time` origin.

Validation included glslang compilation and GPU linking/rendering of all 31
shaders with Hyprland's vertex shader. New effects were exercised at landscape,
portrait, ultrawide, and small resolutions, plus a transparent fixture, with
checks for finite output, premultiplied alpha, cursor boundaries/visibility,
movement settling, expired/overlapping clicks, and matching loop endpoints.
Physical rotated outputs and other GPU drivers require their own validation.

Native interface references:
[screen-shader loading and uniform uploads](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/render/OpenGL.cpp),
[uniform names](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/render/Shader.cpp),
[press history length](https://github.com/hyprwm/Hyprland/blob/0aa6745f705f2c4b0a5004eb019ef76e5bbdb426/src/macros.hpp).
