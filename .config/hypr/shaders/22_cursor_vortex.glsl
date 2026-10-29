#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform float pointer_last_active;
uniform int pointer_hidden;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// A local whirlpool that stirs harder when the pointer moves.
const float TAU = 6.283185307179586;
const float RADIUS_PX = 270.0;
const float TWIST_RADIANS = 0.85;
const float EDGE_FADE_PX = 80.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float weight = 1.0 - smoothstep(0.0, RADIUS_PX, length(delta));
    float visible = float(pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    float activity = 1.0 - smoothstep(0.0, 1.5, pointer_last_active);
    float phase = mod(time, 12.0) * (TAU / 12.0);
    float angle = TWIST_RADIANS * weight * weight
        * (0.45 + 0.55 * activity) * sin(phase) * visible;
    float c = cos(angle);
    float s = sin(angle);
    vec2 rotated = vec2(c * delta.x - s * delta.y, s * delta.x + c * delta.y);
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + (rotated - delta) * edgeMask / size,
        halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
