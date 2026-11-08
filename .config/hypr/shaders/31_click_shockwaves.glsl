#version 320 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
// Hyprland's native press history: newest first; times are ages in seconds.
uniform vec2 pointer_pressed_positions[32];
uniform float pointer_pressed_times[32];
layout(location = 0) out vec4 fragColor;

// dusky: animated
// Each click or touchscreen press launches a soft expanding wave packet.
const float TAU = 6.283185307179586;
const int MAX_WAVES = 6; // Process only the six most recent native history slots.
const float LIFETIME_SECONDS = 2.8;
const float SPEED_PX = 260.0;
const float WIDTH_PX = 65.0;
const float WAVELENGTH_PX = 80.0;
const float AMPLITUDE_PX = 7.0;
const float EDGE_FADE_PX = 64.0;

void main() {
    vec2 size = vec2(textureSize(tex, 0));
    vec2 halfTexel = 0.5 / size;
    vec2 offset = vec2(0.0);
    for (int i = 0; i < MAX_WAVES; ++i) {
        float age = pointer_pressed_times[i];
        // Older history slots cannot contribute either. This is uniform per draw.
        if (age >= LIFETIME_SECONDS)
            break;
        vec2 origin = pointer_pressed_positions[i];
        if (any(lessThan(origin, vec2(0.0))) || any(greaterThan(origin, vec2(1.0))))
            continue;
        vec2 delta = (v_texcoord - origin) * size;
        float radius = length(delta);
        float front = radius - age * SPEED_PX;
        float packet = 1.0 - smoothstep(0.0, WIDTH_PX, abs(front));
        float birth = smoothstep(0.0, 0.12, age);
        float decay = 1.0 - smoothstep(0.2, LIFETIME_SECONDS, age);
        float centerMask = smoothstep(0.0, 10.0, radius);
        offset += delta / max(radius, 1.0) * sin(TAU * front / WAVELENGTH_PX)
            * packet * birth * decay * centerMask * AMPLITUDE_PX;
    }
    // Soft-limit overlapping packets without changing their direction.
    offset /= max(1.0, length(offset) / (AMPLITUDE_PX * 2.0));
    vec2 edge = min(v_texcoord, 1.0 - v_texcoord) * size;
    float edgeMask = smoothstep(0.0, EDGE_FADE_PX, min(edge.x, edge.y));
    vec2 uv = clamp(v_texcoord + offset * edgeMask / size, halfTexel, 1.0 - halfTexel);
    fragColor = textureLod(tex, uv, 0.0);
}
