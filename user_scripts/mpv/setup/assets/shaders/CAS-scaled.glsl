// LICENSE
// =======
// Copyright (c) 2017-2019 Advanced Micro Devices, Inc. All rights reserved.
// -------
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation
// files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy,
// modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
// Software is furnished to do so, subject to the following conditions:
// -------
// The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
// Software.
// -------
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
// WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE AUTHORS OR
// COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
// ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// FidelityFX CAS v1.0.2 by AMD
// ported to mpv by agyild

// Changelog
// Optimized texture lookups for OpenGL 4.0+, DirectX 10+, and OpenGL ES 3.1+
// Changed rcp + mul operations to div for better clarity when CAS_GO_SLOWER is set to 1, since the compiler should automatically
// optimize those instructions anyway.
// Made it directly operate on LUMA plane, since the original shader was operating on LUMA by deriving it from RGB. This should
// cause a major increase in performance, especially on OpenGL 4.0+ renderers (4 texture lookups vs. 16)
// Removed transparency preservation mechanism since the alpha channel is a separate source plan than LUMA
// Added custom gamma curve support for relinearization
// Removed final blending between the original and the sharpened pixels since it was redundant
//
// Notes
// Per AMD's guidelines only upscales content up to 4x (e.g., 1080p -> 2160p, 720p -> 1440p etc.) and everything else in between,
// that means CAS will scale up to 4x at maximum, and any further scaling will be processed by mpv's scalers
//
// The filter is designed to run in linear light, and does have an optional relinerization and delinearization pass which
// assumes BT.1886 content by default. Do not forget to change SOURCE_TRC and TARGET_TRC variables depending
// on what kind of content the filter is running on. You might want to create seperate versions of the file with different
// colorspace values, and apply them via autoprofiles. Note that running in non-linear light will result in oversharpening.

//!HOOK LUMA
//!BIND HOOKED
//!DESC FidelityFX Upsampling and Sharpening v1.0.2 (Relinearization)
//!WHEN OUTPUT.w OUTPUT.h * LUMA.w LUMA.h * / 1.0 >

// User variables - Relinearization
// Compatibility
#define SOURCE_TRC 4 // Is needed to convert from source colorspace to linear light. 0 = None (Skip conversion), 1 = Rec709, 2 = PQ, 3 = sRGB, 4 = BT.1886, 5 = HLG, 6 = Custom
#define CUSTOM_GAMMA 2.2 // Custom power gamma curve to use if and when SOURCE_TRC is 6.

// Shader code

float From709(float rec709) {
	return max(min(rec709 / float(4.5), float(0.081)), pow((rec709 + float(0.099)) / float(1.099), float(1.0 / 0.45)));
}

float FromPq(float pq) {
	float p = pow(pq, float(0.0126833));
	return (pow(clamp(p - float(0.835938), 0.0, 1.0) / (float(18.8516) - float(18.6875) * p), float(6.27739)));
}

float FromSrgb(float srgb) {
	return max(min(srgb / 12.92, float(0.04045)), pow((srgb + float(0.055)) / float(1.055), float(2.4)));
}

float FromHlg(float hlg) {
	const float a = 0.17883277;
	const float b = 0.28466892;
	const float c = 0.55991073;

	float linear;
	if (hlg >= 0.0 && hlg <= 0.5) {
		linear = pow(hlg, 2.0) / 3.0;
	} else {
		linear = (exp((hlg - c) / a) + b) / 12.0;
	}

	return linear;
}

vec4 hook() {
	vec4 col = HOOKED_tex(HOOKED_pos);
	col.r = clamp(col.r, 0.0, 1.0);
#if (SOURCE_TRC == 1)
	col.r = From709(col.r);
#elif (SOURCE_TRC == 2)
	col.r = FromPq(col.r);
#elif (SOURCE_TRC == 3)
	col.r = FromSrgb(col.r);
#elif (SOURCE_TRC == 4)
	col.r = pow(col.r, float(2.4));
#elif (SOURCE_TRC == 5)
	col.r = FromHlg(col.r);
#elif (SOURCE_TRC == 6)
	col.r = pow(col.r, float(CUSTOM_GAMMA));
#endif
	return col;
}

//!HOOK LUMA
//!BIND HOOKED
//!DESC FidelityFX Upsampling and Sharpening v1.0.2
//!WHEN OUTPUT.w OUTPUT.h * LUMA.w LUMA.h * / 1.0 >
//!WIDTH OUTPUT.w OUTPUT.w LUMA.w 2 * < * LUMA.w 2 * OUTPUT.w LUMA.w 2 * > * + OUTPUT.w OUTPUT.w LUMA.w 2 * = * +
//!HEIGHT OUTPUT.h OUTPUT.h LUMA.h 2 * < * LUMA.h 2 * OUTPUT.h LUMA.h 2 * > * + OUTPUT.h OUTPUT.h LUMA.h 2 * = * +

