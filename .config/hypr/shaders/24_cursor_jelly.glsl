#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform float pointer_last_active;
uniform int pointer_hidden;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// A soft cross-wave wobble while moving; settles to the original image.
// This is a local deformation, not a persistent cursor trail or fluid simulation.
const float TAU = 6.283185307179586;
const float RADIUS_PX = 300.0;
const float AMPLITUDE_PX = 11.0;
const float SETTLE_SECONDS = 1.2;
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float weight = 1.0 - smoothstep(0.0, RADIUS_PX, length(delta));
    float visible = float(pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    float activity = 1.0 - smoothstep(0.0, SETTLE_SECONDS, pointer_last_active);
    float phase = mod(time, 8.0) * (TAU / 8.0);
    vec2 wave = vec2(sin(delta.y / 42.0 - phase * 7.0),
        cos(delta.x / 53.0 + phase * 5.0));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 offset = wave * AMPLITUDE_PX * weight * weight * activity * visible * edgeMask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
