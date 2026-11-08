#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Vintage P3 amber monochrome terminal phosphor toning with highlight bloom warmth.
// Input/output: SDR, opaque or premultiplied alpha[cite: 13].

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const vec3  PHOSPHOR_SHADOW    = vec3(0.09, 0.03, 0.00); // Deep warm CRT glass floor
const vec3  PHOSPHOR_MID       = vec3(1.00, 0.65, 0.00); // Classic 590nm P3 amber peak
const vec3  PHOSPHOR_HIGHLIGHT = vec3(1.00, 0.90, 0.58); // Incandescent cream-gold highlight roll-off
const float GAMMA_CONTRAST     = 1.10;                   // Tonal contrast curve (> 1.0 deepens shadows)
const float INTENSITY          = 1.00;                   // Blend factor with original image [0.0, 1.0]
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

    float luma = pow(clamp(dot(color, LUMA), 0.0, 1.0), GAMMA_CONTRAST);

    vec3 lowBand = mix(PHOSPHOR_SHADOW, PHOSPHOR_MID, smoothstep(0.0, 0.65, luma));
    vec3 phosphor = mix(lowBand, PHOSPHOR_HIGHLIGHT, smoothstep(0.55, 1.0, luma)) * luma;

    vec3 result = clamp(mix(color, phosphor, INTENSITY), 0.0, 1.0);
    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
