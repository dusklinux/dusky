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
// Calm, proportionate thick-glass spheres with NaN-free polynomial rail glides,
// phase-separated non-overlapping paths, gentle C2 cursor touch, and glass optics.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const int   SPHERE_COUNT             = 3;     // Default: 3 glass spheres [1 .. 6]
const float BASE_RADIUS_FRACTION     = 0.105; // Base sphere radius as fraction of shorter screen side
const float RADIUS_VARIATION         = 0.12;  // Subtle size variation across spheres [0.0 = uniform]
const float WALL_MARGIN_PX           = 16.0;  // Margin from screen borders in source pixels

// --- Calm Glide Dynamics & Long-Term Slowdown ---
const float BASE_SPEED               = 0.16;  // Calm, graceful initial cruising speed (screen spans / sec)
const float FRICTION_SLOWDOWN_SEC    = 480.0; // Characteristic time (8 mins) over which balls gently slow down
const float MIN_GLIDE_SPEED_RATIO    = 0.45;  // Peaceful steady-state drift ratio after slowdown [0.1 .. 1.0]
const float PATH_WANDER_AMPLITUDE    = 0.14;  // Bounded angle/speed variation so paths explore the full screen
const float BALL_SOFT_CUSHION        = 0.28;  // Gentle C2 separation cushion when spheres pass near each other

// --- Proportionate, Subtle Cursor & Click Touch ---
const float CURSOR_REACH_MULT        = 1.22;  // Only nudges when cursor touches/grazes the sphere rim
const float CURSOR_NUDGE_PX          = 32.0;  // Proportionate, gentle max nudge distance in pixels (no launching)
const float CURSOR_RIM_SQUISH        = 0.07;  // Subtle organic glass rim yield under direct cursor touch
const int   MAX_CLICK_IMPULSES       = 6;     // Number of recent clicks evaluated for gentle ripples
const float CLICK_REACH_PX           = 240.0; // Local reach of a click ripple in source pixels
const float CLICK_NUDGE_PX           = 36.0;  // Subtle, proportionate nudge from a click in source pixels
const float CLICK_DECAY_RATE         = 2.40;  // Smooth decay rate of click ripples

// --- Thick-Glass Optics & Shading ---
const bool  BALL_LENS_INVERT         = true;  // true = real thick-glass inverted refraction, false = upright magnifier
const float REFRACTION_STRENGTH      = 0.64;  // Optical bending strength inside the glass core
const float DISPERSION_PX            = 3.6;   // Internal RGB chromatic aberration near the sphere rim
const float SPECULAR_STRENGTH        = 0.78;  // Primary top-left key light highlight intensity
const float CAUSTIC_BOUNCE           = 0.30;  // Secondary bottom-right internal caustic glow
const float FRESNEL_RIM_STRENGTH     = 0.40;  // Colored crystal Fresnel rim intensity [0.0 .. 1.0]
const float SHADOW_OPACITY           = 0.22;  // Soft ambient contact shadow cast onto the desktop
const vec2  SHADOW_OFFSET_PX         = vec2(12.0, 16.0); // Contact shadow offset in source pixels
// ============================================================================

const float TAU = 6.283185307179586;
const int MAX_SPHERES = 6;

// Wendland C2 compactly supported radial basis function on [0, 1].
// Value, 1st derivative, and 2nd derivative all vanish smoothly at q = 1,
// and slope is zero at q = 0.
float wendlandC2(float q) {
    float t = clamp(1.0 - q, 0.0, 1.0);
    float t2 = t * t;
    return t2 * t2 * (4.0 * q + 1.0);
}

// NaN-free, inverse-trig-free C-infinity periodic rail wave in [0, 1] with period 2.0.
// Uses a range-reduced cosine and normalized 5th-order Taylor polynomial of asin(c):
//   q(c) = (120/149)*c + (20/149)*c^3 + (9/149)*c^5
// Cancels velocity variation up to O(c^6) across the interior of the table while
// rebounding smoothly at u = 0, 1, 2, ... and staying strictly within [0.0, 1.0].
vec2 polynomialRailWave(vec2 u) {
    vec2 c = cos(fract(u * 0.5) * TAU);
    vec2 c2 = c * c;
    vec2 q = c * (0.80536913 + c2 * (0.13422819 + c2 * 0.06040268));
    return clamp(0.5 - 0.5 * q, 0.0, 1.0);
}

