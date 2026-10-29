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
// A bending vertical rift: opposite sides slide apart around the pointer.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 16.0;
const float WIDTH_PX = 180.0;
const float SLIDE_PX = 150.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    if (pointer_hidden != 0 || any(lessThan(pointer_position, vec2(0.0)))
        || any(greaterThan(pointer_position, vec2(1.0)))) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 delta = (v_texcoord - pointer_position) * size;
    float bend = 24.0 * sin(delta.y / 95.0 + phase * 3.0)
        + 12.0 * sin(delta.y / 43.0 - phase * 5.0);
    float x = delta.x - bend;
    float horizontal = 1.0 - smoothstep(0.0, WIDTH_PX, abs(x));
    float vertical = 1.0 - smoothstep(240.0, 680.0, abs(delta.y));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 72.0, min(edge.x, edge.y));
    float mask = horizontal * horizontal * vertical * edgeMask;
    float side = x < 0.0 ? -1.0 : 1.0;
    vec2 offset = vec2(-side * 48.0,
        side * SLIDE_PX * (0.8 + 0.2 * sin(phase * 2.0))) * mask;
    vec2 uv = clamp(v_texcoord + offset / size, halfTexel, 1.0 - halfTexel);
    vec4 color = textureLod(tex, uv, 0.0);
    float rim = (1.0 - smoothstep(0.0, 5.0, abs(x))) * vertical * edgeMask;
    color.rgb = mix(color.rgb, vec3(0.65, 0.14, 0.95) * color.a, rim * 0.8);
    fragColor = color;
}
