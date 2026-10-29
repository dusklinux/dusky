#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Two broad glass bubbles wander on different closed orbits.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 48.0;
const float RADIUS_FRACTION = 0.30; // Fraction of the shorter screen dimension.
const float LENS_STRENGTH = 0.18;
const float EDGE_FADE_PX = 80.0;

vec2 lensOffset(vec2 delta, float radius) {
    float weight = 1.0 - smoothstep(0.0, radius, length(delta));
    return -delta * LENS_STRENGTH * weight * weight;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 first = vec2(0.5 + 0.28 * cos(phase), 0.5 + 0.24 * sin(phase * 2.0));
    vec2 second = vec2(0.5 + 0.30 * sin(phase * 2.0 + 1.8),
        0.5 + 0.25 * cos(phase * 3.0 + 0.7));
    float radius = min(size.x, size.y) * RADIUS_FRACTION;
    vec2 offset = lensOffset((v_texcoord - first) * size, radius)
        + lensOffset((v_texcoord - second) * size, radius * 0.8);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + offset * edgeMask / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
