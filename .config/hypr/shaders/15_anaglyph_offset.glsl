#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Single-image red/cyan pseudo-anaglyph.
// This does not reconstruct scene depth.
// Input/output: SDR, opaque or premultiplied alpha.

// Per-eye displacement in source pixels.
const float SEPARATION_PX = 4.0; // Nonnegative.

// Extra separation toward the horizontal edges.
const float EDGE_BOOST = 0.5; // Nonnegative; 0 disables.

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

    float edgeFactor = clamp(
        abs(v_texcoord.x - 0.5) * 2.0,
        0.0,
        1.0
    );

    float separation = SEPARATION_PX
        * (1.0 + EDGE_BOOST * edgeFactor);

    vec2 offset = vec2(separation / size.x, 0.0);

    vec4 centerSample = sampleScreen(v_texcoord, halfTexel);
    vec4 leftSample = sampleScreen(v_texcoord - offset, halfTexel);
    vec4 rightSample = sampleScreen(v_texcoord + offset, halfTexel);

    vec3 leftColor = straightRGB(leftSample);
    vec3 rightColor = straightRGB(rightSample);

    vec3 color = vec3(
        leftColor.r,
        rightColor.g,
        rightColor.b
    );

    fragColor = vec4(color * centerSample.a, centerSample.a);
}
