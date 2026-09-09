#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Input/output: SDR, opaque or premultiplied alpha.
const float STRENGTH = 0.010; // Nonnegative; keep small.
const bool QUADRATIC_FALLOFF = true;

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }

    return clamp(color.rgb / color.a, 0.0, 1.0);
}

vec4 sampleScreen(vec2 uv, vec2 halfTexel) {
    return textureLod(
        tex,
        clamp(uv, halfTexel, 1.0 - halfTexel),
        0.0
    );
}

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;

    vec2 centered = v_texcoord - 0.5;

    // Radius in units of the shorter texture dimension.
    vec2 radial = centered * size / min(size.x, size.y);

    // centered alone gives linear radial displacement.
    // Multiplying by radius gives quadratic radial displacement.
    float falloff = QUADRATIC_FALLOFF ? length(radial) : 1.0;
    vec2 offset = centered * STRENGTH * falloff;

    vec4 centerSample = sampleScreen(v_texcoord, halfTexel);
    vec4 redSample = sampleScreen(v_texcoord - offset, halfTexel);
    vec4 blueSample = sampleScreen(v_texcoord + offset, halfTexel);

    vec3 center = straightRGB(centerSample);
    vec3 red = straightRGB(redSample);
    vec3 blue = straightRGB(blueSample);

    vec3 color = vec3(red.r, center.g, blue.b);
    fragColor = vec4(color * centerSample.a, centerSample.a);
}
