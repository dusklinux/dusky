#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
out vec4 fragColor;

// dusky: animated
// Inspired by the reference soft-warp-fade; independently implemented
// as a gentle looping screen effect with integer noise and stable edges.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 20.0;
const float NOISE_SCALE_PX = 240.0; // Must be positive.
const float AMPLITUDE_PX = 6.0;     // Nonnegative.
const float EDGE_FADE_PX = 64.0;    // Must be positive.

float cellHash(ivec2 cell) {
    uvec2 p = uvec2(cell);
    uint h = (p.x * 0x9e3779b9u) ^ (p.y * 0x85ebca6bu);
    h ^= h >> 16u;
    h *= 0x7feb352du;
    h ^= h >> 15u;
    h *= 0x846ca68bu;
    h ^= h >> 16u;
    return float(h & 0x00ffffffu) * (1.0 / 16777216.0);
}

float noise(vec2 position) {
    ivec2 cell = ivec2(floor(position));
    vec2 fraction = fract(position);
    // Quintic interpolation makes first and second derivatives continuous.
    vec2 w = fraction * fraction * fraction
        * (fraction * (fraction * 6.0 - 15.0) + 10.0);
    return mix(
        mix(cellHash(cell), cellHash(cell + ivec2(1, 0)), w.x),
        mix(cellHash(cell + ivec2(0, 1)), cellHash(cell + ivec2(1, 1)), w.x), w.y);
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 drift = vec2(cos(phase), sin(phase)) * 0.7;
    vec2 position = v_texcoord * size / NOISE_SCALE_PX;
    vec2 offset = vec2(noise(position + drift),
        noise(position + drift + vec2(17.0, 29.0))) * 2.0 - 1.0;

    vec2 edgeDistances = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX,
        min(edgeDistances.x, edgeDistances.y));
    vec2 uv = v_texcoord + offset * AMPLITUDE_PX * edgeMask / size;
    fragColor = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
}
