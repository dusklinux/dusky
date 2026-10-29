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
// A pool around the pointer; waves settle after you stop moving.
// Hyprland 0.57 pointer coordinates are monitor-local, normalized, top-left.
const float TAU = 6.283185307179586;
const float RADIUS_PX = 340.0;
const float WAVELENGTH_PX = 72.0;
const float AMPLITUDE_PX = 5.0;
const float SETTLE_SECONDS = 2.0;
const float EDGE_FADE_PX = 48.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float radius = length(delta);
    float visible = float(pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    float activity = 1.0 - smoothstep(0.0, SETTLE_SECONDS, pointer_last_active);
    float envelope = 1.0 - smoothstep(0.0, RADIUS_PX, radius);
    float centerMask = smoothstep(0.0, 12.0, radius);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    float phase = TAU * (radius / WAVELENGTH_PX - mod(time, 8.0) * 0.5);
    vec2 offset = delta / max(radius, 1.0) * sin(phase) * AMPLITUDE_PX
        * envelope * centerMask * activity * visible * edgeMask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
