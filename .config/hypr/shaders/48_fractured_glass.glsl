#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Triangular glass shards slide and refract independently along fine cracks.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 24.0;
const float SHARD_SIZE_PX = 180.0;
const float DISPLACEMENT_PX = 42.0;

float cellHash(ivec2 cell, uint triangle) {
    uvec2 p = uvec2(cell);
    uint h = p.x * 0x9e3779b9u ^ p.y * 0x85ebca6bu ^ triangle * 0xc2b2ae35u;
    h ^= h >> 16u;
    h *= 0x7feb352du;
    h ^= h >> 15u;
    h *= 0x846ca68bu;
    h ^= h >> 16u;
    return float(h & 0x00ffffffu) / 16777216.0;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 pixel = v_texcoord * size;
    vec2 grid = vec2(pixel.x + pixel.y * 0.35, pixel.y) / SHARD_SIZE_PX;
    vec2 local = fract(grid);
    uint triangle = local.x + local.y > 1.0 ? 1u : 0u;
    float seed = cellHash(ivec2(floor(grid)), triangle);
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float diagonal = abs(local.x + local.y - 1.0) * 0.70710678;
    float distanceToCrack = min(min(min(local.x, 1.0 - local.x),
        min(local.y, 1.0 - local.y)), diagonal);
    // Derivatives are measured before any divergent control flow.
    float aa = max(fwidth(distanceToCrack), 0.001);
    float crack = 1.0 - smoothstep(0.001, 0.001 + aa, distanceToCrack);
    vec2 direction = vec2(cos(seed * TAU), sin(seed * TAU));
    float motion = sin(phase * 2.0 + seed * TAU);
    vec2 facetCenter = vec2(triangle == 0u ? 1.0 / 3.0 : 2.0 / 3.0);
    vec2 offset = direction * motion * DISPLACEMENT_PX
        + (local - facetCenter) * (seed - 0.5) * 28.0;
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 72.0, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + offset * edgeMask / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);
    color.rgb *= 1.0 - edgeMask * (crack * 0.48 + (1.0 - seed) * 0.10);
    fragColor = color;
}
