#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform int pointer_hidden;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Clear glass magnifier: about 1.4x at the center, smoothly neutral outside.
const float RADIUS_PX = 220.0;
const float MAGNIFICATION = 1.4; // >= 1.
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float weight = 1.0 - smoothstep(0.0, RADIUS_PX, length(delta));
    float visible = float(pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 offset = -delta * (1.0 - 1.0 / MAGNIFICATION)
        * weight * weight * visible * edgeMask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
