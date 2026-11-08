#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
out vec4 fragColor;

// dusky: animated
// Reference-inspired concentric water ripples, reimplemented for Hyprland.
// Sampled RGBA travels together. No window-transition fade or black frames.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 8.0;
const float WAVELENGTH_PX = 140.0; // Must be positive.
const float AMPLITUDE_PX = 3.0;   // Nonnegative.
const float EDGE_FADE_PX = 48.0;  // Must be positive.

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 position = (v_texcoord - 0.5) * size;
    float radius = length(position);
    vec2 direction = position / max(radius, 1.0);

    float phase = TAU * (radius / WAVELENGTH_PX
        - mod(time, LOOP_SECONDS) / LOOP_SECONDS);
    vec2 edgeDistances = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX,
        min(edgeDistances.x, edgeDistances.y));
    float centerMask = smoothstep(0.0, WAVELENGTH_PX * 0.25, radius);
    vec2 uv = v_texcoord + direction * sin(phase)
        * AMPLITUDE_PX * edgeMask * centerMask / size;

    fragColor = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
}
