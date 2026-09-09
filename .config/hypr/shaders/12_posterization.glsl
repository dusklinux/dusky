#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.
const int COLOR_LEVELS = 4; // Must be >= 2.
const bool DITHER = true;

const float BAYER[16] = float[16](
     0.0,  8.0,  2.0, 10.0,
    12.0,  4.0, 14.0,  6.0,
     3.0, 11.0,  1.0,  9.0,
    15.0,  7.0, 13.0,  5.0
);

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }

    return clamp(color.rgb / color.a, 0.0, 1.0);
}

float bayerThreshold(vec2 pixelPosition) {
    ivec2 p = ivec2(mod(floor(pixelPosition), 4.0));
    return (BAYER[p.x + 4 * p.y] + 0.5) / 16.0 - 0.5;
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);

    vec4 sampleColor = textureLod(tex, uv, 0.0);
    vec3 color = straightRGB(sampleColor);

    float steps = float(COLOR_LEVELS - 1);
    float threshold = DITHER
        ? bayerThreshold(uv * size)
        : 0.0;

    vec3 posterized = floor(
        color * steps + 0.5 + threshold
    ) / steps;

    posterized = clamp(posterized, 0.0, 1.0);

    fragColor = vec4(
        posterized * sampleColor.a,
        sampleColor.a
    );
}
