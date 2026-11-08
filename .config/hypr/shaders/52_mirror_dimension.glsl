#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Full-screen mirrored desktop tiles rotate, breathe, and bend into each other.
// Reflection at tile boundaries is deliberate; this effect transforms the edges.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 32.0;
const float TILE_DENSITY = 2.1;
const float BEND = 0.22;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 p = (v_texcoord - 0.5) * size / min(size.x, size.y);
    float angle = 0.55 * sin(phase);
    float c = cos(angle);
    float s = sin(angle);
    p = vec2(c * p.x - s * p.y, s * p.x + c * p.y);
    p *= TILE_DENSITY * (1.0 + 0.25 * sin(phase * 2.0));
    vec2 bend = vec2(sin(p.y * 2.5 + phase * 3.0),
        cos(p.x * 2.2 - phase * 2.0)) * BEND;
    p += bend + vec2(sin(phase), cos(phase)) * 0.4 + 0.5;
    vec2 uv = 1.0 - abs(mod(p, 2.0) - 1.0);
    fragColor = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
}
