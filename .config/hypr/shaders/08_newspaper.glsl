#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Monochrome newspaper halftone.
// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

const float DOT_SPACING_PX = 5.0; // Must be >= 3.
const int COLOR_LEVELS = 4;      // Must be >= 2.
const bool USE_DITHERING = true;

const vec3 PAPER_COLOR = vec3(0.95, 0.92, 0.85);
const float PAPER_GRAIN = 0.025; // [0, 1]

// Smoothing multiplier. Must be positive.
const float DOT_SOFTNESS = 1.0;

// 0 = no ink contrast; 1 = black ink.
const float INK_DARKNESS = 0.95; // [0, 1]

const float BAYER[16] = float[16](
     0.0,  8.0,  2.0, 10.0,
    12.0,  4.0, 14.0,  6.0,
     3.0, 11.0,  1.0,  9.0,
    15.0,  7.0, 13.0,  5.0
);

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }

    return clamp(color.rgb / color.a, 0.0, 1.0);
}

float pixelHash(uvec2 pixel) {
    // Unsigned overflow is intentional and defined.
    uint h = (pixel.x * 0x9e3779b9u)
        ^ (pixel.y * 0x85ebca6bu);

    h ^= h >> 16u;
    h *= 0x7feb352du;
    h ^= h >> 15u;
    h *= 0x846ca68bu;
    h ^= h >> 16u;

    return float(h & 0x00ffffffu) * (1.0 / 16777216.0);
}

float bayerThreshold(vec2 cell) {
    ivec2 p = ivec2(mod(cell, 4.0));
    return (BAYER[p.x + 4 * p.y] + 0.5) / 16.0 - 0.5;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;

    // Continuous coordinates for derivative measurement.
    vec2 sourcePosition = v_texcoord * size;
    vec2 dx = dFdx(sourcePosition);
    vec2 dy = dFdy(sourcePosition);

    // Conservative half-width of an output pixel in source-pixel units.
    float footprint = 0.5 * (length(dx) + length(dy));

    // Positive even in degenerate/local constant-coordinate situations.
    float aa = max(footprint, 0.5)
        * DOT_SOFTNESS / DOT_SPACING_PX;

    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec2 pixelPosition = uv * size;
    vec4 centerSample = textureLod(tex, uv, 0.0);

    vec2 cell = floor(pixelPosition / DOT_SPACING_PX);
    vec2 cellStart = cell * DOT_SPACING_PX;
    vec2 cellEnd = min(cellStart + DOT_SPACING_PX, size);
    vec2 cellCenter = 0.5 * (cellStart + cellEnd);

    // One tone per cell. Explicit LOD avoids derivative-based mip choice
    // at the discontinuities between neighboring cell centers.
    vec4 cellSample = textureLod(
        tex,
        clamp(cellCenter / size, halfTexel, 1.0 - halfTexel),
        0.0
    );

    float tone = dot(straightRGB(cellSample), LUMA);
    float steps = float(COLOR_LEVELS - 1);

    float threshold = USE_DITHERING
        ? bayerThreshold(cell)
        : 0.0;

    tone = clamp(
        floor(tone * steps + 0.5 + threshold) / steps,
        0.0,
        1.0
    );

    float darkness = 1.0 - tone;

    // Use the same actual center for sampling and dot placement,
    // including partial cells at the right/bottom texture boundaries.
    vec2 local = (pixelPosition - cellCenter) / DOT_SPACING_PX;
    float distanceToCenter = length(local);

    vec2 halfCellSize = 0.5 * (cellEnd - cellStart)
        / DOT_SPACING_PX;

    float maximumRadius = length(halfCellSize);

    // Artistic radius mapping, not exact area-calibrated reproduction.
    float radius = mix(
        -aa,
        maximumRadius + aa,
        sqrt(darkness)
    );

    float inkCoverage = 1.0 - smoothstep(
        radius - aa,
        radius + aa,
        distanceToCenter
    );

    // Exact quantized endpoints, independent of roundoff in radius math.
    if (darkness <= 0.0) {
        inkCoverage = 0.0;
    } else if (darkness >= 1.0) {
        inkCoverage = 1.0;
    }

    uvec2 pixel = uvec2(floor(pixelPosition));
    float grain = pixelHash(pixel) * PAPER_GRAIN;

    vec3 paper = clamp(PAPER_COLOR - grain, 0.0, 1.0);
    vec3 ink = paper * (1.0 - INK_DARKNESS);
    vec3 color = mix(paper, ink, inkCoverage);

    fragColor = vec4(color * centerSample.a, centerSample.a);
}
