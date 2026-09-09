#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.
// Pixel-space circular contours, normalized to radius 1 at corners.

const float RADIUS = 0.65;   // Nonnegative start radius.
const float SOFTNESS = 0.35; // Must be positive.
const float STRENGTH = 0.50; // [0, 1]

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    vec4 color = textureLod(tex, uv, 0.0);

    vec2 centeredPixels = (v_texcoord - 0.5) * size;
    float radius = length(centeredPixels) / (0.5 * length(size));

    float darkening = smoothstep(
        RADIUS,
        RADIUS + SOFTNESS,
        radius
    );

    color.rgb *= 1.0 - STRENGTH * darkening;
    fragColor = color;
}
