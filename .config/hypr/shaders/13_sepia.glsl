#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.

// GLSL columns, arranged for SEPIA_MATRIX * RGB.
const mat3 SEPIA_MATRIX = mat3(
    0.393, 0.349, 0.272,
    0.769, 0.686, 0.534,
    0.189, 0.168, 0.131
);

const float INTENSITY = 1.0; // [0, 1]

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    // Matrix transformation and strength interpolation are linear,
    // so they can operate directly on premultiplied RGB.
    vec3 transformed = SEPIA_MATRIX * color.rgb;
    vec3 result = mix(color.rgb, transformed, INTENSITY);

    // Clip after applying filter strength.
    result = clamp(result, vec3(0.0), vec3(color.a));

    fragColor = vec4(result, color.a);
}
