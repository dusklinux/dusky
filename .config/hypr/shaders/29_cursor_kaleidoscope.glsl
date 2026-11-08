#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform int pointer_hidden;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// A pointer-following crystal portal of mirrored sectors with a clear aiming eye.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float RADIUS_PX        = 250.0; // Outer radius of the kaleidoscope portal in pixels
const float FEATHER_PX       = 36.0;  // Outer rim feather width in pixels
const float INNER_EYE_PX     = 28.0;  // Clear unwarped bullseye around cursor tip (0.0 disables)
const float INNER_FEATHER_PX = 18.0;  // Soft transition from clear eye into mirrored sectors
const float SECTORS          = 6.0;   // Number of mirrored symmetry wedges (e.g. 6.0, 8.0, 12.0)
const float ROTATION_SECONDS = 32.0;  // Seconds per full rotation loop (higher = slower)
const float PORTAL_ZOOM      = 1.22;  // Radial magnification inside the sectors
const float RADIAL_TWIST     = 0.35;  // Helical flower twist from center to rim (0.0 = straight wedges)
const float SEAM_GLOW        = 0.14;  // Subtle crystal facet highlight along mirror seams [0, 1]
const float EDGE_FADE_PX     = 64.0;  // Screen-border protection margin in pixels
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

    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    float outerMask = 1.0 - smoothstep(RADIUS_PX - FEATHER_PX, RADIUS_PX, radius);
    float innerMask = INNER_EYE_PX > 0.0
        ? smoothstep(INNER_EYE_PX, INNER_EYE_PX + INNER_FEATHER_PX, radius)
        : 1.0;
    float mask = outerMask * innerMask * edgeMask;

    vec4 original = textureLod(tex, baseUV, 0.0);
    if (mask <= 0.001) {
        fragColor = original;
        return;
    }

    float phase = mod(time, ROTATION_SECONDS) * (TAU / ROTATION_SECONDS);
    float angle = radius > 0.001 ? atan(delta.y, delta.x) : 0.0;
    float normR = radius / RADIUS_PX;

    float sector = TAU / max(SECTORS, 2.0);
    float twistedAngle = angle - phase + RADIAL_TWIST * (1.0 - normR);
    float localAngle = mod(twistedAngle + sector * 0.5, sector) - sector * 0.5;
    float folded = abs(localAngle);

    // Subtle crystal facet highlight along the mirror reflection axes
    float seamDist = min(folded, (sector * 0.5) - folded) * radius;
    float facetHighlight = (1.0 - smoothstep(0.0, 2.5, seamDist)) * SEAM_GLOW * mask;

    float zoomedRadius = radius / max(PORTAL_ZOOM, 0.1);
    vec2 mapped = vec2(cos(folded + phase), sin(folded + phase)) * zoomedRadius;
    vec2 portalUV = v_texcoord + (mapped - delta) * edgeMask / size;
    vec4 portal = textureLod(tex, clamp(portalUV, halfTexel, 1.0 - halfTexel), 0.0);

    vec4 blended = mix(original, portal, mask);
    blended.rgb = clamp(blended.rgb + vec3(facetHighlight * blended.a), vec3(0.0), vec3(blended.a));
    fragColor = blended;
}
