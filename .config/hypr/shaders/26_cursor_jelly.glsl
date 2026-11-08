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

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float RADIUS_PX        = 300.0; // Radius of elastic wobble zone in source pixels
const float AMPLITUDE_PX     = 11.0;  // Maximum displacement in source pixels
const float WAVELENGTH_X_PX  = 42.0;  // Horizontal wave spacing in source pixels
const float WAVELENGTH_Y_PX  = 53.0;  // Vertical wave spacing in source pixels
const float SETTLE_SECONDS   = 1.2;   // Seconds until wobble settles completely after stop
const float LOOP_SECONDS     = 8.0;   // Temporal animation loop duration in seconds
const float EDGE_FADE_PX     = 64.0;  // Screen-border protection margin in pixels
// ============================================================================

const float TAU = 6.283185307179586;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    if (pointer_hidden != 0 || pointer_last_active >= SETTLE_SECONDS
        || any(lessThan(pointer_position, vec2(0.0)))
        || any(greaterThan(pointer_position, vec2(1.0)))) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    vec2 delta = (v_texcoord - pointer_position) * size;
    float radius = length(delta);
    if (radius >= RADIUS_PX) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    float weight = 1.0 - smoothstep(0.0, RADIUS_PX, radius);
    float activity = 1.0 - smoothstep(0.0, SETTLE_SECONDS, pointer_last_active);
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 wave = vec2(
        sin(delta.y / WAVELENGTH_X_PX - phase * 7.0),
        cos(delta.x / WAVELENGTH_Y_PX + phase * 5.0)
    );
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 offset = wave * AMPLITUDE_PX * weight * weight * activity * edgeMask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
