#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Ordinary saturation followed by contrast, not adaptive vibrance.
// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

// 0 = grayscale, 1 = unchanged.
// Values above 1 can produce gamut clipping.
const float SATURATION = 1.25; // Nonnegative.

// Contrast around working-space value 0.5.
// 0 = midgray, 1 = unchanged.
// This does not increase actual display dynamic range.
const float CONTRAST = 1.05; // Nonnegative.

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

    float gray = dot(color, LUMA);
    vec3 saturated = mix(vec3(gray), color, SATURATION);

    vec3 result = (saturated - 0.5) * CONTRAST + 0.5;
    result = clamp(result, 0.0, 1.0);

    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
