#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Warm golden-hour split-toning: burgundy shadows, solar gold midtones, and cream highlights.
// Input/output: SDR, opaque or premultiplied alpha[cite: 13].

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const vec3  SHADOW_TONE     = vec3(0.18, 0.03, 0.06); // Warm ruby-burgundy shadow base
const vec3  MID_TONE        = vec3(0.96, 0.56, 0.12); // Rich sunset amber-gold midtone
const vec3  HIGHLIGHT_TONE  = vec3(1.00, 0.95, 0.78); // Soft sunlit cream highlight
const float COLOR_RETENTION = 0.22;                   // Fraction of original RGB chroma kept [0.0, 1.0]
const float INTENSITY       = 0.90;                   // Overall split-tone blend [0.0, 1.0]
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
    vec3 sunset = mix(
        mix(SHADOW_TONE, MID_TONE, smoothstep(0.0, 0.55, luma)),
        HIGHLIGHT_TONE,
        smoothstep(0.45, 1.0, luma)
    ) * (0.10 + 0.90 * luma);

    vec3 blended = mix(sunset, color, COLOR_RETENTION);
    vec3 result = clamp(mix(color, blended, INTENSITY), 0.0, 1.0);
    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
