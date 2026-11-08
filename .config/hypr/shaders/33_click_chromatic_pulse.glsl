#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_pressed_positions[32];
uniform float pointer_pressed_times[32];
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Every mouse click or touch launches a high-velocity RGB prism shock-ring
// that splits spectral channels along the wave crest and settles cleanly.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const int   MAX_PULSES       = 6;     // Number of recent clicks evaluated simultaneously
const float LIFETIME_SECONDS = 2.0;   // Duration of each expanding prism ring in seconds
const float RING_SPEED_PX    = 440.0; // Outward expansion velocity in source pixels/second
const float RING_WIDTH_PX    = 68.0;  // Radial thickness of the dispersive wave packet
const float WAVELENGTH_PX    = 90.0;  // Internal harmonic wave period in source pixels
const float WARP_AMP_PX      = 6.5;   // Geometric UV displacement at the wave crest
const float PRISM_SPLIT_PX   = 11.0;  // Radial RGB chromatic separation in source pixels
const float EDGE_FADE_PX     = 64.0;  // Screen-border protection margin in pixels
// ============================================================================

const float TAU = 6.283185307179586;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    vec2 totalWarpPx = vec2(0.0);
    vec2 totalSplitPx = vec2(0.0);

    for (int i = 0; i < MAX_PULSES; ++i) {
        float age = pointer_pressed_times[i];
        if (age >= LIFETIME_SECONDS) {
            break;
        }
        vec2 origin = pointer_pressed_positions[i];
        if (any(lessThan(origin, vec2(0.0))) || any(greaterThan(origin, vec2(1.0)))) {
            continue;
        }

        vec2 delta = (v_texcoord - origin) * size;
        float radius = length(delta);
        float front = radius - age * RING_SPEED_PX;

        if (abs(front) >= RING_WIDTH_PX) {
            continue;
        }

        float packet = 1.0 - smoothstep(0.0, RING_WIDTH_PX, abs(front));
        float birth = smoothstep(0.0, 0.08, age);
        float decay = 1.0 - smoothstep(0.15, LIFETIME_SECONDS, age);
        float centerMask = smoothstep(0.0, 12.0, radius);
        float envelope = packet * birth * decay * centerMask;

        vec2 dir = delta / max(radius, 1.0);
        float wave = sin(TAU * front / WAVELENGTH_PX);

        totalWarpPx += dir * (wave * WARP_AMP_PX * envelope);
        totalSplitPx += dir * (PRISM_SPLIT_PX * envelope * (0.6 + 0.4 * cos(TAU * front / WAVELENGTH_PX)));
    }

    // Fast 1-sample passthrough for all pixels outside active click rings.
    if (dot(totalWarpPx, totalWarpPx) + dot(totalSplitPx, totalSplitPx) <= 0.001) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    // Soft-clamp overlapping rings.
    totalWarpPx /= max(1.0, length(totalWarpPx) / (WARP_AMP_PX * 2.0));
    totalSplitPx /= max(1.0, length(totalSplitPx) / (PRISM_SPLIT_PX * 2.0));

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 uv = v_texcoord + (totalWarpPx * edgeMask) / size;
    vec2 split = (totalSplitPx * edgeMask) / size;

    vec4 center = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 red = textureLod(tex, clamp(uv + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blue = textureLod(tex, clamp(uv - split, halfTexel, 1.0 - halfTexel), 0.0);

    fragColor = vec4(
        red.a > 0.0 ? red.r * (center.a / red.a) : 0.0,
        center.g,
        blue.a > 0.0 ? blue.b * (center.a / blue.a) : 0.0,
        center.a
    );
}
