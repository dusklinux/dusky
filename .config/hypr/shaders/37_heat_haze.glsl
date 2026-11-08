#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Fine rising heat shimmer under a slowly wandering atmospheric envelope.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 24.0;
const float AMPLITUDE_PX = 3.5;
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 p = v_texcoord * size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float envelope = 0.55 + 0.45 * sin(p.x / 290.0 + sin(phase) + phase * 2.0);
    float rising = p.y / 48.0 + phase * 18.0;
    vec2 offset = vec2(sin(rising + 0.7 * sin(p.x / 160.0 + phase * 3.0))
        + 0.3 * sin(p.y / 23.0 + phase * 29.0),
        0.22 * sin(p.x / 105.0 + p.y / 170.0 - phase * 7.0));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + offset * AMPLITUDE_PX * envelope * edgeMask / size,
        halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
