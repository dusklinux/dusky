#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform vec2 pointer_position;
uniform int pointer_hidden;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// A rotating dispersive glass lens, with clear text outside the bubble.
const float TAU = 6.283185307179586;
const float RADIUS_PX = 260.0;
const float DISPERSION_PX = 7.0;
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
    float mask = weight * weight * visible * edgeMask;
    if (mask <= 0.0) {
        fragColor = textureLod(tex, clamp(v_texcoord, halfTexel, 1.0 - halfTexel), 0.0);
        return;
    }
    float phase = mod(time, 16.0) * (TAU / 16.0);
    vec2 split = vec2(cos(phase), sin(phase)) * DISPERSION_PX * mask / size;
    vec2 uv = v_texcoord - delta * (0.14 * mask) / size;
    vec4 center = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 red = textureLod(tex, clamp(uv + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blue = textureLod(tex, clamp(uv - split, halfTexel, 1.0 - halfTexel), 0.0);
    // Recombine straight channels, then retain the center's premultiplied alpha.
    fragColor = vec4(red.a > 0.0 ? red.r * (center.a / red.a) : 0.0,
        center.g, blue.a > 0.0 ? blue.b * (center.a / blue.a) : 0.0, center.a);
}
