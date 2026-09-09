#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    // alpha * (1 - straightRGB) = alpha - premultipliedRGB.
    vec3 inverted = clamp(
        vec3(color.a) - color.rgb,
        vec3(0.0),
        vec3(color.a)
    );

    fragColor = vec4(inverted, color.a);
}