// User variables - Upsampling and Sharpening
// Intensity
#define SHARPENING 0.0 // Adjusts the range the shader adapts to high contrast (0 is not all the way off).  Higher values = more high contrast sharpening. 0.0 to 1.0.

// Performance
#define CAS_BETTER_DIAGONALS 1 // If set to 0, drops certain math and texture lookup operations for better performance. This is only useful on pre-OpenGL 4.0 renderers and there is no need to disable it otherwise. 0 or 1.
#define CAS_GO_SLOWER 0 // If set to 1, disables the use of optimized approximate transcendental functions which might slightly increase accuracy in exchange of performance. 0 or 1.

// Compatibility
#define TARGET_TRC 4 // Is needed to convert from source colorspace to target colorspace. 0 = None (Skip conversion), 1 = Rec709, 2 = PQ, 3 = sRGB, 4 = BT.1886, 5 = HLG, 6 = Custom
#define CUSTOM_GAMMA 2.2 // Custom power gamma curve to use if and when TARGET_TRC is 6.

// Shader code

float To709(float linear) {
	return max(min(linear * float(4.5), float(0.018)), float(1.099) * pow(linear, float(0.45)) - float(0.099));
}

float ToPq(float linear) {
	float p = pow(linear, float(0.159302));
	return pow((float(0.835938) + float(18.8516) * p) / (float(1.0) + float(18.6875) * p), float(78.8438));
}

float ToSrgb(float linear) {
	return max(min(linear * float(12.92), float(0.0031308)), float(1.055) * pow(linear, float(0.41666)) - float(0.055));
}

float ToHlg(float linear) {
	const float a = 0.17883277;
	const float b = 0.28466892;
	const float c = 0.55991073;

	float hlg;
	if (linear <= 1.0 / 12.0) {
		hlg = sqrt(3.0 * linear);
	} else {
		hlg = a * log(12.0 * linear - b) + c;
	}

	return hlg;
}

#if (CAS_GO_SLOWER == 0)

float APrxLoSqrtF1(float a) {
	return uintBitsToFloat((floatBitsToUint(a) >> uint(1)) + uint(0x1fbc4639));
}

float APrxLoRcpF1(float a) {
	return uintBitsToFloat(uint(0x7ef07ebb) - floatBitsToUint(a));
}

float APrxMedRcpF1(float a) {
	float b = uintBitsToFloat(uint(0x7ef19fff) - floatBitsToUint(a));
	return b * (-b * a + float(2.0));
}

#endif

