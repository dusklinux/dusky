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
// A local whirlpool that stirs harder when the pointer moves.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float RADIUS_PX        = 270.0; // Radius of the vortex in source pixels
const float TWIST_RADIANS    = 0.85;  // Peak rotation angle in radians at vortex core
const float IDLE_STRENGTH    = 0.45;  // Fraction of twist retained when pointer is stationary [0, 1]
const float BOOST_SETTLE_SEC = 1.5;   // Seconds for movement-induced spin boost to decay
const float BREATHE_PERIOD_S = 12.0;  // Seconds per full breathing oscillation cycle
const float EDGE_FADE_PX     = 80.0;  // Screen-border protection margin in pixels
// ============================================================================

const float TAU = 6.283185307179586;

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
    float activity = 1.0 - smoothstep(0.0, BOOST_SETTLE_SEC, pointer_last_active);
    float phase = mod(time, BREATHE_PERIOD_S) * (TAU / BREATHE_PERIOD_S);
    float angle = TWIST_RADIANS * weight * weight
        * mix(IDLE_STRENGTH, 1.0, activity) * sin(phase);

    float c = cos(angle);
    float s = sin(angle);
    vec2 rotated = vec2(c * delta.x - s * delta.y, s * delta.x + c * delta.y);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 uv = clamp(v_texcoord + (rotated - delta) * edgeMask / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