// Algebraic C2 boundary cushion without tanh() or exp() overflow risks.
// Keeps nudged spheres strictly inside [minB, maxB] with zero hard clamping jerks.
vec2 softBoundaryCushion(vec2 p, vec2 minB, vec2 maxB, float cushionPx) {
    vec2 center = 0.5 * (minB + maxB);
    vec2 halfSpan = max(0.5 * (maxB - minB), vec2(1.0));
    vec2 u = clamp((p - center) / halfSpan, vec2(-2.0), vec2(2.0));
    float softZone = clamp(cushionPx / min(halfSpan.x, halfSpan.y), 0.04, 0.35);
    float thresh = 1.0 - softZone;

    vec2 absU = abs(u);
    vec2 excess = max(absU - thresh, vec2(0.0)) / softZone;
    vec2 sat = excess * inversesqrt(1.0 + excess * excess);
    vec2 compressed = min(absU, vec2(thresh)) + softZone * sat;
    vec2 signedComp = vec2(
        u.x >= 0.0 ? compressed.x : -compressed.x,
        u.y >= 0.0 ? compressed.y : -compressed.y
    );
    return clamp(center + signedComp * halfSpan, minB, maxB);
}

// Analytical integral of long-horizon velocity decay from t = 0 to t:
// v(t) = v_min + (1 - v_min) * exp(-t / tau)
// Clamped exponent argument prevents any floating-point underflow/overflow.
float integratedTableDistance(float t, float baseSpeed, float slowdownSec, float minRatio) {
    float safeT = max(t, 0.0);
    float tau = max(slowdownSec, 1.0);
    float alpha = clamp(minRatio, 0.05, 1.0);
    float expTerm = exp(-clamp(safeT / tau, 0.0, 80.0));
    return baseSpeed * (alpha * safeT + (1.0 - alpha) * tau * (1.0 - expTerm));
}

// Per-sphere crystal rim tint palette.
vec3 sphereGlassTint(int idx) {
    if (idx == 0) return vec3(0.28, 0.78, 0.98); // Ice Sapphire
    if (idx == 1) return vec3(0.20, 0.94, 0.62); // Emerald Quartz
    if (idx == 2) return vec3(1.00, 0.68, 0.22); // Amber Topaz
    if (idx == 3) return vec3(0.76, 0.36, 0.98); // Violet Amethyst
    if (idx == 4) return vec3(0.98, 0.34, 0.58); // Rose Tourmaline
    return vec3(0.45, 0.92, 0.95);               // Aquamarine
}

