#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Tall columns of desktop pixels liquefy, sink, and recover at different phases.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 20.0;
const float COLUMN_WIDTH_PX = 44.0;
const float MELT_FRACTION = 0.32;

float columnHash(uint column) {
    uint h = column * 0x9e3779b9u;
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
    float column = floor(pixel.x / COLUMN_WIDTH_PX);
    float seed = columnHash(uint(column));
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float drop = 0.5 + 0.5 * sin(phase * 2.0 + seed * TAU);
    drop *= drop * drop;
    float stripeCenter = min((column + 0.5) * COLUMN_WIDTH_PX, size.x - 0.5);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 64.0, min(edge.x, edge.y));
    vec2 uv = v_texcoord;
    uv.x += (stripeCenter / size.x - uv.x) * drop * 0.75 * edgeMask;
    uv.y -= drop * MELT_FRACTION * (0.35 + 0.65 * v_texcoord.y) * edgeMask;
    uv.y = 1.0 - abs(mod(uv.y, 2.0) - 1.0);
    vec4 color = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    color.rgb *= 1.0 - drop * 0.12 * edgeMask;
    fragColor = color;
}
