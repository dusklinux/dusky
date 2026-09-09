#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Adaptive saturation enhancement.
// Input/output: SDR, opaque or premultiplied alpha.
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

// 0 disables. Less-saturated colors receive more requested boost.
const float VIBRANCE = 0.35; // [0, 1]

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

    float highChannel = max(color.r, max(color.g, color.b));
    float lowChannel = min(color.r, min(color.g, color.b));
    float gray = clamp(dot(color, LUMA), 0.0, 1.0);

    float saturation = 0.0;

    if (highChannel > 0.0) {
        saturation = (highChannel - lowChannel) / highChannel;
    }

    float gain = 1.0 + VIBRANCE * (1.0 - saturation);

    float upperChroma = highChannel - gray;
    float lowerChroma = gray - lowChannel;

    // Only divide when expansion would actually leave the gamut.
    // Each condition implies its denominator is positive.
    if (upperChroma * gain > 1.0 - gray) {
        gain = (1.0 - gray) / upperChroma;
    }

    if (lowerChroma * gain > gray) {
        gain = gray / lowerChroma;
    }

    vec3 result = vec3(gray) + (color - vec3(gray)) * gain;
    result = clamp(result, 0.0, 1.0);

    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
