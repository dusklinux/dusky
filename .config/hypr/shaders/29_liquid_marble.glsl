#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Curl of two analytic stream functions: smooth eddies without noise lookups.
// This deforms the current frame; it does not accumulate simulation state.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 32.0;
const float CELL_SCALE_PX = 150.0;
const float AMPLITUDE_PX = 9.0;
const float EDGE_FADE_PX = 72.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 p = v_texcoord * size / CELL_SCALE_PX;
    vec2 a = p + vec2(cos(phase), sin(phase * 2.0)) * 0.8;
    vec2 b = p * 1.7 + vec2(sin(phase * 3.0), cos(phase)) * 0.6;
    // (d psi / dy, -d psi / dx) for psi = sin(x) * sin(y).
    vec2 curl = vec2(sin(a.x) * cos(a.y), -cos(a.x) * sin(a.y))
        + 0.35 * vec2(sin(b.x) * cos(b.y), -cos(b.x) * sin(b.y));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + curl * AMPLITUDE_PX * edgeMask / size,
        halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
