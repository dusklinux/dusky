#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Procedural raindrops trickling down the display with surface-tension meniscus
// refraction, trailing wet wake beads, and stationary condensation micro-drops.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float COLUMN_WIDTH_PX   = 92.0;  // Horizontal spacing of raindrop tracks in pixels
const float DROP_RADIUS_PX    = 14.5;  // Radius of primary trickling raindrops in pixels
const float FALL_SPEED        = 1.15;  // Downward trickle speed multiplier
const float REFRACTION_PX     = 18.0;  // Background refraction offset inside droplets in pixels
const float WAKE_BEAD_STRENGTH= 0.55;  // Visibility of trailing wake droplets behind falling drops
const float MICRO_MIST_DENSITY= 0.50;  // Density of small stationary condensation beads [0, 1]
const float SPECULAR_RIM      = 0.22;  // Top-light meniscus highlight intensity [0.0, 0.5]
const float LOOP_SECONDS      = 48.0;  // Closed-loop temporal cycle in seconds
const float EDGE_FADE_PX      = 48.0;  // Screen-border protection margin in pixels
// ============================================================================

const float TAU = 6.283185307179586;

float hash12(uvec2 p) {
    uint h = p.x * 0x9e3779b9u ^ p.y * 0x85ebca6bu;
    h ^= h >> 16u;
    h *= 0x7feb352du;
    h ^= h >> 15u;
    h *= 0x846ca68bu;
    h ^= h >> 16u;
    return float(h & 0x00ffffffu) * (1.0 / 16777216.0);
}

// Evaluates one parallax layer of trickling raindrop tracks + wake beads.
vec3 rainTrackLayer(vec2 pixel, float scale, float phase, uint layerSalt) {
    vec2 cellDim = vec2(COLUMN_WIDTH_PX * scale, COLUMN_WIDTH_PX * 3.2 * scale);
    float colIdx = floor(pixel.x / cellDim.x);
    uint colU = uint(max(colIdx + 4096.0, 0.0)) + layerSalt * 313u;

    float colSeed = hash12(uvec2(colU, layerSalt));
    float speedCycles = (1.0 + floor(colSeed * 2.0)) * FALL_SPEED;

    // Staggered vertical scroll with subtle slip-stick hesitation.
    float rawT = phase * speedCycles + colSeed * TAU;
    float trickleY = (rawT + 0.28 * sin(rawT * 3.0)) / TAU * cellDim.y;

    vec2 gridPos = vec2(pixel.x, pixel.y - trickleY);
    ivec2 cell = ivec2(floor(gridPos / cellDim));
    vec2 local = (fract(gridPos / cellDim) - 0.5) * cellDim;

    float cellSeed = hash12(uvec2(uint(cell.x + 2048), uint(cell.y + 2048) + layerSalt * 97u));

    // Serpentine horizontal wiggle as the drop slides down the glass.
    float wiggle = sin(pixel.y / (55.0 * scale) + cellSeed * TAU) * (cellDim.x * 0.22);
    vec2 dropPos = vec2(wiggle, (cellSeed - 0.5) * cellDim.y * 0.25);

    vec2 delta = local - dropPos;
    // Teardrop elongation: slightly narrower above the drop center, fuller at the bottom meniscus.
    vec2 shapedDelta = vec2(delta.x, delta.y * (delta.y < 0.0 ? 0.72 : 1.12));
    float dropR = DROP_RADIUS_PX * scale * mix(0.75, 1.15, cellSeed);
    float dist = length(shapedDelta);

    vec2 normal = vec2(0.0);
    float spec = 0.0;

    if (dist < dropR) {
        float normR = dist / dropR;
        float dome = 1.0 - smoothstep(0.0, 1.0, normR);
        normal = -(delta / max(dropR, 1.0)) * dome;
        // Bright meniscus highlight near the bottom-left of the drop.
        float rim = smoothstep(0.55, 0.95, normR) * (1.0 - smoothstep(0.95, 1.0, normR));
        float lightAlign = clamp((-delta.x * 0.5 + delta.y * 0.85) / max(dist, 1.0), 0.0, 1.0);
        spec = rim * lightAlign;
    }

    // Wake trail of smaller stationary beads above the descending drop (negative delta.y).
    if (delta.y < 0.0 && delta.y > -cellDim.y * 0.55) {
        float trailFade = 1.0 - smoothstep(0.0, cellDim.y * 0.55, -delta.y);
        float beadSpacing = 22.0 * scale;
        float beadY = (fract(pixel.y / beadSpacing) - 0.5) * beadSpacing;
        vec2 beadDelta = vec2(delta.x, beadY);
        float beadR = dropR * 0.36 * trailFade;
        float beadDist = length(beadDelta);
        if (beadDist < beadR && beadR > 1.0) {
            float beadDome = (1.0 - beadDist / beadR) * WAKE_BEAD_STRENGTH * trailFade;
            normal += -(beadDelta / beadR) * beadDome;
        }
    }

    return vec3(normal, spec);
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 pixel = v_texcoord * size;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);

    // Combine two parallax trickling rain layers.
    vec3 layerA = rainTrackLayer(pixel, 1.00, phase, 1u);
    vec3 layerB = rainTrackLayer(pixel + vec2(43.0, 71.0), 0.72, phase, 2u);

    vec2 normal = layerA.xy + layerB.xy * 0.75;
    float spec = layerA.z + layerB.z * 0.75;

    // Layer 3: Fine stationary condensation micro-beads.
    if (MICRO_MIST_DENSITY > 0.01) {
        float mistCellPx = 34.0;
        ivec2 mistCell = ivec2(floor(pixel / mistCellPx));
        float mistSeed = hash12(uvec2(mistCell + ivec2(4096)));
        if (mistSeed < MICRO_MIST_DENSITY) {
            vec2 mistLocal = (fract(pixel / mistCellPx) - 0.5) * mistCellPx;
            vec2 mistCenter = (vec2(mistSeed, fract(mistSeed * 13.7)) - 0.5) * (mistCellPx * 0.45);
            vec2 mistDelta = mistLocal - mistCenter;
            float mistR = mix(2.5, 5.5, fract(mistSeed * 7.3));
            float mistDist = length(mistDelta);
            if (mistDist < mistR) {
                float mistDome = 1.0 - smoothstep(0.0, mistR, mistDist);
                normal += -(mistDelta / mistR) * (mistDome * 0.45);
                spec += smoothstep(0.6, 0.95, mistDist / mistR) * 0.25;
            }
        }
    }

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 uv = clamp(v_texcoord + (normal * REFRACTION_PX * edgeMask) / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    vec3 rgb = color.rgb + vec3(0.90, 0.96, 1.00) * (spec * SPECULAR_RIM * color.a * edgeMask);
    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(color.a)), color.a);
}
