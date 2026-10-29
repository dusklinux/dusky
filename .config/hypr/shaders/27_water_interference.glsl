#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Two moving ripple sources interfere like overlapping pools of water.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 24.0;
const float WAVELENGTH_PX = 125.0;
const float AMPLITUDE_PX = 2.5; // Per source.
const float EDGE_FADE_PX = 64.0;

vec2 ripple(vec2 delta, float phase) {
    float radius = length(delta);
    float envelope = smoothstep(0.0, 24.0, radius) / (1.0 + radius / 650.0);
    return delta / max(radius, 1.0) * sin(TAU * radius / WAVELENGTH_PX - phase)
        * envelope * AMPLITUDE_PX;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 first = vec2(0.32 + 0.10 * cos(phase), 0.42 + 0.15 * sin(phase));
    vec2 second = vec2(0.68 + 0.12 * sin(phase), 0.58 + 0.13 * cos(phase * 2.0));
    vec2 offset = ripple((v_texcoord - first) * size, phase * 9.0)
        + ripple((v_texcoord - second) * size, phase * 11.0 + 1.7);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + offset * edgeMask / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
