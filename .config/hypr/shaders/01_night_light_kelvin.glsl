#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

// Full-color circadian Night Light filter using Planckian blackbody locus RGB.
// Input/output: SDR, opaque or premultiplied alpha[cite: 13].

// ============================================================================
// CONFIGURATION (Tunable Parameters)
// ============================================================================
const float KELVIN_TEMP        = 3400.0; // Target color temperature in Kelvin [1800.0 = candle, 3400.0 = warm night, 6500.0 = daylight]
const float FILTER_STRENGTH    = 0.72;   // Blend strength of the Kelvin shift [0.0 = off, 1.0 = full blackbody]
const float LUMINANCE_PRESERVE = 0.85;   // Restores perceived brightness lost when attenuating blue/green [0.0, 1.0]
const float DIMMING_FACTOR     = 0.96;   // Overall comfortable eye-strain brightness multiplier [0.5, 1.0]
// ============================================================================

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

vec3 straightRGB(vec4 color) {
    if (color.a <= 0.0) {
        return vec3(0.0);
    }
    return clamp(color.rgb / color.a, 0.0, 1.0);
}

vec3 kelvinToRGB(float kelvin) {
    float temp = clamp(kelvin, 1000.0, 12000.0) / 100.0;
    float r;
    float g;
    float b;

    if (temp <= 66.0) {
        r = 1.0;
        g = clamp((99.4708025861 * log(max(temp, 1.0)) - 161.1195681661) / 255.0, 0.0, 1.0);
        if (temp <= 19.0) {
            b = 0.0;
        } else {
            b = clamp((138.5177312231 * log(max(temp - 10.0, 1.0)) - 305.0447927307) / 255.0, 0.0, 1.0);
        }
    } else {
        r = clamp((329.698727446 * pow(temp - 60.0, -0.1332047592)) / 255.0, 0.0, 1.0);
        g = clamp((288.1221695283 * pow(temp - 60.0, -0.0755148492)) / 255.0, 0.0, 1.0);
        b = 1.0;
    }

    return vec3(r, g, b);
}

void main() {
    vec2 halfTexel = 0.5 / vec2(textureSize(tex, 0));
    vec2 uv = clamp(v_texcoord, halfTexel, 1.0 - halfTexel);
    vec4 sampleColor = textureLod(tex, uv, 0.0);
    vec3 color = straightRGB(sampleColor);

    vec3 warmFilter = kelvinToRGB(KELVIN_TEMP);
    vec3 filtered = color * warmFilter;

    float origLuma = dot(color, LUMA);
    float newLuma = max(dot(filtered, LUMA), 0.0001);
    vec3 lumaRestored = filtered * mix(1.0, origLuma / newLuma, LUMINANCE_PRESERVE);

    vec3 result = mix(color, lumaRestored, FILTER_STRENGTH) * DIMMING_FACTOR;
    result = clamp(result, 0.0, 1.0);

    fragColor = vec4(result * sampleColor.a, sampleColor.a);
}
