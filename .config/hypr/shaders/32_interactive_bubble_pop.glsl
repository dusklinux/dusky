#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform float pointer_last_active;
uniform int pointer_hidden;
uniform vec2 pointer_pressed_positions[32];
uniform float pointer_pressed_times[32];
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Buoyant iridescent soap bubbles rise across the screen, deflect away from the
// pointer, and pop into expanding micro-droplet rings when clicked or poked.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float CELL_SIZE_PX        = 240.0; // Grid spacing per bubble cell in source pixels
const float BUBBLE_RADIUS_PX    = 52.0;  // Base radius of each floating bubble in pixels
const float RISE_SPEED_PX       = 36.0;  // Upward buoyancy speed in pixels per second
const float SWAY_AMP_PX         = 22.0;  // Horizontal oscillation amplitude in pixels
const float DEFLECT_RADIUS_PX   = 185.0; // Cursor proximity radius that pushes bubbles away
const float DEFLECT_FORCE_PX    = 46.0;  // Maximum deflection distance away from cursor
const float TOUCH_POP_RADIUS_PX = 42.0;  // Direct cursor-hit distance that pops a bubble
const float CLICK_POP_RADIUS_PX = 160.0; // Click shockwave radius that pops nearby bubbles
const float POP_DURATION_SEC    = 0.85;  // Duration of the expanding droplet pop animation
const float REFRACTION_STRENGTH = 0.28;  // Spherical lens distortion inside intact bubbles
const float IRIDESCENCE_GLOW    = 0.22;  // Soap-film rainbow rim intensity [0.0, 0.6]
const float EDGE_FADE_PX        = 56.0;  // Screen-border protection margin in pixels
// ============================================================================

const float TAU = 6.283185307179586;

vec2 cellHash2(ivec2 cell, uint layer) {
    uvec2 p = uvec2(cell) + uvec2(layer * 73u, layer * 151u);
    uint hx = p.x * 0x9e3779b9u ^ p.y * 0x85ebca6bu;
    uint hy = p.y * 0xc2b2ae35u ^ p.x * 0x27d4eb2fu;
    hx ^= hx >> 16u; hx *= 0x7feb352du; hx ^= hx >> 15u;
    hy ^= hy >> 16u; hy *= 0x846ca68bu; hy ^= hy >> 15u;
    return vec2(float(hx & 0x00ffffffu), float(hy & 0x00ffffffu)) * (1.0 / 16777216.0);
}

