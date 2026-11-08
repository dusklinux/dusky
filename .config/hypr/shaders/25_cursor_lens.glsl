#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform int pointer_hidden;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Clear glass magnifier: smoothly neutral outside RADIUS_PX.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float RADIUS_PX        = 220.0; // Radius of the magnifying glass in source pixels
const float MAGNIFICATION    = 1.40;  // Zoom factor at lens center (> 1.0 magnifies)
const float LENS_CURVE_POWER = 2.0;   // Falloff exponent (2.0 = spherical, 3.0 = flatter center)
const float EDGE_FADE_PX     = 64.0;  // Screen-border protection margin in pixels
// ============================================================================

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    if (pointer_hidden != 0
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
    float profile = pow(weight, LENS_CURVE_POWER);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 offset = -delta * (1.0 - 1.0 / max(MAGNIFICATION, 0.01)) * profile * edgeMask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
