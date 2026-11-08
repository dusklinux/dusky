#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Translucent curtains of emerald, cyan, and violet aurora borealis light
// ripple gently across the desktop with zero text distortion.

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float AURORA_STRENGTH   = 0.18; // Peak additive glow intensity [0.0 = off, 0.35 = vivid]
const float CURTAIN_FREQUENCY = 5.5;  // Horizontal fold density of the aurora ribbons
const float VERTICAL_REACH    = 0.78; // How far down the screen the aurora extends [0.2, 1.0]
const float SHIMMER_DETAIL    = 14.0; // Vertical ray striation density inside the curtains
const float LOOP_SECONDS      = 36.0; // Closed-loop animation period in seconds
const vec3  AURORA_EMERALD    = vec3(0.10, 0.96, 0.55); // Lower curtain oxygen-green band
const vec3  AURORA_CYAN       = vec3(0.08, 0.82, 0.98); // Mid-curtain teal-cyan transition
const vec3  AURORA_VIOLET     = vec3(0.68, 0.24, 0.96); // Upper altitude nitrogen-violet crown
// ============================================================================

const float TAU = 6.283185307179586;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float x = v_texcoord.x * (size.x / max(size.y, 1.0));
    float y = v_texcoord.y;

    // Two undulating ribbon paths across the upper-to-mid viewport.
    float foldA = sin(x * CURTAIN_FREQUENCY + phase * 2.0 + 0.6 * sin(x * 2.3 - phase))
        * 0.12 + 0.24;
    float foldB = cos(x * (CURTAIN_FREQUENCY * 0.75) - phase * 3.0 + 0.5 * cos(x * 3.1 + phase * 2.0))
        * 0.14 + 0.36;

    float bandA = exp(-abs(y - foldA) * 6.5);
    float bandB = exp(-abs(y - foldB) * 5.2);

    // Fine vertical magnetic field ray striations.
    float rays = 0.65 + 0.35 * sin(x * SHIMMER_DETAIL * CURTAIN_FREQUENCY + sin(y * 8.0 + phase * 4.0));
    float altitudeFade = 1.0 - smoothstep(0.05, VERTICAL_REACH, y);

    float veil = (0.65 * bandA + 0.55 * bandB) * rays * altitudeFade;

    vec3 palette = mix(
        AURORA_EMERALD,
        mix(AURORA_CYAN, AURORA_VIOLET, smoothstep(0.15, 0.65, y)),
        smoothstep(0.10, 0.45, y + 0.1 * sin(x * 4.0 + phase))
    );

    vec3 rgb = color.rgb + palette * (veil * AURORA_STRENGTH * color.a);
    fragColor = vec4(clamp(rgb, vec3(0.0), vec3(color.a)), color.a);
}