void evaluateBubbleLayer(
    uint layer,
    vec2 pixel,
    vec2 pointerPx,
    bool pointerValid,
    float cursorActivity,
    inout vec2 totalOffsetPx,
    inout vec3 totalFilmAdd,
    inout float totalShade
) {
    float scale = layer == 0u ? 1.0 : 0.76;
    float cellSize = CELL_SIZE_PX * scale;
    float speed = RISE_SPEED_PX * (layer == 0u ? 1.0 : 1.25);

    // Upward scrolling coordinate space with layer offset.
    vec2 layerOffset = layer == 0u ? vec2(0.0) : vec2(cellSize * 0.5, cellSize * 0.37);
    vec2 scrollPos = pixel + layerOffset + vec2(0.0, time * speed);

    ivec2 cell = ivec2(floor(scrollPos / cellSize));
    vec2 localPx = fract(scrollPos / cellSize) * cellSize;
    vec2 seed = cellHash2(cell, layer);

    float phase = mod(time, 24.0) * (TAU / 24.0) + seed.x * TAU;
    float radius = BUBBLE_RADIUS_PX * scale * mix(0.72, 1.12, seed.y);

    // Keep base center safely inside the cell so bubbles never clip at cell borders.
    vec2 baseCenterLocal = vec2(
        cellSize * 0.5 + sin(phase * 2.0 + seed.y * TAU) * SWAY_AMP_PX * scale,
        cellSize * (0.28 + 0.44 * seed.x) + cos(phase + seed.x * TAU) * 12.0 * scale
    );
    vec2 bubbleScreenPx = (vec2(cell) * cellSize + baseCenterLocal)
        - layerOffset - vec2(0.0, time * speed);

    // 1. Check click-triggered pop state across the 6 most recent clicks.
    float popProgress = 0.0;
    for (int i = 0; i < 6; ++i) {
        float age = pointer_pressed_times[i];
        if (age >= POP_DURATION_SEC) {
            break;
        }
        vec2 clickOrigin = pointer_pressed_positions[i];
        if (all(greaterThanEqual(clickOrigin, vec2(0.0)))
            && all(lessThanEqual(clickOrigin, vec2(1.0)))) {
            float clickDist = length(bubbleScreenPx - clickOrigin * vec2(textureSize(tex, 0)));
            if (clickDist < CLICK_POP_RADIUS_PX) {
                popProgress = max(popProgress, clamp(age / POP_DURATION_SEC, 0.01, 1.0));
            }
        }
    }

    // 2. Cursor proximity deflection & direct-poke pop.
    vec2 deflect = vec2(0.0);
    if (pointerValid) {
        vec2 fromPointer = bubbleScreenPx - pointerPx;
        float ptrDist = length(fromPointer);
        if (ptrDist < TOUCH_POP_RADIUS_PX && cursorActivity > 0.15) {
            float pokePhase = fract(time * 1.5 + seed.x);
            popProgress = max(popProgress, clamp(pokePhase, 0.05, 1.0));
        } else if (ptrDist < DEFLECT_RADIUS_PX) {
            float push = 1.0 - smoothstep(0.0, DEFLECT_RADIUS_PX, ptrDist);
            vec2 pushDir = fromPointer / max(ptrDist, 1.0);
            deflect = pushDir * (DEFLECT_FORCE_PX * scale * push * push);
        }
    }

    vec2 delta = localPx - (baseCenterLocal + deflect);
    float dist = length(delta);

    // 3A. Render popping burst (expanding ring of micro-droplets + shock ripple).
    if (popProgress > 0.0) {
        float burstRadius = radius * (1.0 + 0.65 * popProgress);
        float ringWidth = 14.0 * scale;
        float ringMask = (1.0 - smoothstep(0.0, ringWidth, abs(dist - burstRadius)))
            * (1.0 - popProgress);
        if (ringMask > 0.001) {
            float angle = dist > 0.001 ? atan(delta.y, delta.x) : 0.0;
            float droplets = smoothstep(0.2, 0.85, 0.5 + 0.5 * cos(angle * 18.0 + seed.x * TAU));
            vec2 burstDir = delta / max(dist, 1.0);
            totalOffsetPx += burstDir * (12.0 * ringMask);
            totalFilmAdd += vec3(0.85, 0.95, 1.0) * (ringMask * droplets * 0.45);
        }
        return;
    }

    // 3B. Render intact floating bubble (spherical refraction + thin-film iridescence).
    if (dist >= radius) {
        return;
    }

    float normR = dist / radius;
    float edgeFeather = 1.0 - smoothstep(0.92, 1.0, normR);
    float sphereZ = sqrt(max(0.0, 1.0 - normR * normR));

    // Subtle surface wobble.
    float angle = dist > 0.001 ? atan(delta.y, delta.x) : 0.0;
    float wobble = 1.0 + 0.04 * sin(angle * 3.0 + phase * 4.0);

    // Spherical refraction pulls toward bubble center, stärker near the curved rim.
    vec2 refractOffset = -delta * REFRACTION_STRENGTH * (1.0 - sphereZ * 0.65) * wobble * edgeFeather;
    totalOffsetPx += refractOffset;

    // Thin-film interference rainbow on the outer shell + top-left specular crescent.
    float rimWeight = smoothstep(0.45, 0.95, normR) * edgeFeather;
    vec3 iridescence = 0.5 + 0.5 * cos(
        TAU * (normR * 1.8 + angle / TAU + seed.y + phase * 0.5) + vec3(0.0, 0.33, 0.67)
    );

    vec2 specPos = delta / radius - vec2(-0.38, -0.38);
    float specSpot = (1.0 - smoothstep(0.0, 0.26, length(specPos))) * edgeFeather;

    totalFilmAdd += (iridescence * (rimWeight * IRIDESCENCE_GLOW) + vec3(specSpot * 0.38));
    totalShade += smoothstep(0.85, 0.98, normR) * 0.08 * edgeFeather;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 pixel = v_texcoord * size;

    bool pointerValid = (pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    vec2 pointerPx = pointer_position * size;
    float cursorActivity = 1.0 - smoothstep(0.0, 0.6, pointer_last_active);

    vec2 totalOffsetPx = vec2(0.0);
    vec3 totalFilmAdd = vec3(0.0);
    float totalShade = 0.0;

    evaluateBubbleLayer(0u, pixel, pointerPx, pointerValid, cursorActivity, totalOffsetPx, totalFilmAdd, totalShade);
    evaluateBubbleLayer(1u, pixel, pointerPx, pointerValid, cursorActivity, totalOffsetPx, totalFilmAdd, totalShade);

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));

    vec2 uv = clamp(v_texcoord + (totalOffsetPx * edgeMask) / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    vec3 rgb = color.rgb * (1.0 - totalShade * edgeMask) + totalFilmAdd * (color.a * edgeMask);
    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(color.a)), color.a);
}
