#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Broad ocean swells plus chromatic dispersion at their moving crests.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 24.0;
const float AMPLITUDE_PX = 5.0;
const float DISPERSION_PX = 2.0;
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 p = v_texcoord * size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float swell = p.x / 210.0 + p.y / 320.0 - phase * 4.0;
    float crossWave = p.y / 140.0 - p.x / 370.0 + phase * 5.0;
    vec2 wave = vec2(sin(swell) + 0.35 * cos(crossWave),
        0.65 * cos(swell) + 0.30 * sin(crossWave));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = v_texcoord + wave * AMPLITUDE_PX * edgeMask / size;
    vec2 split = vec2(cos(swell), sin(crossWave)) * DISPERSION_PX * edgeMask / size;
    vec4 center = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 red = textureLod(tex, clamp(uv + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blue = textureLod(tex, clamp(uv - split, halfTexel, 1.0 - halfTexel), 0.0);
    fragColor = vec4(red.a > 0.0 ? red.r * (center.a / red.a) : 0.0,
        center.g, blue.a > 0.0 ? blue.b * (center.a / blue.a) : 0.0, center.a);
}
