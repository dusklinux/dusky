#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Weighted grayscale placed in the green channel.
// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);

    float gray = dot(color.rgb, LUMA);
    fragColor = vec4(0.0, gray, 0.0, color.a);
}
