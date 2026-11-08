#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// "Komorebi" (dappled sunlight filtering through swaying overhead tree leaves).
// Projects two parallax layers of soft wind-swayed foliage shadows and warm
// diagonal sunbeams across the desktop while keeping text completely readable.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float CANOPY_SCALE_PX    = 260.0; // Size of overhead leaf clusters in source pixels
const float SHADOW_DEPTH       = 0.22;  // Darkness of dappled foliage shade [0.0, 0.50]
const float SUNBEAM_STRENGTH   = 0.14;  // Warmth/brightness of sunlit canopy openings [0.0, 0.35]
const float SUN_ANGLE_RAD      = 0.55;  // Diagonal angle of incoming sunbeams in radians
const float LEAF_SOFTNESS      = 0.42;  // Penumbra blur of leaf edges [0.1 = crisp, 0.6 = soft]
const float BREEZE_SWAY_AMP    = 0.38;  // Wind sway amplitude across the canopy
const float SHIMMER_PX         = 0.8;   // Subtle thermal refraction at sunlit leaf edges (0.0 disables)
const float LOOP_SECONDS       = 32.0;  // Closed-loop wind cycle duration in seconds
const vec3  SUNLIGHT_TINT      = vec3(1.00, 0.93, 0.76); // Golden-hour sunbeam tint
const vec3  WOODLAND_SHADE_RGB = vec3(0.86, 0.94, 0.90); // Cool forest ambient shadow tone
// ============================================================================

const float TAU = 6.283185307179586;

// Analytic domain-warped foliage silhouette field (zero texture lookups).
float foliageCanopy(vec2 p, float phase) {
    vec2 sway = vec2(
        sin(p.y * 0.7 + phase) + 0.45 * cos(p.x * 1.3 - phase * 2.0),
        cos(p.x * 0.8 + phase) + 0.45 * sin(p.y * 1.4 + phase * 2.0)
    ) * BREEZE_SWAY_AMP;

    vec2 q = p + sway;

    // Intersecting harmonic lobes form organic leaf clusters and branches.
    float branch = 0.5 + 0.5 * sin(q.x * 1.15 + sin(q.y * 0.85)) * cos(q.y * 1.05 - cos(q.x * 0.75));
    float leaves = 0.5 + 0.5 * (
        0.55 * sin(q.x * 2.7 + q.y * 1.9 + phase * 3.0) * cos(q.x * 1.8 - q.y * 2.9 - phase * 2.0) +
        0.45 * cos(q.x * 4.1 - q.y * 3.3 + phase * 4.0)
    );

    return clamp(0.55 * branch + 0.45 * leaves, 0.0, 1.0);
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);

    vec2 p = v_texcoord * size / CANOPY_SCALE_PX;

    // Two parallax canopy layers moving at slightly different wind speeds.
    float upperCanopy = foliageCanopy(p, phase);
    float lowerCanopy = foliageCanopy(p * 1.55 + vec2(3.7, 1.9), phase * 2.0 + 1.2);

    float combinedDensity = 0.58 * upperCanopy + 0.42 * lowerCanopy;
    float gap = 1.0 - smoothstep(0.48 - LEAF_SOFTNESS * 0.5, 0.48 + LEAF_SOFTNESS * 0.5, combinedDensity);
    float shade = 1.0 - gap;

    // Diagonal crepuscular sunbeams aligned with SUN_ANGLE_RAD.
    vec2 sunDir = vec2(cos(SUN_ANGLE_RAD), sin(SUN_ANGLE_RAD));
    vec2 rayOrtho = vec2(-sunDir.y, sunDir.x);
    float rayCoord = dot(p, rayOrtho);
    float rayBands = 0.5 + 0.5 * (
        0.65 * sin(rayCoord * 3.2 + phase) +
        0.35 * cos(rayCoord * 6.7 - phase * 2.0)
    );
    float sunbeam = gap * rayBands * (1.15 - 0.35 * v_texcoord.y);

    // Optional gentle leaf-edge shimmer.
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 48.0, min(edge.x, edge.y));
    vec2 shimmerOffset = vec2(
        cos(combinedDensity * TAU + phase * 3.0),
        sin(combinedDensity * TAU - phase * 3.0)
    ) * (SHIMMER_PX * gap * (1.0 - gap) * 4.0 * edgeMask);

    vec2 uv = clamp(v_texcoord + shimmerOffset / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    // Apply cool forest shadow in canopy shade and warm golden glow in sunbeams.
    vec3 shaded = color.rgb * mix(vec3(1.0), WOODLAND_SHADE_RGB * (1.0 - SHADOW_DEPTH), shade);
    vec3 lit = shaded + (SUNLIGHT_TINT * color.a) * (sunbeam * SUNBEAM_STRENGTH);

    fragColor = vec4(clamp(lit, vec3(0.0), vec3(color.a)), color.a);
}
