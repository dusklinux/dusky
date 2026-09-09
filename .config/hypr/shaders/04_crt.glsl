#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Static artistic CRT.
// Input/output: SDR, opaque or premultiplied alpha.
// Outside the curved image is an intentionally opaque black bezel.

const float TAU = 6.283185307179586;

// Nonnegative radial outward sampling distortion.
// 0 disables curvature; small values are recommended.
const float CURVATURE = 0.08;

// Source-pixel scanline period. Must be positive.
// Values above 2 are recommended for visible scanlines.
const float SCANLINE_PERIOD_PX = 3.0;
const float SCANLINE_STRENGTH = 0.22; // [0, 1]

// Fractional radial red/blue displacement.
const float ABERRATION = 0.002; // Nonnegative; keep small.

// Radius is normalized to 1 at the unwarped texture corners.
const float VIGNETTE_START = 0.55;
const float VIGNETTE_END = 1.0; // Must exceed VIGNETTE_START.
const float VIGNETTE_STRENGTH = 0.30; // [0, 1]

// Artistic gain, not bloom. Values above 1 may clip highlights.
const float GAIN = 1.08; // Nonnegative.

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }

    return clamp(color.rgb / color.a, 0.0, 1.0);
}

vec4 sampleScreen(vec2 uv, vec2 halfTexel) {
    return textureLod(
        tex,
        clamp(uv, halfTexel, 1.0 - halfTexel),
        0.0
    );
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;

    vec2 centered = v_texcoord - 0.5;
    vec2 centeredPixels = centered * size;
    float cornerRadius = 0.5 * length(size);
    float radius = length(centeredPixels) / cornerRadius;

    vec2 warpedUV = 0.5
        + centered * (1.0 + CURVATURE * radius * radius);

    // Positive inside the warped image, negative outside.
    vec2 edgeDistances = min(warpedUV, 1.0 - warpedUV) * size;
    float edgeDistance = min(edgeDistances.x, edgeDistances.y);

    // Evaluate derivatives unconditionally.
    float edgeAA = max(0.5 * fwidth(edgeDistance), 0.5);
    float imageMask = smoothstep(-edgeAA, edgeAA, edgeDistance);

    // Screen-anchored pattern: it does not follow moving image content.
    float scanPhase = v_texcoord.y * size.y / SCANLINE_PERIOD_PX;

    // Conservative frequency suppression near the sampling limit.
    float scanFootprint = fwidth(scanPhase);
    float scanVisibility = 1.0
        - smoothstep(0.25, 0.5, scanFootprint);

    // Bound the trigonometric argument without changing its period.
    float scanWave = 0.5
        + 0.5 * cos(TAU * fract(scanPhase)) * scanVisibility;

    vec2 separation = (warpedUV - 0.5) * ABERRATION;

    vec4 redSample = sampleScreen(warpedUV + separation, halfTexel);
    vec4 centerSample = sampleScreen(warpedUV, halfTexel);
    vec4 blueSample = sampleScreen(warpedUV - separation, halfTexel);

    vec3 red = straightRGB(redSample);
    vec3 center = straightRGB(centerSample);
    vec3 blue = straightRGB(blueSample);

    vec3 color = vec3(red.r, center.g, blue.b);
    color *= 1.0 - SCANLINE_STRENGTH * scanWave;

    float vignette = 1.0 - VIGNETTE_STRENGTH
        * smoothstep(VIGNETTE_START, VIGNETTE_END, radius);

    color = clamp(color * vignette * GAIN, 0.0, 1.0);

    // Blend the premultiplied image with an opaque black bezel.
    vec3 resultRGB = color * centerSample.a * imageMask;
    float resultAlpha = mix(1.0, centerSample.a, imageMask);

    fragColor = vec4(resultRGB, resultAlpha);
}