vec4 hook()
{
	// Scaling algorithm adaptively interpolates between nearest 4 results of the non-scaling algorithm.
	//  a b c d
	//  e f g h
	//  i j k l
	//  m n o p
	// Working these 4 results.
	//  +-----+-----+
	//  |     |     |
	//  |  f..|..g  |
	//  |  .  |  .  |
	//  +-----+-----+
	//  |  .  |  .  |
	//  |  j..|..k  |
	//  |     |     |
	//  +-----+-----+

	vec2 pp = HOOKED_pos * HOOKED_size - 0.5;
	vec2 fp = floor(pp);
	pp -= fp;

#if (defined(HOOKED_gather) && (__VERSION__ >= 400 || (GL_ES && __VERSION__ >= 310)))
	vec4 abef = HOOKED_gather(vec2((fp - vec2(0.5)) * HOOKED_pt), 0);

	float b = abef.z;
	float e = abef.x;
	float f = abef.y;

	vec4 cdgh = HOOKED_gather(vec2((fp + vec2(1.5, -0.5)) * HOOKED_pt), 0);

	float c = cdgh.w;
	float g = cdgh.x;
	float h = cdgh.y;

	vec4 ijmn = HOOKED_gather(vec2((fp + vec2(-0.5, 1.5)) * HOOKED_pt), 0);

	float i = ijmn.w;
	float j = ijmn.z;
	float n = ijmn.y;

	vec4 klop = HOOKED_gather(vec2((fp + vec2(1.5)) * HOOKED_pt), 0);

	float k = klop.w;
	float l = klop.z;
	float o = klop.x;

	#if (CAS_BETTER_DIAGONALS == 1)
		float a = abef.w;
		float d = cdgh.z;
		float m = ijmn.x;
		float p = klop.y;
	#endif
#else
	ivec2 sp = ivec2(fp);

	#if (CAS_BETTER_DIAGONALS == 1)
		float a = texelFetch(HOOKED_raw, sp + ivec2(-1, -1), 0).r * HOOKED_mul;
		float d = texelFetch(HOOKED_raw, sp + ivec2( 2, -1), 0).r * HOOKED_mul;
		float m = texelFetch(HOOKED_raw, sp + ivec2(-1,  2), 0).r * HOOKED_mul;
		float p = texelFetch(HOOKED_raw, sp + ivec2( 2,  2), 0).r * HOOKED_mul;
	#endif

	float b = texelFetch(HOOKED_raw, sp + ivec2( 0, -1), 0).r * HOOKED_mul;
	float e = texelFetch(HOOKED_raw, sp + ivec2(-1,  0), 0).r * HOOKED_mul;
	float f = texelFetch(HOOKED_raw, sp                , 0).r * HOOKED_mul;

	float c = texelFetch(HOOKED_raw, sp + ivec2( 1, -1), 0).r * HOOKED_mul;
	float g = texelFetch(HOOKED_raw, sp + ivec2( 1,  0), 0).r * HOOKED_mul;
	float h = texelFetch(HOOKED_raw, sp + ivec2( 2,  0), 0).r * HOOKED_mul;

	float i = texelFetch(HOOKED_raw, sp + ivec2(-1,  1), 0).r * HOOKED_mul;
	float j = texelFetch(HOOKED_raw, sp + ivec2( 0,  1), 0).r * HOOKED_mul;
	float n = texelFetch(HOOKED_raw, sp + ivec2( 0,  2), 0).r * HOOKED_mul;

	float k = texelFetch(HOOKED_raw, sp + ivec2( 1,  1), 0).r * HOOKED_mul;
	float l = texelFetch(HOOKED_raw, sp + ivec2( 2,  1), 0).r * HOOKED_mul;
	float o = texelFetch(HOOKED_raw, sp + ivec2( 1,  2), 0).r * HOOKED_mul;
#endif

	// Soft min and max.
	// These are 2.0x bigger (factored out the extra multiply).
	//  a b c             b
	//  e f g * 0.5  +  e f g * 0.5  [F]
	//  i j k             j

	float mnfL = min(min(b, min(e, f)), min(g, j));
	float mxfL = max(max(b, max(e, f)), max(g, j));

#if (CAS_BETTER_DIAGONALS == 1)
	float mnfL2 = min(min(mnfL, min(a, c)), min(i, k));
	mnfL += mnfL2;

	float mxfL2 = max(max(mxfL, max(a, c)), max(i, k));
	mxfL += mxfL2;
#endif

	//  b c d             c
	//  f g h * 0.5  +  f g h * 0.5  [G]
	//  j k l             k
	float mngL = min(min(c, min(f, g)), min(h, k));
	float mxgL = max(max(c, max(f, g)), max(h, k));
#if (CAS_BETTER_DIAGONALS == 1)
	float mngL2 = min(min(mngL, min(b, d)), min(j, l));
	mngL += mngL2;

	float mxgL2 = max(max(mxgL, max(b, d)), max(j, l));
	mxgL += mxgL2;
#endif

	//  e f g             f
	//  i j k * 0.5  +  i j k * 0.5  [J]
	//  m n o             n
	float mnjL  = min(min(f, min(i, j)), min(k, n));
	float mxjL  = max(max(f, max(i, j)), max(k, n));
#if (CAS_BETTER_DIAGONALS == 1)
	float mnjL2 = min(min(mnjL, min(e, g)), min(m, o));
	mnjL += mnjL2;

	float mxjL2 = max(max(mxjL, max(e, g)), max(m, o));
	mxjL += mxjL2;
#endif

	//  f g h             g
	//  j k l * 0.5  +  j k l * 0.5  [K]
	//  n o p             o
	float mnkL = min(min(g, min(j, k)), min(l, o));
	float mxkL = max(max(g, max(j, k)), max(l, o));
#if (CAS_BETTER_DIAGONALS == 1)
	float mnkL2 = min(min(mnkL, min(f, h)), min(n, p));
	mnkL += mnkL2;

	float mxkL2 = max(max(mxkL, max(f, h)), max(n, p));
	mxkL += mxkL2;
#endif

	// Smooth minimum distance to signal limit divided by smooth max.
	const float bdval = bool(CAS_BETTER_DIAGONALS) ? 2.0 : 1.0;
#if (CAS_GO_SLOWER == 1)
	float ampfL = clamp(min(mnfL, bdval - mxfL) / mxfL, 0.0, 1.0);
	float ampgL = clamp(min(mngL, bdval - mxgL) / mxgL, 0.0, 1.0);
	float ampjL = clamp(min(mnjL, bdval - mxjL) / mxjL, 0.0, 1.0);
	float ampkL = clamp(min(mnkL, bdval - mxkL) / mxkL, 0.0, 1.0);
#else
	float ampfL = clamp(min(mnfL, bdval - mxfL) * APrxLoRcpF1(mxfL), 0.0, 1.0);
	float ampgL = clamp(min(mngL, bdval - mxgL) * APrxLoRcpF1(mxgL), 0.0, 1.0);
	float ampjL = clamp(min(mnjL, bdval - mxjL) * APrxLoRcpF1(mxjL), 0.0, 1.0);
	float ampkL = clamp(min(mnkL, bdval - mxkL) * APrxLoRcpF1(mxkL), 0.0, 1.0);
#endif

	// Shaping amount of sharpening.
#if (CAS_GO_SLOWER == 1)
	ampfL = sqrt(ampfL);
	ampgL = sqrt(ampgL);
	ampjL = sqrt(ampjL);
	ampkL = sqrt(ampkL);
#else
	ampfL = APrxLoSqrtF1(ampfL);
	ampgL = APrxLoSqrtF1(ampgL);
	ampjL = APrxLoSqrtF1(ampjL);
	ampkL = APrxLoSqrtF1(ampkL);
#endif

	// Filter shape.
	//  0 w 0
	//  w 1 w
	//  0 w 0

	const float peak = -(mix(8.0, 5.0, clamp(SHARPENING, 0.0, 1.0)));
	float wfL = ampfL / peak;
	float wgL = ampgL / peak;
	float wjL = ampjL / peak;
	float wkL = ampkL / peak;

	// Blend between 4 results.
	//  s t
	//  u v
	float s = (1.0 - pp.x) * (1.0 - pp.y);
	float t = pp.x * (1.0 - pp.y);
	float u = (1.0 - pp.x) * pp.y;
	float v = pp.x * pp.y;

	// Thin edges to hide bilinear interpolation (helps diagonals).
	const float thinB = 0.03125; // 1.0 / 32.0

#if (CAS_GO_SLOWER == 1)
	s /= thinB + mxfL - mnfL;
	t /= thinB + mxgL - mngL;
	u /= thinB + mxjL - mnjL;
	v /= thinB + mxkL - mnkL;
#else
	s *= APrxLoRcpF1(thinB + mxfL - mnfL);
	t *= APrxLoRcpF1(thinB + mxgL - mngL);
	u *= APrxLoRcpF1(thinB + mxjL - mnjL);
	v *= APrxLoRcpF1(thinB + mxkL - mnkL);
#endif

	// Final weighting.
	//    b c
	//  e f g h
	//  i j k l
	//    n o
	//  _____  _____  _____  _____
	//         fs        gt
	//
	//  _____  _____  _____  _____
	//  fs      s gt  fs  t     gt
	//         ju        kv
	//  _____  _____  _____  _____
	//         fs        gt
	//  ju      u kv  ju  v     kv
	//  _____  _____  _____  _____
	//
	//         ju        kv
	float qbeL = wfL * s;
	float qchL = wgL * t;
	float qfL  = wgL * t + wjL * u + s;
	float qgL  = wfL * s + wkL * v + t;
	float qjL  = wfL * s + wkL * v + u;
	float qkL  = wgL * t + wjL * u + v;
	float qinL = wjL * u;
	float qloL = wkL * v;

	// Filter.
	vec4 pix = vec4(0.0, 0.0, 0.0, 1.0);
	float W = 2.0 * qbeL + 2.0 * qchL + 2.0 * qinL + 2.0 * qloL + qfL + qgL + qjL + qkL;
	pix.r = b * qbeL + e * qbeL + c * qchL + h * qchL + i * qinL + n * qinL + l * qloL + o * qloL + f * qfL + g * qgL + j * qjL + k * qkL;
#if (CAS_GO_SLOWER == 1)
	pix.r /= W;
#else
	pix.r *= APrxMedRcpF1(W);
#endif

	pix.r = clamp(pix.r, 0.0, 1.0);

#if (TARGET_TRC == 1)
	pix.r = To709(pix.r);
#elif (TARGET_TRC == 2)
	pix.r = ToPq(pix.r);
#elif (TARGET_TRC == 3)
	pix.r = ToSrgb(pix.r);
#elif (TARGET_TRC == 4)
	pix.r = pow(pix.r, float(1.0 / 2.4));
#elif (TARGET_TRC == 5)
	pix.r = ToHlg(pix.r);
#elif (TARGET_TRC == 6)
	pix.r = pow(pix.r, float(1.0 / CUSTOM_GAMMA));
#endif

	return pix;
}