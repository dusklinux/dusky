#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Calming emerald-jade grade centered near peak human photopic sensitivity (555 nm).
// Input/output: SDR, opaque or premultiplied alpha[cite: 13].

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const vec3  SHADOW_TONE     = vec3(0.02, 0.11, 0.06); // Deep pine-jade shadow base
const vec3  MID_TONE        = vec3(0.14, 0.82, 0.46); // Crisp emerald midtone
const vec3  HIGHLIGHT_TONE  = vec3(0.82, 1.00, 0.88); // Soft mint-white highlight
const float COLOR_RETENTION = 0.20;                   // Fraction of original RGB chroma kept [0.0, 1.0]
const float INTENSITY       = 0.92;                   // Overall emerald grade strength [0.0, 1.0]
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

    float luma = dot(color, LUMA);
    vec3 emerald = mix(
        mix(SHADOW_TONE, MID_TONE, smoothstep(0.0, 0.58, luma)),
        HIGHLIGHT_TONE,
        smoothstep(0.48, 1.0, luma)
    ) * (0.12 + 0.88 * luma);

    vec3 blended = mix(emerald, color, COLOR_RETENTION);
    vec3 result = clamp(mix(color, blended, INTENSITY), 0.0, 1.0);
    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
