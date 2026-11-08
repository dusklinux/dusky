#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

// Enable only when straight input RGB is sRGB-encoded and the shader
// must also output sRGB-encoded RGB.
//
// Leave false for a direct working-space weighted grayscale,
// including when the compositor already supplies linear RGB.
const bool SRGB_LINEAR_LIGHT = false;

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }

    return clamp(color.rgb / color.a, 0.0, 1.0);
}

float srgbToLinear(float c) {
    return c <= 0.04045
        ? c / 12.92
        : pow((c + 0.055) / 1.055, 2.4);
}

float linearToSrgb(float c) {
    return c <= 0.0031308
        ? c * 12.92
        : 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 sampleColor = textureLod(tex, uv, 0.0);

    float gray;

    if (SRGB_LINEAR_LIGHT) {
        vec3 color = straightRGB(sampleColor);

        vec3 linearColor = vec3(
            srgbToLinear(color.r),
            srgbToLinear(color.g),
            srgbToLinear(color.b)
        );

        float linearGray = dot(linearColor, LUMA);
        float encodedGray = clamp(linearToSrgb(linearGray), 0.0, 1.0);

        gray = encodedGray * sampleColor.a;
    } else {
        // Linear transformation: premultiplied RGB can be used directly.
        gray = dot(sampleColor.rgb, LUMA);
    }

    fragColor = vec4(vec3(gray), sampleColor.a);
}
