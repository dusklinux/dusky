#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Approximate number of blocks across the shorter texture dimension.
// Must be positive. Blocks use integer source-pixel dimensions.
const float PIXEL_COUNT = 350.0;

void main() {
    ivec2 textureDimensions = textureSize(tex, 0);
    vec2 size = vec2(textureDimensions);
    vec2 halfTexel = 0.5 / size;

    float blockSize = max(
        1.0,
        floor(min(size.x, size.y) / PIXEL_COUNT + 0.5)
    );

    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec2 position = uv * size;

    vec2 blockStart = floor(position / blockSize) * blockSize;
    vec2 blockEnd = min(blockStart + blockSize, size);

    // Pick a central source texel. For even-sized blocks, choose the
    // lower of the two central texels on each axis.
    ivec2 samplePixel = ivec2(
        floor(0.5 * (blockStart + blockEnd - 1.0))
    );

    samplePixel = clamp(
        samplePixel,
        ivec2(0),
        textureDimensions - ivec2(1)
    );

    // Exact base-level fetch, independent of sampler filtering.
    fragColor = texelFetch(tex, samplePixel, 0);
}
