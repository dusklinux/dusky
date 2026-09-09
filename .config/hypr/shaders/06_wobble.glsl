#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float TIME;
out vec4 fragColor;

// Spatial warp: sampled RGBA travels together.
const float TAU = 6.283185307179586;

// Must be positive. Integer temporal harmonics close the loop.
const float LOOP_SECONDS = 64.0;

// Spatial phase density per shorter texture dimension.
const float FREQUENCY = 15.0; // Nonnegative.

// Displacement measured as a fraction of the shorter dimension.
const float AMPLITUDE = 0.025; // Nonnegative; keep small.

// Edge fade in the same units. 0 disables fading.
const float EDGE_FADE = 0.10; // Nonnegative.

const bool ORGANIC_MOTION = true;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float shortSide = min(size.x, size.y);

    // Bounds shader arithmetic, but cannot restore precision already
    // lost when the host converted a large timestamp to float.
    float phase = mod(TIME, LOOP_SECONDS)
        * (TAU / LOOP_SECONDS);

    vec2 position = (v_texcoord - 0.5) * size / shortSide;

    vec2 edgeDistances = min(v_texcoord, 1.0 - v_texcoord)
        * size / shortSide;

    float edgeMask = 1.0;

    if (EDGE_FADE > 0.0) {
        edgeMask = smoothstep(
            0.0,
            EDGE_FADE,
            min(edgeDistances.x, edgeDistances.y)
        );
    }

    vec2 offset;

    if (ORGANIC_MOTION) {
        offset.x =
            0.50 * sin(position.y * FREQUENCY       + phase * 31.0)
          + 0.30 * sin(position.y * FREQUENCY * 2.1 + phase * 42.0)
          + 0.20 * sin(position.y * FREQUENCY * 0.5 + phase * 22.0)
          + 0.10 * sin(position.x * FREQUENCY * 0.3 + phase * 15.0);

        offset.y =
            0.50 * cos(position.x * FREQUENCY * 0.9 + phase * 35.0)
          + 0.30 * cos(position.x * FREQUENCY * 1.7 + phase * 26.0)
          + 0.20 * cos(position.x * FREQUENCY * 0.4 + phase * 44.0)
          + 0.10 * cos(position.y * FREQUENCY * 0.3 + phase * 19.0);
    } else {
        offset = vec2(
            sin(position.y * FREQUENCY + phase * 31.0),
            cos(position.x * FREQUENCY + phase * 31.0)
        );
    }

    vec2 uv = v_texcoord
        + offset * AMPLITUDE * edgeMask * shortSide / size;

    fragColor = textureLod(
        tex,
        clamp(uv, halfTexel, 1.0 - halfTexel),
        0.0
    );
}
