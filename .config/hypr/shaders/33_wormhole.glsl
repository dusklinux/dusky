#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// The desktop becomes concentric, rotating walls of an imaginary tunnel.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 24.0;
const float SPIRAL_STRENGTH = 1.8;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float shortSide = min(size.x, size.y);
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 center = vec2(0.5) + vec2(sin(phase), cos(phase * 2.0)) * 0.055;
    vec2 p = (v_texcoord - center) * size / shortSide;
    float radius = length(p);
    float depth = -log(max(radius, 0.012));
    float angle = SPIRAL_STRENGTH * depth + phase;
    float c = cos(angle);
    float s = sin(angle);
    vec2 direction = vec2(c * p.x - s * p.y, s * p.x + c * p.y)
        / max(radius, 0.012);
    float tunnelRadius = 0.28 + 0.20 * sin(depth * 5.0 - phase * 4.0);
    vec2 mapped = center + direction * tunnelRadius * shortSide / size;
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 64.0, min(edge.x, edge.y));
    vec2 uv = mix(v_texcoord, mapped, edgeMask);
    vec4 color = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    float tunnelLight = 0.72 + 0.28 * (0.5 + 0.5 * cos(depth * 5.0 - phase * 4.0));
    float core = smoothstep(0.012, 0.055, radius);
    color.rgb *= mix(1.0, core * tunnelLight, edgeMask);
    fragColor = color;
}
