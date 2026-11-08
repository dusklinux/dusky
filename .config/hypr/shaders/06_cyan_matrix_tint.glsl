#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Cool bioluminescent cyan-teal spectrum grade with optional hue retention.
// Input/output: SDR, opaque or premultiplied alpha[cite: 13].

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const vec3  SHADOW_TONE     = vec3(0.01, 0.09, 0.14); // Abyssal deep-sea shadow tint
const vec3  MID_TONE        = vec3(0.04, 0.78, 0.88); // Crisp cyan-teal midtone
const vec3  HIGHLIGHT_TONE  = vec3(0.76, 0.99, 1.00); // Ice-cyan highlight peak
const float COLOR_RETENTION = 0.16;                   // Fraction of original RGB chroma kept [0.0, 1.0]
const float CONTRAST_CURVE  = 1.06;                   // Midtone contrast exponent
// ============================================================================

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }
    return clamp(color.rgb / color.a, 0.0, 1.0);
}

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 sampleColor = textureLod(tex, uv, 0.0);
    vec3 color = straightRGB(sampleColor);

    float luma = pow(clamp(dot(color, LUMA), 0.0, 1.0), CONTRAST_CURVE);
    vec3 graded = mix(
        mix(SHADOW_TONE, MID_TONE, smoothstep(0.0, 0.60, luma)),
        HIGHLIGHT_TONE,
        smoothstep(0.50, 1.0, luma)
    ) * (0.15 + 0.85 * luma);

    vec3 result = clamp(mix(graded, color, COLOR_RETENTION), 0.0, 1.0);
    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