void main() {
    vec2 size = max(vec2(textureSize(tex, 0)), vec2(1.0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec2 pixel = v_texcoord * size;

    int count = clamp(SPHERE_COUNT, 1, MAX_SPHERES);
    float shortSide = min(size.x, size.y);
    float baseRadius = max(shortSide * BASE_RADIUS_FRACTION, 4.0);

    bool pointerValid = (pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    vec2 pointerPx = pointer_position * size;

    // Distinct modulation frequencies and phase seeds per sphere so each sphere
    // bounces at varying angles across the walls while remaining phase-separated.
    const vec2 MOD_FREQS[MAX_SPHERES] = vec2[MAX_SPHERES](
        vec2(1.618, 1.272),
        vec2(1.382, 1.732),
        vec2(1.839, 1.146),
        vec2(1.220, 1.562),
        vec2(1.755, 1.325),
        vec2(1.465, 1.682)
    );
    const vec2 MOD_PHASES[MAX_SPHERES] = vec2[MAX_SPHERES](
        vec2(0.15, 0.73),
        vec2(2.24, 1.48),
        vec2(4.19, 3.05),
        vec2(1.12, 4.62),
        vec2(3.38, 0.89),
        vec2(5.14, 2.71)
    );
    const vec2 QUAD_OFFSETS[MAX_SPHERES] = vec2[MAX_SPHERES](
        vec2(0.00, 0.50),
        vec2(0.08, 0.54),
        vec2(0.96, 0.44),
        vec2(0.04, 0.56),
        vec2(0.92, 0.46),
        vec2(0.10, 0.58)
    );
    const float RAD_SCALE[MAX_SPHERES] = float[MAX_SPHERES](
        1.00, 0.94, 1.04, 0.90, 0.97, 0.86
    );

    float baseTravel = integratedTableDistance(time, BASE_SPEED, FRICTION_SLOWDOWN_SEC, MIN_GLIDE_SPEED_RATIO);
    float invCount = 1.0 / float(count);
    float wanderAmp = clamp(PATH_WANDER_AMPLITUDE, 0.0, 0.20);

    vec2  pos[MAX_SPHERES];
    float rad[MAX_SPHERES];

    // 1. Phase-separated 4-wall billiard trajectories (guaranteed bounds, zero clumping).
    for (int i = 0; i < MAX_SPHERES; ++i) {
        if (i >= count) {
            pos[i] = vec2(-9999.0);
            rad[i] = 1.0;
            continue;
        }

        float r = (count == 1)
            ? baseRadius
            : baseRadius * mix(1.0, RAD_SCALE[i], RADIUS_VARIATION);
        rad[i] = r;

        vec2 minBound = vec2(r + WALL_MARGIN_PX);
        vec2 maxBound = max(size - minBound, minBound + vec2(1.0));
        vec2 tableSpan = maxBound - minBound;

        // Topological phase spacing (2.0 / count) around the period-2.0 rail cycle
        // keeps spheres evenly distributed around the table at all times.
        float phaseSlot = 2.0 * float(i) * invCount;
        vec2 modArg = fract(baseTravel * MOD_FREQS[i] * 0.25) * TAU + MOD_PHASES[i];
        vec2 wander = wanderAmp * vec2(sin(modArg.x), cos(modArg.y));

        vec2 u = vec2(baseTravel + phaseSlot) + QUAD_OFFSETS[i] + wander;
        vec2 folded = polynomialRailWave(u);

        pos[i] = minBound + folded * tableSpan;
    }

    // 2. Single-pass, contraction-bounded C2 soft separation cushion between spheres.
    if (count > 1 && BALL_SOFT_CUSHION > 0.001) {
        vec2 pairNudge[MAX_SPHERES];
        for (int i = 0; i < MAX_SPHERES; ++i) {
            pairNudge[i] = vec2(0.0);
        }

        for (int i = 0; i < MAX_SPHERES; ++i) {
            if (i >= count) break;
            for (int j = i + 1; j < MAX_SPHERES; ++j) {
                if (j >= count) break;

                vec2 delta = pos[i] - pos[j];
                float distSq = dot(delta, delta);
                float minSep = rad[i] + rad[j];
                float cushionZone = minSep * 1.35;

                if (distSq < cushionZone * cushionZone) {
                    float dist = sqrt(distSq);
                    float q = dist / cushionZone;
                    float w = wendlandC2(q);

                    float core = 0.65 * minSep;
                    vec2 softDir = delta * inversesqrt(distSq + core * core);
                    float maxPush = minSep * clamp(BALL_SOFT_CUSHION, 0.0, 0.45) * w;

                    float totalR = max(minSep, 1.0);
                    pairNudge[i] += softDir * (maxPush * (rad[j] / totalR));
                    pairNudge[j] -= softDir * (maxPush * (rad[i] / totalR));
                }
            }
        }

        for (int i = 0; i < MAX_SPHERES; ++i) {
            if (i >= count) break;
            vec2 minBound = vec2(rad[i] + WALL_MARGIN_PX);
            vec2 maxBound = max(size - minBound, minBound + vec2(1.0));
            pos[i] = softBoundaryCushion(pos[i] + pairNudge[i], minBound, maxBound, rad[i] * 0.45);
        }
    }

    // 3. Proportionate, gentle C2 cursor & click touch nudge.
    float clickMaxAge = 4.5 / max(CLICK_DECAY_RATE, 0.25);
    for (int i = 0; i < MAX_SPHERES; ++i) {
        if (i >= count) break;

        float r = rad[i];
        vec2 minBound = vec2(r + WALL_MARGIN_PX);
        vec2 maxBound = max(size - minBound, minBound + vec2(1.0));
        vec2 touchOffset = vec2(0.0);

        // A. Subtle click ripple when clicking near a sphere.
        for (int c = 0; c < MAX_CLICK_IMPULSES; ++c) {
            float age = pointer_pressed_times[c];
            if (age <= 0.0 || age >= clickMaxAge) {
                continue;
            }
            vec2 clickOrig = pointer_pressed_positions[c];
            if (all(greaterThanEqual(clickOrig, vec2(0.0)))
                && all(lessThanEqual(clickOrig, vec2(1.0)))) {
                vec2 fromClick = pos[i] - clickOrig * size;
                float cDistSq = dot(fromClick, fromClick);
                if (cDistSq < CLICK_REACH_PX * CLICK_REACH_PX) {
                    float cDist = sqrt(cDistSq);
                    float q = cDist / CLICK_REACH_PX;
                    float env = smoothstep(0.0, 0.14, age) * exp(-clamp(age * CLICK_DECAY_RATE, 0.0, 40.0));
                    float spatial = wendlandC2(q);
                    vec2 regDir = fromClick * inversesqrt(cDistSq + r * r * 0.85);
                    touchOffset += regDir * (CLICK_NUDGE_PX * env * spatial);
                }
            }
        }

        // B. Proportionate cursor touch nudge when touching/grazing the sphere rim.
        if (pointerValid) {
            vec2 fromPtr = pos[i] - pointerPx;
            float pDistSq = dot(fromPtr, fromPtr);
            float touchRadius = r * CURSOR_REACH_MULT;

            if (pDistSq < touchRadius * touchRadius) {
                float pDist = sqrt(pDistSq);
                float q = pDist / touchRadius;
                float w = wendlandC2(q);

                float coreSq = (0.75 * r) * (0.75 * r);
                vec2 gentleDir = fromPtr * inversesqrt(pDistSq + coreSq);
                touchOffset += gentleDir * (CURSOR_NUDGE_PX * w);
            }
        }

        if (dot(touchOffset, touchOffset) > 1e-4) {
            pos[i] = softBoundaryCushion(pos[i] + touchOffset, minBound, maxBound, r * 0.45);
        }
    }

    // 4. Find closest glass sphere (with subtle rim yield under touch) and contact shadow.
    int hitIdx = -1;
    float nearestNormR = 2.0;
    vec2 bestDelta = vec2(0.0);
    float bestRad = 1.0;
    float shadowMask = 0.0;

    for (int i = 0; i < MAX_SPHERES; ++i) {
        if (i >= count) break;
        vec2 d = pixel - pos[i];
        float r = rad[i];
        float rawDistSq = dot(d, d);
        float rawDist = sqrt(rawDistSq);

        // Subtle, proportionate glass rim cushion when the cursor touches the sphere.
        float effRad = r;
        vec2 squishShift = vec2(0.0);
        if (pointerValid && CURSOR_RIM_SQUISH > 0.001) {
            vec2 toPtr = pointerPx - pos[i];
            float ptrDistSq = dot(toPtr, toPtr);
            float squishZone = r * 1.35;
            if (ptrDistSq < squishZone * squishZone) {
                float ptrDist = sqrt(ptrDistSq);
                float wPtr = wendlandC2(ptrDist / squishZone);
                vec2 ptrDir = toPtr * inversesqrt(ptrDistSq + r * r * 0.35);
                vec2 fragDir = d * inversesqrt(rawDistSq + r * r * 0.10);
                float facing = max(0.0, dot(fragDir, ptrDir));
                float rimIndent = CURSOR_RIM_SQUISH * wPtr * facing * facing;
                float equatorBulge = 0.30 * CURSOR_RIM_SQUISH * wPtr * (1.0 - facing * facing);
                effRad = r * (1.0 - rimIndent + equatorBulge);
                squishShift = ptrDir * (r * 0.06 * wPtr * facing);
            }
        }

        float safeEffRad = max(effRad, 1.0);
        float normR = rawDist / safeEffRad;
        if (normR < nearestNormR) {
            nearestNormR = normR;
            bestDelta = d + squishShift;
            bestRad = safeEffRad;
            hitIdx = i;
        }

        vec2 shadowDelta = (pixel - (pos[i] + SHADOW_OFFSET_PX)) / (safeEffRad * 1.06);
        float sDist = length(shadowDelta);
        if (sDist < 1.35) {
            float s = (1.0 - smoothstep(0.22, 1.35, sDist)) * SHADOW_OPACITY;
            shadowMask = max(shadowMask, s);
        }
    }

    vec4 original = textureLod(tex, baseUV, 0.0);

    // Fast early-out for all desktop pixels outside spheres and their shadows.
    if (nearestNormR >= 1.0) {
        if (shadowMask > 0.001) {
            original.rgb *= (1.0 - shadowMask);
        }
        fragColor = original;
        return;
    }

    // 5. 3D Thick-Glass Ball-Lens Refraction & Chromatic Dispersion.
    float rSq = clamp(nearestNormR * nearestNormR, 0.0, 1.0);
    float depth = sqrt(1.0 - rSq);
    vec3 normal = normalize(vec3(bestDelta / bestRad, max(depth, 1e-4)));

    vec2 mappedDelta = BALL_LENS_INVERT
        ? -bestDelta * ((1.0 - REFRACTION_STRENGTH) + REFRACTION_STRENGTH * depth)
        :  bestDelta * (1.0 - REFRACTION_STRENGTH * (0.35 + 0.65 * depth));

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 48.0, min(edge.x, edge.y));

    vec2 centerUV = v_texcoord + (mappedDelta - bestDelta) * edgeMask / size;
    vec2 radialDir = bestDelta * inversesqrt(dot(bestDelta, bestDelta) + 1.0);
    vec2 split = radialDir * (DISPERSION_PX * (1.0 - depth * 0.7) * edgeMask) / size;

    vec4 centerSample = textureLod(tex, clamp(centerUV, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 redSample    = textureLod(tex, clamp(centerUV + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blueSample   = textureLod(tex, clamp(centerUV - split, halfTexel, 1.0 - halfTexel), 0.0);

    vec3 sphereRGB = vec3(
        redSample.a > 1e-4 ? redSample.r * (centerSample.a / redSample.a) : 0.0,
        centerSample.g,
        blueSample.a > 1e-4 ? blueSample.b * (centerSample.a / blueSample.a) : 0.0
    );

    // 6. Fresnel rim tint, internal depth shading, dual specular & caustic bounce.
    vec3 tint = sphereGlassTint(hitIdx) * centerSample.a;
    float fresnel = pow(1.0 - depth, 2.6);
    float rimBand = smoothstep(0.68, 0.98, nearestNormR);

    sphereRGB *= (0.74 + 0.26 * depth);
    sphereRGB = mix(sphereRGB, tint, (0.55 * fresnel + 0.45 * rimBand) * FRESNEL_RIM_STRENGTH);

    vec3 keyLightDir  = normalize(vec3(-0.42, -0.52, 1.0));
    vec3 backLightDir = normalize(vec3(0.45, 0.55, 0.65));
    float keySpec     = pow(max(0.0, dot(normal, keyLightDir)), 42.0) * SPECULAR_STRENGTH;
    float secondaryRimSpec = pow(max(0.0, dot(normal, normalize(vec3(0.55, 0.48, 0.68)))), 28.0) * 0.18;
    float causticGlow = pow(max(0.0, dot(normal, backLightDir)), 9.0) * CAUSTIC_BOUNCE * (1.0 - depth * 0.5);

    sphereRGB += tint * causticGlow;
    sphereRGB = mix(sphereRGB, vec3(centerSample.a), clamp(keySpec + secondaryRimSpec, 0.0, 1.0));
    sphereRGB = clamp(sphereRGB, vec3(0.0), vec3(centerSample.a));

    vec4 shadowedBg = vec4(original.rgb * (1.0 - shadowMask), original.a);
    vec4 sphereColor = vec4(sphereRGB, centerSample.a);

    float aaMask = 1.0 - smoothstep(0.965, 1.0, nearestNormR);
    fragColor = mix(shadowedBg, sphereColor, aaMask);
}
