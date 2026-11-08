#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
uniform float time;
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Three moving spheres carry inverted reflections of the desktop.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 40.0;
const float RADIUS_FRACTION = 0.155;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    float radius = min(size.x, size.y) * RADIUS_FRACTION;
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    vec2 bestDelta = vec2(0.0);
    float nearest = 2.0;
    for (int i = 0; i < 3; ++i) {
        float orbit = phase + float(i) * (TAU / 3.0);
        // Equal angular spacing on one ellipse keeps the spheres separated.
        vec2 center = vec2(0.5 + 0.30 * cos(orbit), 0.5 + 0.30 * sin(orbit));
        vec2 delta = (v_texcoord - center) * size;
        float r = length(delta) / radius;
        if (r < nearest) {
            nearest = r;
            bestDelta = delta;
        }
    }
    vec4 original = textureLod(tex, clamp(v_texcoord, halfTexel, 1.0 - halfTexel), 0.0);
    if (nearest >= 1.0) {
        fragColor = original;
        return;
    }
    float depth = sqrt(max(0.0, 1.0 - nearest * nearest));
    vec2 mappedDelta = -bestDelta * (0.4 + 0.6 * depth);
    vec2 uv = v_texcoord + (mappedDelta - bestDelta) / size;
    vec4 sphere = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec3 normal = vec3(bestDelta / radius, depth);
    float highlight = pow(max(0.0, dot(normal, normalize(vec3(-0.4, -0.5, 1.0)))), 32.0);
    float rim = smoothstep(0.75, 1.0, nearest);
    sphere.rgb = mix(sphere.rgb * (0.65 + 0.35 * depth),
        vec3(0.30, 0.75, 0.95) * sphere.a, rim * 0.40);
    sphere.rgb = mix(sphere.rgb, vec3(sphere.a), highlight * 0.65);
    float mask = 1.0 - smoothstep(0.96, 1.0, nearest);
    fragColor = mix(original, sphere, mask);
}
