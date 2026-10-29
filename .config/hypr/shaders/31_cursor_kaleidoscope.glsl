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
// A pointer-following portal of six mirrored sectors; feathered into the desktop.
const float TAU = 6.283185307179586;
const float RADIUS_PX = 240.0;
const float FEATHER_PX = 32.0;
const float SECTORS = 6.0;
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float radius = length(delta);
    float visible = float(pointer_hidden == 0
        && all(greaterThanEqual(pointer_position, vec2(0.0)))
        && all(lessThanEqual(pointer_position, vec2(1.0))));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    float mask = (1.0 - smoothstep(RADIUS_PX - FEATHER_PX, RADIUS_PX, radius))
        * visible * edgeMask;
    vec4 original = textureLod(tex, clamp(v_texcoord, halfTexel, 1.0 - halfTexel), 0.0);
    // Most of the screen needs neither angular folding nor a second sample.
    if (mask <= 0.0) {
        fragColor = original;
        return;
    }
    float phase = mod(time, 32.0) * (TAU / 32.0);
    // atan(0, 0) is undefined; the center's angle is visually immaterial.
    float angle = radius > 0.001 ? atan(delta.y, delta.x) : 0.0;
    float sector = TAU / SECTORS;
    float folded = abs(mod(angle - phase + sector * 0.5, sector) - sector * 0.5);
    vec2 mapped = vec2(cos(folded + phase), sin(folded + phase)) * radius;
    // Fade displacement at the display edge as well as fading the portal itself.
    vec2 portalUV = v_texcoord + (mapped - delta) * edgeMask / size;
    vec4 portal = textureLod(tex, clamp(portalUV, halfTexel, 1.0 - halfTexel), 0.0);
    fragColor = mix(original, portal, mask);
}
