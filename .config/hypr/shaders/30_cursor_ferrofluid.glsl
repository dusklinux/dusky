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
// A glossy magnetic ferrofluid lens around the pointer whose perimeter bristles
// with counter-rotating Rosensweig spikes when moving and relaxes at rest.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float BASE_RADIUS_PX    = 175.0; // Base radius of the ferrofluid pool in source pixels
const float SPIKE_COUNT       = 14.0;  // Number of primary magnetic lobes around the perimeter
const float SPIKE_AMP_PX      = 36.0;  // Peak radial spike extension in source pixels
const float IDLE_SPIKE_RATIO  = 0.28;  // Fraction of spike height retained when pointer rests [0, 1]
const float SETTLE_SECONDS    = 1.6;   // Seconds for movement-excited spikes to settle
const float MAGNETIC_ZOOM     = 1.32;  // Refraction magnification inside the fluid dome
const float RIM_SHEEN         = 0.26;  // Glossy metallic highlight along the liquid meniscus [0, 1]
const float RIM_SHADE         = 0.22;  // Dark magnetic ink edge contrast [0, 1]
const float LOOP_SECONDS      = 16.0;  // Closed-loop temporal period in seconds
const float EDGE_FADE_PX      = 64.0;  // Screen-border protection margin in pixels
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
    float maxBound = BASE_RADIUS_PX + SPIKE_AMP_PX + 24.0;

    // Early-out outside maximum spike reach.
    if (radius >= maxBound) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    float activity = 1.0 - smoothstep(0.0, SETTLE_SECONDS, pointer_last_active);
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float angle = radius > 0.001 ? atan(delta.y, delta.x) : 0.0;

    // Counter-rotating harmonic lobes create realistic ferrofluid crown spikes.
    float waveA = cos(SPIKE_COUNT * angle + phase * 3.0);
    float waveB = sin((SPIKE_COUNT * 0.5) * angle - phase * 5.0);
    float spikeProfile = pow(0.5 + 0.5 * (0.65 * waveA + 0.35 * waveB), 1.6);

    float amp = SPIKE_AMP_PX * mix(IDLE_SPIKE_RATIO, 1.0, activity);
    float boundaryRadius = BASE_RADIUS_PX + amp * (spikeProfile * 2.0 - 0.6);

    float feather = 14.0;
    float inside = 1.0 - smoothstep(boundaryRadius - feather, boundaryRadius + feather, radius);
    if (inside <= 0.001) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    float normR = clamp(radius / max(boundaryRadius, 1.0), 0.0, 1.0);

    // Smooth dome refraction + slight tangential pull toward spike ridges.
    float dome = 1.0 - normR * normR;
    vec2 tangent = vec2(-delta.y, delta.x) / max(radius, 1.0);
    float ridgePull = -sin(SPIKE_COUNT * angle + phase * 3.0) * 6.0 * normR * (1.0 - normR);
    vec2 offset = (-delta * (1.0 - 1.0 / max(MAGNETIC_ZOOM, 0.1)) * dome + tangent * ridgePull)
        * inside * edgeMask;

    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    // Meniscus shading & metallic specular rim.
    float rimZone = smoothstep(0.68, 0.96, normR) * inside * edgeMask;
    float lightDir = 0.5 + 0.5 * cos(angle + 2.2 + 0.4 * waveA);
    float specular = pow(lightDir, 4.0) * RIM_SHEEN * rimZone;
    float darkEdge = smoothstep(0.82, 1.0, normR) * RIM_SHADE * inside * edgeMask;

    vec3 rgb = color.rgb * (1.0 - darkEdge) + vec3(0.78, 0.88, 1.0) * (specular * color.a);
    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(color.a)), color.a);
}
