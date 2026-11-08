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
// Artistic gravitational lens: a twisting image, dark core, and spectral rim.
const float TAU = 6.283185307179586;
const float LOOP_SECONDS = 20.0;
const float RADIUS_FRACTION = 0.42;
const float TWIST_RADIANS = 2.4;
const float DISPERSION_PX = 9.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 baseUV = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    if (pointer_hidden != 0 || any(lessThan(pointer_position, vec2(0.0)))
        || any(greaterThan(pointer_position, vec2(1.0)))) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }
    float scale = min(size.x, size.y) * RADIUS_FRACTION;
    vec2 delta = (v_texcoord - pointer_position) * size;
    float r = length(delta) / scale;
    if (r >= 1.0) {
        fragColor = textureLod(tex, baseUV, 0.0);
        return;
    }
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, 64.0, min(edge.x, edge.y));
    float weight = 1.0 - smoothstep(0.0, 1.0, r);
    float phase = mod(time, LOOP_SECONDS) * (TAU / LOOP_SECONDS);
    float twist = TWIST_RADIANS * weight * weight * (0.8 + 0.2 * sin(phase));
    float c = cos(twist);
    float s = sin(twist);
    vec2 warped = vec2(c * delta.x - s * delta.y, s * delta.x + c * delta.y)
        * (1.0 + 1.6 * weight * weight);
    vec2 uv = v_texcoord + (warped - delta) * edgeMask / size;
    float rim = 1.0 - smoothstep(0.0, 0.12, abs(r - 0.20));
    vec2 split = delta / max(length(delta), 1.0)
        * DISPERSION_PX * (weight * 0.25 + rim) * edgeMask / size;
    vec4 center = textureLod(tex, clamp(uv, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 red = textureLod(tex, clamp(uv + split, halfTexel, 1.0 - halfTexel), 0.0);
    vec4 blue = textureLod(tex, clamp(uv - split, halfTexel, 1.0 - halfTexel), 0.0);
    vec3 rgb = vec3(red.a > 0.0 ? red.r * (center.a / red.a) : 0.0,
        center.g, blue.a > 0.0 ? blue.b * (center.a / blue.a) : 0.0);
    rgb *= mix(1.0, smoothstep(0.07, 0.19, r), edgeMask);
    rgb = mix(rgb, vec3(0.95, 0.40, 0.08) * center.a, rim * 0.28 * edgeMask);
    fragColor = vec4(rgb, center.a);
}
