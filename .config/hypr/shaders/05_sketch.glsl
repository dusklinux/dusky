#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

const float EDGE_THRESHOLD = 0.15; // Nonnegative.
const float EDGE_SOFTNESS = 0.08;  // Must be positive.

// Sobel sample spacing, not literal stroke width.
const float SAMPLE_RADIUS_PX = 1.0; // Must be positive.

const float LINE_VALUE = 0.05;  // [0, 1]
const float PAPER_VALUE = 0.98; // [0, 1]
const float PAPER_GRAIN = 0.03; // [0, 1]

// Local standard-deviation threshold adjustment.
// This is a heuristic, not a general denoiser.
const float LOCAL_THRESHOLD_GAIN = 0.5; // Nonnegative.

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

float sampleLuma(vec2 uv, vec2 halfTexel) {
    vec4 color = textureLod(
        tex,
        clamp(uv, halfTexel, 1.0 - halfTexel),
        0.0
    );

    return dot(straightRGB(color), LUMA);
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 stepUV = SAMPLE_RADIUS_PX / size;
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    vec4 center = textureLod(tex, uv, 0.0);
    float c = dot(straightRGB(center), LUMA);

    float tl = sampleLuma(
        uv + vec2(-stepUV.x, -stepUV.y), halfTexel
    );
    float t = sampleLuma(
        uv + vec2(0.0, -stepUV.y), halfTexel
    );
    float tr = sampleLuma(
        uv + vec2(stepUV.x, -stepUV.y), halfTexel
    );
    float l = sampleLuma(
        uv + vec2(-stepUV.x, 0.0), halfTexel
    );
    float r = sampleLuma(
        uv + vec2(stepUV.x, 0.0), halfTexel
    );
    float bl = sampleLuma(
        uv + vec2(-stepUV.x, stepUV.y), halfTexel
    );
    float b = sampleLuma(
        uv + vec2(0.0, stepUV.y), halfTexel
    );
    float br = sampleLuma(
        uv + vec2(stepUV.x, stepUV.y), halfTexel
    );

    float gx = (tr + 2.0 * r + br) - (tl + 2.0 * l + bl);
    float gy = (bl + 2.0 * b + br) - (tl + 2.0 * t + tr);
    float gradient = length(vec2(gx, gy));

    float mean = (tl + t + tr + l + c + r + bl + b + br) / 9.0;

    vec3 d0 = vec3(tl, t, tr) - mean;
    vec3 d1 = vec3(l, c, r) - mean;
    vec3 d2 = vec3(bl, b, br) - mean;

    float standardDeviation = sqrt(
        (dot(d0, d0) + dot(d1, d1) + dot(d2, d2)) / 9.0
    );

    float threshold = EDGE_THRESHOLD
        + standardDeviation * LOCAL_THRESHOLD_GAIN;

    float edge = smoothstep(
        threshold - EDGE_SOFTNESS,
        threshold + EDGE_SOFTNESS,
        gradient
    );

    uvec2 pixel = uvec2(floor(uv * size));

    float paper = clamp(
        PAPER_VALUE - pixelHash(pixel) * PAPER_GRAIN,
        0.0,
        1.0
    );

    float value = mix(paper, LINE_VALUE, edge);
    fragColor = vec4(vec3(value) * center.a, center.a);
}
