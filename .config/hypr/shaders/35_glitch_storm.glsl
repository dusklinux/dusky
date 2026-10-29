#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Bursts of displaced scan bands, block corruption, and separated RGB channels.
// Discrete 12 Hz changes are intentional digital glitches.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 16.0;
const float MAX_SHIFT_FRACTION = 0.18;
const float DISPERSION_PX = 12.0;

float bandHash(uint band, uint tick) {
    uint h = band * 0x9e3779b9u ^ tick * 0x85ebca6bu;
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
    float seconds = mod(time, LOOP_SECONDS);
    float phase = seconds * (TAU / LOOP_SECONDS);
    uint tick = uint(floor(seconds * 12.0));
    uint band = uint(floor(v_texcoord.y * size.y / 22.0));
    float seed = bandHash(band, tick);
    float gate = smoothstep(0.48, 0.78, seed);
    float swell = 0.20 + 0.80 * pow(0.5 + 0.5 * sin(phase * 3.0), 4.0);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 64.0, min(edge.x, edge.y));
    float shift = (seed * 2.0 - 1.0) * gate * swell * edgeMask;
    vec2 uv = v_texcoord + vec2(shift * MAX_SHIFT_FRACTION, 0.0);
    // Mirror wrapping makes displaced fragments reappear instead of smearing edges.
    uv.x = 1.0 - abs(mod(uv.x, 2.0) - 1.0);
    vec2 split = vec2(DISPERSION_PX * gate * swell * edgeMask / size.x, 0.0);
    vec4 center = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 red = textureLod(tex, clamp(uv + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blue = textureLod(tex, clamp(uv - split, halfTexel, 1.0 - halfTexel), 0.0);
    fragColor = vec4(red.a > 0.0 ? red.r * (center.a / red.a) : 0.0,
        center.g, blue.a > 0.0 ? blue.b * (center.a / blue.a) : 0.0, center.a);
}
