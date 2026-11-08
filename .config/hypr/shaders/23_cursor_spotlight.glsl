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
// Soft ambient desktop dimmer with a feathered spotlight following the pointer.
// Automatically restores full desktop brightness when the pointer is hidden or off-monitor.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float SPOTLIGHT_RADIUS_PX = 240.0; // Clear inner radius of the spotlight in source pixels
const float FEATHER_WIDTH_PX    = 150.0; // Soft falloff width from full brightness to ambient dimness
const float AMBIENT_DIMNESS     = 0.68;  // Darkness outside the spotlight [0.0 = none, 0.90 = very dark]
const float IDLE_SHRINK_PX      = 35.0;  // Slight radius contraction when cursor rests (0.0 disables)
const float IDLE_SETTLE_SECONDS = 2.5;   // Seconds until idle contraction reaches full effect
const float RIM_GLOW_STRENGTH   = 0.10;  // Warm halogen accent ring along the beam edge [0.0, 0.5]
const vec3  RIM_GLOW_COLOR      = vec3(1.00, 0.88, 0.65); // Warm beam rim tint
const float SPOTLIGHT_ZOOM      = 1.00;  // Optional magnification inside beam (1.00 = no distortion)
// ============================================================================

const float TAU = 6.283185307179586;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    // Disappear cleanly when cursor is hidden or on another monitor.
    if (pointer_hidden != 0
        || any(lessThan(pointer_position, vec2(0.0)))
        || any(greaterThan(pointer_position, vec2(1.0)))) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    vec2 delta = (v_texcoord - pointer_position) * size;
    float radius = length(delta);

    float activity = 1.0 - smoothstep(0.0, IDLE_SETTLE_SECONDS, pointer_last_active);
    float phase = mod(time, 8.0) * (TAU / 8.0);
    float breathe = 4.0 * sin(phase);
    float activeRadius = SPOTLIGHT_RADIUS_PX - IDLE_SHRINK_PX * (1.0 - activity) + breathe;
    float outerEdge = activeRadius + FEATHER_WIDTH_PX;

    // Fast path outside the spotlight feather zone.
    if (radius >= outerEdge) {
        vec4 outside = textureLod(tex, baseUV, 0.0);
        outside.rgb *= (1.0 - AMBIENT_DIMNESS);
        fragColor = outside;
        return;
    }

    vec2 uv = baseUV;
    if (SPOTLIGHT_ZOOM > 1.001 && radius < activeRadius) {
        float lensWeight = 1.0 - smoothstep(0.0, activeRadius, radius);
        vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
        float edgeMask = smoothstep(0.0, 48.0, min(edge.x, edge.y));
        vec2 offset = -delta * (1.0 - 1.0 / SPOTLIGHT_ZOOM) * lensWeight * lensWeight * edgeMask;
        uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    }

    vec4 color = textureLod(tex, uv, 0.0);
    float beam = 1.0 - smoothstep(activeRadius, outerEdge, radius);
    float brightness = mix(1.0 - AMBIENT_DIMNESS, 1.0, beam);

    // Subtle warm optical rim around the transition zone.
    float rimDist = abs(radius - activeRadius) / max(FEATHER_WIDTH_PX * 0.5, 1.0);
    float rim = (1.0 - smoothstep(0.0, 1.0, rimDist)) * RIM_GLOW_STRENGTH;

    vec3 rgb = color.rgb * brightness + RIM_GLOW_COLOR * (rim * color.a);
    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(color.a)), color.a);
}
