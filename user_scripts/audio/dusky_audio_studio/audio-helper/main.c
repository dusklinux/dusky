/* Dusky Audio Studio: PipeWire virtual microphone and playback sink with DSP.
 *
 * IPC: line-based commands on stdin, fixed-size binary audio frames on stdout
 *      (see protocol.h). Telemetry rate ~60 Hz; audio runs at 48 kHz.
 *
 * Threading model: one PipeWire main thread runs the loop, timers, and stdin
 * reader. Audio callbacks may run on separate PipeWire data loops. Parameters
 * and bounded rings are exchanged with C11 atomics without callback waits.
 *
 * RNNoise output is 960 samples behind input at 48 kHz. Frame collection
 * adds 479 samples, giving 1,439 samples (~30 ms) in this streaming path;
 * PipeWire buffers and device paths add further latency.
 *
 * License: GPL-3.0 (same as parent project). RNNoise links from the system.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <pipewire/pipewire.h>
#include <pipewire/stream.h>
#include <spa/param/audio/format-utils.h>
#include <spa/param/latency-utils.h>

#include "protocol.h"
#include <rnnoise.h>

/* ---------------------------------------------------------------------------
 *   Constants
 * ------------------------------------------------------------------------- */

#define SAMPLE_RATE 48000
#define RNN_FRAME 480 /* rnnoise's fixed frame size at 48 kHz */
#define RING_CAP 8192 /* per-direction ring buffer (samples)  */
#define FRAME_EMIT_HZ 60
#define FRAME_EMIT_SAMPLES (SAMPLE_RATE / FRAME_EMIT_HZ)

/* ---------------------------------------------------------------------------
 *   Atomic parameter bag (UI -> RT thread)
 * ------------------------------------------------------------------------- */

struct eq_band
{
    _Atomic int type;         /* 0=peak,1=lowshelf,2=highshelf,3=hp,4=lp,5=notch */
    _Atomic int freq_hz;      /* 20..20000 */
    _Atomic int q_mille;      /* Q * 1000  (e.g. 707 = 0.707) */
    _Atomic int gain_centidb; /* gain in dB * 100 */
};

static struct
{
    _Atomic int rnnoise_on;
    _Atomic int out_rnnoise_on; /* Two-Way Output Noise Cancellation (Speakers/Headphones) */
    _Atomic int out_rnn_aggressiveness; /* 0..1000 */
    _Atomic int eq_on;
    _Atomic int delay_on;
    _Atomic int reverb_on;
    _Atomic int monitor_on;
    _Atomic int vocoder_on;
    _Atomic int vocoder_mix;        /* per-mille dry/wet */
    _Atomic int vocoder_carrier_hz; /* fundamental of internal sawtooth (fixed mode) */
    _Atomic int vocoder_attack_ms;
    _Atomic int vocoder_release_ms;
    _Atomic int vocoder_detune;      /* per-mille; spreads two carriers apart */
    _Atomic int vocoder_follow;      /* 0=fixed pitch, 1=track voice pitch */
    _Atomic int vocoder_shift_semis; /* -24..+24 transpose applied when follow=1 */

    /* Master output gain applied at the virtual source stream (NOT the
     * monitor) so apps recording from "Dusky Mic" hear it
     * at the user-chosen level. Per-mille: 0=mute, 1000=unity, 2000=+6 dB.
     * Boosts above unity may exceed digital full scale. */
    _Atomic int master_vol_mille;

    /* Post-EQ output gain in centi-dB applied immediately after the biquad
     * cascade (before delay / reverb). Drives the "drag the response curve
     * vertically" UI gesture: a uniform translation of the line corresponds
     * to a pure gain offset, independent of which bands are active. Allowed
     * range -3600..+3600 centi-dB (= +/- 36 dB) - wide enough that the UI
     * can drag the line above the +18 dB chart bounds without saturating
     * the parameter. */
    _Atomic int eq_gain_centidb;

    /* RNNoise-stage aggressiveness, per-mille (0..1000). Controls a soft
     * gate applied after the model output when its VAD is below the
     * close threshold. 0 = no extra suppression (raw RNNoise output);
     * 1000 = up to -24 dB of residual attenuation during silence. The
     * gate ramps in/out smoothly with a 250 ms hangover so trailing
     * consonants are preserved. Default 700 is a good "Zoom-like" feel:
     * speech is unchanged, room tone drops to near-inaudible. */
    _Atomic int rnn_aggressiveness;

    /* Voice-effects parameters used by the vocoder presets to nail the
     * "Darth Vader" / "Chipmunk" / "Cher" / "Robot Phone" characters.
     * The vocoder alone cannot produce those - they need pitch shifting,
     * pitch correction and / or amplitude quantisation in series. */
    _Atomic int pitch_shift_centisemis; /* -2400..+2400 = -24..+24 st, 1/100 st step */
    _Atomic int autotune_on;            /* 1 = override pitch_shift with chromatic snap */
    _Atomic int bitcrush_bits;          /* 0 = bypass; 1..15 = quantise to this many bits */
    _Atomic int bitcrush_downsample;    /* >=1; sample-and-hold N output samples per input */

    /* Per-preset "Matrix-ness", per-mille. Interpolates the vocoder carrier
     * between a CLEAN saw+square stack (intensity 0 - Kraftwerk, Hawking,
     * any "pure analog vocoder" preset) and the FAT/CRUNCHY/RING-MODULATED
     * stack we use for the Matrix Agent preset (intensity 1000). This is
     * what distinguishes Kraftwerk from Matrix Agent even with similar
     * envelope and detune settings: Kraftwerk gets a clean carrier with
     * no ring mod, Matrix Agent gets the full metallic treatment. */
    _Atomic int matrix_intensity_mille;

    /* Voice-stage band-pass filter. Two single biquads (HPF + LPF) wrapped
     * around the post-vocoder signal so presets can dial in the telephone
     * band (300-3400 Hz for "Robot Phone") or just a muffled low-pass
     * for "Darth Vader". 0 = bypass that biquad. */
    _Atomic int voice_bpf_hpf_hz; /* 0..2000 */
    _Atomic int voice_bpf_lpf_hz; /* 0..20000 */

    /* Stutter gate: square-wave amplitude modulation at speech rate. The
     * Cylon's "By Your Command" cadence is the canonical example - a
     * voice chopped 4..8 times per second produces an unmistakable
     * "machine speaking" rhythm. Duty cycle controls how much of each
     * period is "on" (500 = exact square; lower = shorter on, longer off). */
    _Atomic int voice_stutter_hz;         /* 0 = bypass; 1..20 typical */
    _Atomic int voice_stutter_duty_mille; /* 0..1000, default 500 */

    /* Autotune target frequency in Hz. 0 = chromatic snap (Cher / T-Pain
     * style: pitch snaps to nearest equal-tempered semitone). >0 = snap
     * to that one fixed pitch always - true monotone, the DECtalk Stephen
     * Hawking style. */
    _Atomic int voice_autotune_target_hz;

    struct eq_band eq[GHA_EQ_BANDS];

    /* Output Playback Stereo Parametric Equalizer */
    _Atomic int out_eq_on;
    _Atomic int out_eq_gain_centidb;
    struct eq_band out_eq[GHA_EQ_BANDS];

    /* Output Playback Stereo Voice FX & Modulation */
    _Atomic int out_vocoder_on;
    _Atomic int out_vocoder_mix;
    _Atomic int out_vocoder_carrier_hz;
    _Atomic int out_vocoder_attack_ms;
    _Atomic int out_vocoder_release_ms;
    _Atomic int out_vocoder_detune;
    _Atomic int out_vocoder_follow;
    _Atomic int out_vocoder_shift_semis;
    _Atomic int out_matrix_intensity_mille;
    _Atomic int out_pitch_shift_centisemis;
    _Atomic int out_autotune_on;
    _Atomic int out_voice_autotune_target_hz;
    _Atomic int out_bitcrush_bits;
    _Atomic int out_bitcrush_downsample;
    _Atomic int out_voice_bpf_hpf_hz;
    _Atomic int out_voice_bpf_lpf_hz;
    _Atomic int out_voice_stutter_hz;
    _Atomic int out_voice_stutter_duty_mille;

    /* Output Playback Stereo Delay & Reverb */
    _Atomic int out_delay_on;
    _Atomic int out_delay_ms;
    _Atomic int out_delay_feedback;
    _Atomic int out_delay_mix;

    _Atomic int out_reverb_on;
    _Atomic int out_reverb_room;
    _Atomic int out_reverb_damp;
    _Atomic int out_reverb_width;
    _Atomic int out_reverb_mix;

    _Atomic int delay_ms;       /* 0..1000 */
    _Atomic int delay_feedback; /* 0..1000 (per-mille) */
    _Atomic int delay_mix;      /* 0..1000 (per-mille) */

    _Atomic int reverb_room;  /* 0..1000 */
    _Atomic int reverb_damp;  /* 0..1000 */
    _Atomic int reverb_width; /* 0..1000 */
    _Atomic int reverb_mix;   /* 0..1000 */
} g_params;

static void params_init(void)
{
    atomic_store(&g_params.rnnoise_on, 1);
    atomic_store(&g_params.out_rnnoise_on, 0); /* Default OFF */
    atomic_store(&g_params.out_rnn_aggressiveness, 700);
    atomic_store(&g_params.out_eq_on, 0);      /* Output EQ default OFF */
    atomic_store(&g_params.out_eq_gain_centidb, 0);
    atomic_store(&g_params.eq_on, 0);
    atomic_store(&g_params.delay_on, 0);
    atomic_store(&g_params.reverb_on, 0);
    atomic_store(&g_params.monitor_on, 0);

    atomic_store(&g_params.out_vocoder_on, 0);
    atomic_store(&g_params.out_vocoder_mix, 700);
    atomic_store(&g_params.out_vocoder_carrier_hz, 110);
    atomic_store(&g_params.out_vocoder_attack_ms, 5);
    atomic_store(&g_params.out_vocoder_release_ms, 30);
    atomic_store(&g_params.out_vocoder_detune, 20);
    atomic_store(&g_params.out_vocoder_follow, 1);
    atomic_store(&g_params.out_vocoder_shift_semis, 0);
    atomic_store(&g_params.out_matrix_intensity_mille, 0);
    atomic_store(&g_params.out_pitch_shift_centisemis, 0);
    atomic_store(&g_params.out_autotune_on, 0);
    atomic_store(&g_params.out_voice_autotune_target_hz, 0);
    atomic_store(&g_params.out_bitcrush_bits, 0);
    atomic_store(&g_params.out_bitcrush_downsample, 1);
    atomic_store(&g_params.out_voice_bpf_hpf_hz, 0);
    atomic_store(&g_params.out_voice_bpf_lpf_hz, 0);
    atomic_store(&g_params.out_voice_stutter_hz, 0);
    atomic_store(&g_params.out_voice_stutter_duty_mille, 500);

    atomic_store(&g_params.out_delay_on, 0);
    atomic_store(&g_params.out_delay_ms, 250);
    atomic_store(&g_params.out_delay_feedback, 350);
    atomic_store(&g_params.out_delay_mix, 300);

    atomic_store(&g_params.out_reverb_on, 0);
    atomic_store(&g_params.out_reverb_room, 700);
    atomic_store(&g_params.out_reverb_damp, 500);
    atomic_store(&g_params.out_reverb_width, 800);
    atomic_store(&g_params.out_reverb_mix, 350);

    /* Default EQ: gentle voice shaping (similar to user's existing
     * easyeffects preset). Extra parking slots at 250 / 1500 / 6000 Hz are
     * idle by default and let the user shape mud / body / brightness. */
    struct
    {
        int type;
        int hz;
        int q;
        int g;
    } defaults[GHA_EQ_BANDS] = {
        {0, 80, 707, 0},      /* peak 80 Hz */
        {1, 120, 707, 300},   /* low-shelf +3 dB */
        {0, 250, 1000, 0},    /* peak idle (mud control) */
        {0, 400, 1000, -200}, /* peak -2 dB at 400 (mud cut) */
        {0, 1500, 1000, 0},   /* peak idle (body control) */
        {0, 3500, 700, 300},  /* peak +3 dB at 3.5k (presence) */
        {0, 6000, 1000, 0},   /* peak idle (brightness control) */
        {2, 9000, 700, 200},  /* high-shelf +2 dB */
        {0, 12000, 1000, 0},  /* peak idle (air) */
    };
    for (int i = 0; i < GHA_EQ_BANDS; i++)
    {
        atomic_store(&g_params.eq[i].type, defaults[i].type);
        atomic_store(&g_params.eq[i].freq_hz, defaults[i].hz);
        atomic_store(&g_params.eq[i].q_mille, defaults[i].q);
        atomic_store(&g_params.eq[i].gain_centidb, defaults[i].g);

        atomic_store(&g_params.out_eq[i].type, defaults[i].type);
        atomic_store(&g_params.out_eq[i].freq_hz, defaults[i].hz);
        atomic_store(&g_params.out_eq[i].q_mille, defaults[i].q);
        atomic_store(&g_params.out_eq[i].gain_centidb, 0); /* Output EQ flat 0 dB */
    }

    atomic_store(&g_params.delay_ms, 250);
    atomic_store(&g_params.delay_feedback, 350);
    atomic_store(&g_params.delay_mix, 300);

    atomic_store(&g_params.reverb_room, 700); /* deep by default per spec */
    atomic_store(&g_params.reverb_damp, 500);
    atomic_store(&g_params.reverb_width, 800);
    atomic_store(&g_params.reverb_mix, 350);

    atomic_store(&g_params.vocoder_on, 0);
    atomic_store(&g_params.vocoder_mix, 700);        /* 70% wet sounds robotic */
    atomic_store(&g_params.vocoder_carrier_hz, 110); /* low male pitch */
    atomic_store(&g_params.vocoder_attack_ms, 5);
    atomic_store(&g_params.vocoder_release_ms, 30);
    atomic_store(&g_params.vocoder_detune, 20); /* mild stereo detune */
    /* Pitch follow ON by default - makes the vocoder actually intelligible
     * because the robot sings at the speaker's pitch. shift=0 means "same
     * pitch as voice". Users wanting the classic constant-pitch Kraftwerk
     * sound switch follow off. */
    atomic_store(&g_params.vocoder_follow, 1);
    atomic_store(&g_params.vocoder_shift_semis, 0);

    /* Master volume defaults to unity (1000 = 100%). Range 0..2000. */
    atomic_store(&g_params.master_vol_mille, 1000);

    /* Post-EQ gain defaults to 0 dB so the existing chain behaviour is
     * unchanged until the user drags the response line. */
    atomic_store(&g_params.eq_gain_centidb, 0);

    /* RNNoise aggressiveness defaults to 1000 per-mille = 100% full suppression. */
    atomic_store(&g_params.rnn_aggressiveness, 1000);

    /* Voice effects default to bypass so a fresh install matches the
     * "raw vocoder" feel the previous build had. Presets stamp these. */
    atomic_store(&g_params.pitch_shift_centisemis, 0);
    atomic_store(&g_params.autotune_on, 0);
    atomic_store(&g_params.bitcrush_bits, 0);
    atomic_store(&g_params.bitcrush_downsample, 1);

    /* Matrix intensity defaults to 500 (halfway) so a fresh install hears
     * a moderate ring-mod / sub-osc colour right out of the box. Presets
     * push it to 0 for clean carriers, 1000 for full Matrix character. */
    atomic_store(&g_params.matrix_intensity_mille, 500);

    /* Voice band-pass / stutter / monotone-autotune all default to bypass. */
    atomic_store(&g_params.voice_bpf_hpf_hz, 0);
    atomic_store(&g_params.voice_bpf_lpf_hz, 0);
    atomic_store(&g_params.voice_stutter_hz, 0);
    atomic_store(&g_params.voice_stutter_duty_mille, 500);
    atomic_store(&g_params.voice_autotune_target_hz, 0);
}

/* ---------------------------------------------------------------------------
 *   DSP: Biquad (RBJ cookbook) for parametric EQ
 * ------------------------------------------------------------------------- */

struct biquad
{
    float b0, b1, b2, a1, a2;
    float z1, z2;
};

static void biquad_design(struct biquad *bq, int type, float fs, float f0,
                          float q, float gain_db)
{
    float A = powf(10.0f, gain_db / 40.0f);
    float w0 = 2.0f * (float)M_PI * f0 / fs;
    float cw = cosf(w0);
    float sw = sinf(w0);
    float alpha = sw / (2.0f * q);

    float b0, b1, b2, a0, a1, a2;

    switch (type)
    {
    case 1: /* low-shelf */
        b0 = A * ((A + 1) - (A - 1) * cw + 2 * sqrtf(A) * alpha);
        b1 = 2 * A * ((A - 1) - (A + 1) * cw);
        b2 = A * ((A + 1) - (A - 1) * cw - 2 * sqrtf(A) * alpha);
        a0 = (A + 1) + (A - 1) * cw + 2 * sqrtf(A) * alpha;
        a1 = -2 * ((A - 1) + (A + 1) * cw);
        a2 = (A + 1) + (A - 1) * cw - 2 * sqrtf(A) * alpha;
        break;
    case 2: /* high-shelf */
        b0 = A * ((A + 1) + (A - 1) * cw + 2 * sqrtf(A) * alpha);
        b1 = -2 * A * ((A - 1) + (A + 1) * cw);
        b2 = A * ((A + 1) + (A - 1) * cw - 2 * sqrtf(A) * alpha);
        a0 = (A + 1) - (A - 1) * cw + 2 * sqrtf(A) * alpha;
        a1 = 2 * ((A - 1) - (A + 1) * cw);
        a2 = (A + 1) - (A - 1) * cw - 2 * sqrtf(A) * alpha;
        break;
    case 3: /* high-pass */
        b0 = (1 + cw) / 2;
        b1 = -(1 + cw);
        b2 = (1 + cw) / 2;
        a0 = 1 + alpha;
        a1 = -2 * cw;
        a2 = 1 - alpha;
        break;
    case 4: /* low-pass */
        b0 = (1 - cw) / 2;
        b1 = 1 - cw;
        b2 = (1 - cw) / 2;
        a0 = 1 + alpha;
        a1 = -2 * cw;
        a2 = 1 - alpha;
        break;
    case 5: /* notch */
        b0 = 1;
        b1 = -2 * cw;
        b2 = 1;
        a0 = 1 + alpha;
        a1 = -2 * cw;
        a2 = 1 - alpha;
        break;
    case 6: /* band-pass (constant 0 dB peak gain). RBJ cookbook BPF form
             * favoured by classical vocoders: only passes a narrow band
             * around f0, full reject elsewhere. The vocoder analyser +
             * synthesiser banks need this; a peak filter does NOT work
             * because it lets the rest of the spectrum through. */
        b0 = alpha;
        b1 = 0;
        b2 = -alpha;
        a0 = 1 + alpha;
        a1 = -2 * cw;
        a2 = 1 - alpha;
        break;
    case 0: /* peak (default) */
    default:
        b0 = 1 + alpha * A;
        b1 = -2 * cw;
        b2 = 1 - alpha * A;
        a0 = 1 + alpha / A;
        a1 = -2 * cw;
        a2 = 1 - alpha / A;
        break;
    }

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static inline float biquad_tick(struct biquad *bq, float x)
{
    /* Transposed direct form II */
    float y = bq->b0 * x + bq->z1;
    bq->z1 = bq->b1 * x - bq->a1 * y + bq->z2;
    bq->z2 = bq->b2 * x - bq->a2 * y;
    return y;
}

/* ---------------------------------------------------------------------------
 *   DSP: Delay line (mono, max 1 s)
 * ------------------------------------------------------------------------- */

#define DELAY_MAX_SAMPLES (SAMPLE_RATE) /* 1 s */

struct delay_state
{
    float buf[DELAY_MAX_SAMPLES];
    int write;
};

static inline float delay_tick(struct delay_state *d, float x,
                               int delay_samples, float feedback, float mix)
{
    if (delay_samples < 1)
        delay_samples = 1;
    if (delay_samples > DELAY_MAX_SAMPLES - 1)
        delay_samples = DELAY_MAX_SAMPLES - 1;

    int read = d->write - delay_samples;
    if (read < 0)
        read += DELAY_MAX_SAMPLES;
    float wet = d->buf[read];
    d->buf[d->write] = x + wet * feedback;
    d->write = (d->write + 1) % DELAY_MAX_SAMPLES;
    return x * (1.0f - mix) + wet * mix;
}

/* ---------------------------------------------------------------------------
 *   DSP: Schroeder reverb (deep, 4 combs + 2 allpasses, classic Freeverb-lite)
 * ------------------------------------------------------------------------- */

#define COMB_COUNT 4
#define APASS_COUNT 2

static const int comb_lens[COMB_COUNT] = {1557, 1617, 1491, 1422};
static const int apass_lens[APASS_COUNT] = {225, 556};

struct comb_state
{
    float buf[2048];
    int idx;
    float filterstore;
};
struct apass_state
{
    float buf[1024];
    int idx;
};

struct reverb_state
{
    struct comb_state combs[COMB_COUNT];
    struct apass_state apasses[APASS_COUNT];
};

static inline float comb_tick(struct comb_state *c, int len, float x,
                              float feedback, float damp)
{
    float out = c->buf[c->idx];
    c->filterstore = (out * (1.0f - damp)) + (c->filterstore * damp);
    c->buf[c->idx] = x + c->filterstore * feedback;
    c->idx = (c->idx + 1) % len;
    return out;
}

static inline float apass_tick(struct apass_state *a, int len, float x)
{
    float buf_out = a->buf[a->idx];
    float out = -x + buf_out;
    a->buf[a->idx] = x + buf_out * 0.5f;
    a->idx = (a->idx + 1) % len;
    return out;
}

static inline float reverb_tick(struct reverb_state *r, float x,
                                float room, float damp, float width, float mix)
{
    float feedback = 0.28f + room * 0.7f; /* 0.28..0.98 */
    float wet = 0.0f;
    for (int i = 0; i < COMB_COUNT; i++)
        wet += comb_tick(&r->combs[i], comb_lens[i], x, feedback, damp);
    wet *= (1.0f / COMB_COUNT);
    for (int i = 0; i < APASS_COUNT; i++)
        wet = apass_tick(&r->apasses[i], apass_lens[i], wet);
    wet *= width;
    return x * (1.0f - mix) + wet * mix;
}

/* ---------------------------------------------------------------------------
 *   DSP: Channel vocoder (Kraftwerk-style filterbank, mono)
 *
 *   16 log-spaced narrow band-pass channels from 100 Hz to 8 kHz, Q=8.
 *   Per-band envelope follower (rectify + one-pole LPF with separate
 *   attack/release coefficients) modulates a matching set of band-pass-
 *   filtered carrier waves. The carrier is a sawtooth blended with a
 *   slightly detuned square so the result has the buzzy, intelligible
 *   "Man-Machine" character rather than a smooth talker-box pad.
 *
 *   Bands 0..14 cover the formant range (vocal intelligibility); the top
 *   band passes the high-frequency sibilance unprocessed through a noise
 *   carrier (white noise) so /s/ and /f/ phonemes survive.
 * ------------------------------------------------------------------------- */

#define VOC_BANDS 16
#define VOC_SIBILANCE_BAND_START 13 /* bands 13..15 use noise carrier */

struct voc_band
{
    struct biquad ana; /* analyser bandpass (voice -> envelope) */
    struct biquad syn; /* synthesiser bandpass (carrier -> output) */
    float env_a;       /* one-pole attack coef */
    float env_r;       /* one-pole release coef */
    float env;         /* current envelope value */
};

/* ---------------------------------------------------------------------------
 *   Pitch tracker (autocorrelation, voice band 70..400 Hz)
 *
 *   Runs on the same signal the vocoder consumes (post-rnnoise-or-bypass
 *   so it benefits from denoise when active but works on raw mic when not).
 *   Refreshed every PITCH_HOP samples (~ 10 ms @ 48 kHz). A one-pole LPF on
 *   log-Hz smooths jumps; silence below PITCH_SILENCE_RMS holds the last
 *   detected pitch instead of dropping to 0.
 *
 *   The carrier generator inside the vocoder reads `tracked_hz` whenever
 *   the user has the "follow" toggle on.
 * ------------------------------------------------------------------------- */
#define PITCH_BUF_LEN 2048 /* ~43 ms; covers 70 Hz at 48 kHz */
#define PITCH_HOP 480      /* refresh cadence (matches rnnoise) */
#define PITCH_MIN_HZ 70.0f
#define PITCH_MAX_HZ 400.0f
#define PITCH_SILENCE_RMS 0.005f /* below this, hold last */

struct pitch_tracker
{
    float buf[PITCH_BUF_LEN];
    int head;                  /* next slot to write */
    int samples_since_refresh; /* fires the autocorrelation every PITCH_HOP */
    float tracked_hz;          /* smoothed estimate (Hz), held across silence */
    float log_smoother;        /* one-pole LPF state in log-Hz domain */
    int valid;
    int miss_hops;
};

struct vocoder_state
{
    struct voc_band bands[VOC_BANDS];
    int designed;
    int designed_attack_ms;
    int designed_release_ms;
    float saw_phase;    /* main sawtooth - bright mid-range body */
    float sqr_phase;    /* detuned square - hollow low-mid harmonics */
    float sub_phase;    /* sub-octave square at fc/2 - "fat" low foundation */
    float dc_block_z;   /* one-pole DC blocker state for the saturator output */
    float rm_phase;     /* ring-mod sub-audio sine (~35 Hz) - Matrix metallic */
    float trem_phase;   /* mechanical tremolo LFO (~5.5 Hz) - machine throb */
    uint32_t noise_lcg; /* unvoiced (sibilance) noise carrier */
};

/* Linear-congruential white-noise generator for the high-band carrier.
 * Cheap, decorrelated from the saw/sqr phases, deterministic across runs. */
static inline float voc_noise(struct vocoder_state *v)
{
    v->noise_lcg = v->noise_lcg * 1103515245u + 12345u;
    return (float)((int32_t)(v->noise_lcg)) * (1.0f / 2147483648.0f);
}

static void vocoder_design(struct vocoder_state *v, float fs,
                           int attack_ms, int release_ms)
{
    /* 60 Hz..8 kHz log-spaced. The lowered floor lets the analyser bands
     * track the chest-resonance content of male voices and the sub-octave
     * carrier so the robot gets a real bass foundation. Above 8 kHz the
     * bandpass center starts wrapping close to Nyquist on lower-rate
     * setups. */
    const float lo = 60.0f, hi = 8000.0f;
    float att = (float)attack_ms * 0.001f;
    float rel = (float)release_ms * 0.001f;
    if (att < 0.001f)
        att = 0.001f;
    if (rel < 0.005f)
        rel = 0.005f;
    float a_coef = 1.0f - expf(-1.0f / (fs * att));
    float r_coef = 1.0f - expf(-1.0f / (fs * rel));
    for (int i = 0; i < VOC_BANDS; i++)
    {
        float t = (float)i / (VOC_BANDS - 1);
        float fc = lo * powf(hi / lo, t);
        /* Narrow Q for proper formant tracking. Type 6 = constant-0 dB
         * peak gain band-pass (RBJ cookbook). */
        biquad_design(&v->bands[i].ana, 6, fs, fc, 8.0f, 0.0f);
        biquad_design(&v->bands[i].syn, 6, fs, fc, 8.0f, 0.0f);
        v->bands[i].env_a = a_coef;
        v->bands[i].env_r = r_coef;
        v->bands[i].env = 0.0f;
    }
    if (!v->designed)
    {
        v->saw_phase = 0.0f;
        v->sqr_phase = 0.0f;
        v->sub_phase = 0.0f;
        v->dc_block_z = 0.0f;
        v->rm_phase = 0.0f;
        v->trem_phase = 0.0f;
        v->noise_lcg = 0x9e3779b9u;
    }
    v->designed = 1;
    v->designed_attack_ms = attack_ms;
    v->designed_release_ms = release_ms;
}

/* Update pitch tracker with a new audio sample. Re-runs the autocorrelation
 * every PITCH_HOP samples; in between, just buffers. The autocorrelation
 * searches lags corresponding to PITCH_MIN_HZ .. PITCH_MAX_HZ and picks the
 * lag with the highest *normalised* correlation, with parabolic interpolation
 * for sub-sample resolution. Outputs are smoothed in log-Hz space (so the
 * smoothing is octave-equivalent) and held across silence frames. */
static void pitch_tracker_push(struct pitch_tracker *p, float x, float fs)
{
    p->buf[p->head] = x;
    p->head = (p->head + 1) % PITCH_BUF_LEN;
    p->samples_since_refresh++;
    if (p->samples_since_refresh < PITCH_HOP)
        return;
    p->samples_since_refresh = 0;

    /* Downsample by two: a full low-pitch period fits, at modest RT cost. */
    enum { NBUF = PITCH_BUF_LEN / 2 };
    float lin[NBUF];
    int start = p->head;
    for (int i = 0; i < NBUF; i++)
        lin[i] = 0.5f * (p->buf[(start + 2 * i) % PITCH_BUF_LEN] +
                         p->buf[(start + 2 * i + 1) % PITCH_BUF_LEN]);

    /* Silence gate: cheap mean-square. */
    float ms = 0.0f;
    for (int i = 0; i < NBUF; i++)
        ms += lin[i] * lin[i];
    ms /= (float)NBUF;
    float rms = sqrtf(ms);
    if (rms < PITCH_SILENCE_RMS)
        goto missed;

    /* Lag bounds in samples. min lag = fs/max_hz; max lag = fs/min_hz. */
    int min_lag = (int)((fs / 2.0f) / PITCH_MAX_HZ);
    int max_lag = (int)ceilf((fs / 2.0f) / PITCH_MIN_HZ);
    if (min_lag < 8)
        min_lag = 8;

    /* Normalised autocorrelation: r(k) / sqrt(r0_a * r0_b). The "two
     * windows" formulation keeps the result in 0..1 and is well-conditioned
     * for voiced speech. */
    int N = NBUF - max_lag;
    float r0_a = 0.0f;
    for (int i = 0; i < N; i++)
        r0_a += lin[i] * lin[i];
    if (r0_a < 1e-9f)
        goto missed;

    float best_score = -1.0f;
    float scores[NBUF] = {0};
    for (int k = min_lag; k <= max_lag; k++)
    {
        float num = 0.0f;
        float r0_b = 0.0f;
        for (int i = 0; i < N; i++)
        {
            num += lin[i] * lin[i + k];
            r0_b += lin[i + k] * lin[i + k];
        }
        if (r0_b < 1e-9f)
            continue;
        float score = num / sqrtf(r0_a * r0_b);
        scores[k] = score;
        if (score > best_score)
            best_score = score;
    }

    /* Reject weak / unvoiced frames so we don't track room rumble. */
    if (best_score < 0.4f)
        goto missed;

    /* Use the first strong local maximum: a global maximum often picks
     * a multiple of the fundamental period and reports an octave too low. */
    int best_lag = 0;
    float threshold = fmaxf(0.55f, best_score * 0.88f);
    if (scores[min_lag] >= threshold && scores[min_lag] >= scores[min_lag + 1])
        best_lag = min_lag;
    for (int k = min_lag + 1; best_lag == 0 && k < max_lag; k++)
        if (scores[k] >= threshold && scores[k] >= scores[k - 1] &&
            scores[k] >= scores[k + 1])
        {
            best_lag = k;
            break;
        }
    if (best_lag == 0)
    {
        for (int k = min_lag; k <= max_lag; k++)
            if (scores[k] == best_score)
            {
                best_lag = k;
                break;
            }
    }

    /* Parabolic interpolation around the peak for sub-sample lag. */
    float lag = (float)best_lag;
    if (best_lag > min_lag && best_lag < max_lag)
    {
        float ym = scores[best_lag - 1], yp = scores[best_lag + 1];
        float center = scores[best_lag];
        float denom = ym - 2.0f * center + yp;
        if (fabsf(denom) > 1e-6f)
        {
            float delta = 0.5f * (ym - yp) / denom;
            if (delta > -1.0f && delta < 1.0f)
                lag = (float)best_lag + delta;
        }
    }

    float instant_hz = (fs / 2.0f) / lag;
    if (instant_hz < PITCH_MIN_HZ)
        instant_hz = PITCH_MIN_HZ;
    if (instant_hz > PITCH_MAX_HZ)
        instant_hz = PITCH_MAX_HZ;

    /* Smooth in log-Hz so the same time-constant feels right across octaves. */
    float log_now = logf(instant_hz);
    if (p->log_smoother < 1e-3f)
    {
        /* first update */
        p->log_smoother = log_now;
    }
    else
    {
        /* tau ~= 30 ms; alpha = 1 - exp(-hop/(fs*tau)) ~= 0.27 at 48 kHz */
        float alpha = 0.27f;
        p->log_smoother += alpha * (log_now - p->log_smoother);
    }
    p->tracked_hz = expf(p->log_smoother);
    p->valid = 1;
    p->miss_hops = 0;
    return;

missed:
    if (p->miss_hops < 10)
        p->miss_hops++;
    if (p->miss_hops >= 10)
        p->valid = 0;
}

static inline float vocoder_tick(struct vocoder_state *v, float x,
                                 float fs, int carrier_hz, int detune_mille,
                                 float mix, float tracked_hz, int follow,
                                 int shift_semis, int matrix_mille)
{
    /* Carrier frequency: either fixed slider (Kraftwerk style) or tracking
     * the speaker's voice with a semitone transposition (so the slider
     * becomes "+/- N semitones from your voice"). */
    float fc;
    if (follow && tracked_hz > 1.0f)
    {
        /* 2^(semis/12) is the canonical equal-temperament ratio. */
        fc = tracked_hz * powf(2.0f, (float)shift_semis / 12.0f);
    }
    else
    {
        fc = (float)carrier_hz;
    }
    if (fc < 30.0f)
        fc = 30.0f;
    if (fc > 2000.0f)
        fc = 2000.0f;

    /* Three-oscillator stack: saw + detuned square + sub-octave square.
     * Matrix-intensity (0..1) crossfades between two carrier flavours:
     *
     *   intensity = 0 -> CLEAN: 0.5*saw + 0.5*sqr, no sub, no saturation.
     *                   Two-oscillator analog-style carrier, the canonical
     *                   Kraftwerk / EMS Vocoder timbre.
     *   intensity = 1 -> CRUNCHY: 0.35*saw + 0.55*sqr + 0.55*sub through
     *                   tanh(1.8) drive. Fat sub-octave foundation plus
     *                   harmonic saturation - the Matrix Agent timbre.
     *
     * Phases always advance for both stacks so the carrier is continuous
     * if the user changes intensity live (e.g. picks a different preset
     * mid-sentence). */
    float intensity = (float)matrix_mille * 0.001f;
    if (intensity < 0.0f)
        intensity = 0.0f;
    if (intensity > 1.0f)
        intensity = 1.0f;

    float f_saw = fc;
    float f_sqr = f_saw * (1.0f + detune_mille * 0.0005f);
    float f_sub = f_saw * 0.5f;
    v->saw_phase += f_saw / fs;
    v->sqr_phase += f_sqr / fs;
    v->sub_phase += f_sub / fs;
    if (v->saw_phase >= 1.0f)
        v->saw_phase -= 1.0f;
    if (v->sqr_phase >= 1.0f)
        v->sqr_phase -= 1.0f;
    if (v->sub_phase >= 1.0f)
        v->sub_phase -= 1.0f;
    float saw = 2.0f * v->saw_phase - 1.0f;
    float sqr = (v->sqr_phase < 0.5f) ? 1.0f : -1.0f;
    float sub = (v->sub_phase < 0.5f) ? 1.0f : -1.0f;

    float clean = 0.5f * saw + 0.5f * sqr;
    /* Build the crunchy variant. tanh() is approximately linear when its
     * argument stays small, so at intensity = 0 the drive computation
     * collapses to roughly the clean blend - good safety net even though
     * the crossfade below makes it explicit. */
    float crunchy_raw = 0.35f * saw + 0.55f * sqr + 0.55f * sub;
    float crunchy = tanhf(crunchy_raw * 1.8f);
    float pitched = (1.0f - intensity) * clean + intensity * crunchy;
    /* DC block only matters for the saturated branch (tanh introduces a
     * tiny rail offset on asymmetric input). At intensity = 0 the state
     * still tracks but its output subtraction is essentially noise. */
    const float dc_coef = 0.995f;
    v->dc_block_z = pitched + dc_coef * (v->dc_block_z - pitched);
    pitched -= v->dc_block_z;
    float noise = voc_noise(v);

    float out = 0.0f;
    for (int i = 0; i < VOC_BANDS; i++)
    {
        struct voc_band *b = &v->bands[i];
        /* Analyser: filter voice, rectify, smooth with attack/release. */
        float ax = biquad_tick(&b->ana, x);
        float rect = fabsf(ax);
        float coef = (rect > b->env) ? b->env_a : b->env_r;
        b->env += coef * (rect - b->env);
        /* Synth: pitched carrier for formant bands, noise carrier for
         * sibilance bands so unvoiced phonemes stay intelligible. */
        float carrier = (i >= VOC_SIBILANCE_BAND_START) ? noise : pitched;
        float cx = biquad_tick(&b->syn, carrier);
        out += cx * b->env;
    }
    /* Empirical gain compensation: 16 narrow bands of ~ -6dB each sum
     * to a fairly quiet wet signal without makeup. */
    out *= 6.0f;

    /* ---- Matrix-style colouration (intensity-scaled) ----------------
     * Two stages that turn the clean vocoder into the Sentinel / Agent
     * comm-rig timbre when intensity is high, and disappear smoothly
     * when intensity is low.
     *
     *   1. Ring modulation by a 35 Hz sine. Mix scales from 0 (intensity
     *      0 - pure formant vocoder) to 0.7 (intensity 1 - full metallic
     *      sidebands around every formant).
     *
     *   2. Mechanical tremolo at 5.5 Hz. Depth scales from 0 to 0.18.
     *
     * Both phases always advance so a live intensity change does not
     * jump-cut the LFO position. */
    const float rm_hz = 35.0f;
    v->rm_phase += rm_hz / fs;
    if (v->rm_phase >= 1.0f)
        v->rm_phase -= 1.0f;
    float rm_carrier = sinf(2.0f * (float)M_PI * v->rm_phase);
    float rm_mix = 0.7f * intensity;
    out = (1.0f - rm_mix) * out + rm_mix * (out * rm_carrier);

    const float trem_hz = 5.5f;
    v->trem_phase += trem_hz / fs;
    if (v->trem_phase >= 1.0f)
        v->trem_phase -= 1.0f;
    float trem_depth = 0.18f * intensity;
    out *= (1.0f - trem_depth) +
           trem_depth * sinf(2.0f * (float)M_PI * v->trem_phase);

    return x * (1.0f - mix) + out * mix;
}

/* ---------------------------------------------------------------------------
 *   DSP: Pitch shifter (granular, 2-tap Hann-windowed)
 *
 *   A classic doppler / variable-delay pitch shifter. Two read heads
 *   tap the ring at opposite grain phases; the Hann² weights sum to 1
 *   for unity gain. Ratio > 1 = up-shift (read heads outrun the write
 *   head and reset every grain length); ratio < 1 = down-shift (read
 *   heads fall behind and reset). Quality is "effect-grade" - good
 *   enough for Vader / Chipmunk presets, not for serious music work.
 *   No formant preservation, so up-shifts gain chipmunk character and
 *   down-shifts gain Vader character - which is exactly what we want
 *   for these presets.
 * ------------------------------------------------------------------------- */

#define PSH_GRAIN_SIZE 2048 /* ~43 ms at 48 kHz */
#define PSH_RING_SIZE (PSH_GRAIN_SIZE * 2)
#define PSH_RING_MASK (PSH_RING_SIZE - 1)

struct pitch_shifter
{
    float ring[PSH_RING_SIZE];
    uint32_t write_pos;
    float grain_phase; /* 0..1 within the current grain pair */
};

static inline float psh_tick(struct pitch_shifter *ps, float x, float ratio)
{
    /* Always commit the input so the buffer stays warm even during the
     * bypass branch - re-engaging the shifter later finds full history. */
    ps->ring[ps->write_pos & PSH_RING_MASK] = x;
    ps->write_pos++;

    /* Bypass on unity ratio: avoids the inherent grain-fade ripple of
     * a 2-tap reader and keeps the default path bit-perfect. */
    if (fabsf(ratio - 1.0f) < 1e-4f)
        return x;

    /* Advance the grain phase: ratio > 1 shrinks the delay (phase goes
     * negative wrapped); ratio < 1 grows it. Per-output-sample step
     * is (1 - ratio) / grain_size so a full grain traversal happens
     * every grain_size / |1 - ratio| samples. */
    ps->grain_phase += (1.0f - ratio) / (float)PSH_GRAIN_SIZE;
    while (ps->grain_phase >= 1.0f)
        ps->grain_phase -= 1.0f;
    while (ps->grain_phase < 0.0f)
        ps->grain_phase += 1.0f;

    float phase_a = ps->grain_phase;
    float phase_b = phase_a + 0.5f;
    if (phase_b >= 1.0f)
        phase_b -= 1.0f;

    float delay_a = phase_a * (float)PSH_GRAIN_SIZE;
    float delay_b = phase_b * (float)PSH_GRAIN_SIZE;
    /* Keep positions inside the ring before converting to float. An absolute
     * sample counter loses sub-sample precision and eventually overflows int. */
    float write = (float)((ps->write_pos - 1u) & PSH_RING_MASK);
    float read_a = write - delay_a;
    float read_b = write - delay_b;

    int ia0 = ((int)floorf(read_a)) & PSH_RING_MASK;
    int ia1 = (ia0 + 1) & PSH_RING_MASK;
    float fa = read_a - floorf(read_a);
    float sample_a = ps->ring[ia0] * (1.0f - fa) + ps->ring[ia1] * fa;

    int ib0 = ((int)floorf(read_b)) & PSH_RING_MASK;
    int ib1 = (ib0 + 1) & PSH_RING_MASK;
    float fb = read_b - floorf(read_b);
    float sample_b = ps->ring[ib0] * (1.0f - fb) + ps->ring[ib1] * fb;

    /* Hann² crossfade: f_a = sin²(pi*phase), f_b = cos²(pi*phase).
     * The two weights sum to 1 always so the output stays at unity
     * amplitude regardless of grain phase, no per-sample normalisation. */
    float u = cosf(2.0f * (float)M_PI * phase_a);
    float fade_a = (1.0f - u) * 0.5f;
    float fade_b = (1.0f + u) * 0.5f;
    return sample_a * fade_a + sample_b * fade_b;
}

/* ---------------------------------------------------------------------------
 *   DSP: Autotune ratio computation
 *
 *   Two modes selected by target_hz:
 *     target_hz == 0 -> chromatic snap (nearest equal-tempered semitone),
 *                       the Cher / T-Pain style.
 *     target_hz >  0 -> monotone snap (always shift to this one pitch),
 *                       the DECtalk / Stephen Hawking style. The output
 *                       reads as a single droning note regardless of
 *                       what the speaker's pitch is doing.
 *   Returns 1.0 for unvoiced / silence frames so the shifter passes the
 *   signal through without trying to lock onto room noise.
 * ------------------------------------------------------------------------- */

static float autotune_ratio_for(float tracked_hz, int target_hz)
{
    if (tracked_hz < 50.0f || tracked_hz > 2000.0f)
        return 1.0f;
    if (target_hz > 0)
        return (float)target_hz / tracked_hz;
    /* Chromatic snap: MIDI note number; A4 (69) = 440 Hz. */
    float midi = 69.0f + 12.0f * log2f(tracked_hz / 440.0f);
    float snapped = roundf(midi);
    float target = 440.0f * powf(2.0f, (snapped - 69.0f) / 12.0f);
    return target / tracked_hz;
}

/* ---------------------------------------------------------------------------
 *   DSP: Bit crusher (sample-and-hold + amplitude quantisation)
 *
 *   Two retro distortions in one tick:
 *     - downsample factor: holds a sample for N output samples, mimicking
 *       low-rate ADC sampling (the "8-bit telephone" / "Atari TTS" sound).
 *     - bit depth: quantises amplitude to 2^bits levels for hard staircase.
 *
 *   Pass bits = 0 / downsample = 1 to bypass cheaply. State lives on the
 *   caller-supplied struct so the helper survives bypass / re-engage.
 * ------------------------------------------------------------------------- */

struct bitcrusher
{
    float held_sample;
    int hold_count;
};

static inline float bitcrush_tick(struct bitcrusher *bc, float x,
                                  int bits, int downsample)
{
    if (downsample <= 1)
    {
        bc->held_sample = x;
    }
    else
    {
        if (bc->hold_count <= 0)
        {
            bc->held_sample = x;
            bc->hold_count = downsample;
        }
        bc->hold_count--;
    }
    float y = bc->held_sample;
    if (bits > 0 && bits < 16)
    {
        float levels = (float)(1 << (bits - 1));
        y = roundf(y * levels) / levels;
    }
    return y;
}

/* ---------------------------------------------------------------------------
 *   Audio frame snapshot (RT -> main loop)
 * ------------------------------------------------------------------------- */

struct snapshot
{
    _Atomic uint32_t seq;
    _Atomic int published_slot;
    /* 0=free, 1=ready, 2=writer owns, 3=reader owns. */
    _Atomic int slot_state[2];
    _Atomic uint32_t flags;
    _Atomic float vad_prob;
    _Atomic float tracked_pitch_hz; /* latest pitch tracker output */
    float rms_in_sum[2];
    float rms_out_sum[2];
    /* Aligned dry versus processed signal difference per emit window.
     * This measures signal change, not isolated noise attenuation. */
    float rnn_diff_sum[2];
    int rnn_diff_n[2];
    int rms_n[2];
};

static struct snapshot g_snap;

/* ---------------------------------------------------------------------------
 *   Main DSP-state owned by RT thread
 * ------------------------------------------------------------------------- */

/* Post-DSP mono PCM ring buffer. Producer = capture stream callback (always
 * active once wireplumber has routed a real mic to us). Consumer = virtual
 * source stream callback (only active when an app is recording from us).
 *
 * Sequence numbers are monotonic. A lagging reader skips overwritten samples
 * and resumes near the live producer position. Each atomic slot carries its
 * sequence number so a concurrent overwrite cannot be mistaken for old data.
 *
 * A second instance of the same ring is used to feed the optional monitor
 * playback stream so the user can hear their own processed voice. We keep
 * the rings separate so a paused/absent monitor doesn't starve the virtual
 * source. */
#define POST_RING_CAP 4096

struct post_ring
{
    _Atomic uint64_t buf[POST_RING_CAP];
    _Atomic uint32_t head;
    _Atomic uint32_t tail;
};

struct eq_settings
{
    int type, freq_hz, q_mille, gain_centidb;
};

struct rt_state
{
    DenoiseState *rn;
    struct biquad eq[GHA_EQ_BANDS];
    struct eq_settings eq_settings[GHA_EQ_BANDS]; /* last applied values */
    struct delay_state delay;
    struct reverb_state reverb;
    int was_delay_on;
    int was_reverb_on;
    struct vocoder_state vocoder;
    struct pitch_tracker pitch; /* lives across capture callbacks */

    /* Pre-RNNoise high-pass: kills DC and sub-70 Hz rumble (HVAC, mic
     * handling, mains hum harmonics) before they enter the model. The
     * RNN was trained on telephony-band speech; removing rumble it can
     * never voice frees model capacity for the actual speech content
     * and noticeably tightens the residual hiss after denoise. */
    struct biquad rnn_hpf;

    /* Voice-effects state (pitch shift / autotune / bitcrush). All carry
     * tiny rings or held samples; living here keeps them warm across
     * bypass cycles so re-engaging an effect does not click. */
    struct pitch_shifter pitch_shifter;
    struct bitcrusher bitcrusher;
    float autotune_ratio_smooth; /* per-callback one-pole smoothed autotune ratio */

    /* Voice-stage band-pass biquads (post-vocoder telephone / muffled
     * shaping). Designed lazily on parameter change so we don't pay the
     * design cost every callback. The "designed" snapshots let us detect
     * when a new design is needed. */
    struct biquad voice_hpf;
    struct biquad voice_lpf;
    int voice_hpf_designed_hz;
    int voice_lpf_designed_hz;

    /* Stutter gate state. Phase advances at stutter_hz / fs per output
     * sample; gate is square-windowed by the duty cycle then one-pole
     * smoothed to avoid edge clicks. */
    float stutter_phase;
    float stutter_gain; /* one-pole smoothed gate gain (0..1) */

    /* rnnoise 480-sample input buffer */
    float rn_in[RNN_FRAME];
    int rn_fill;
    float raw_frame[3][RNN_FRAME];
    int raw_frame_index;

    /* rnnoise output ring (samples ready for downstream chain) */
    float rn_out_ring[RING_CAP];
    int rn_out_head, rn_out_tail;

    /* Smoothed voice-activity probability and the per-sample gate state
     * driven by it. RNNoise's raw VAD per 10 ms frame is noisy at the
     * speech/non-speech boundary and occasionally mis-labels sibilants.
     * vad_ema applies an asymmetric EMA (fast attack, slow release) so
     * the meter and the gate both see a stable signal. gate_gain ramps
     * smoothly between 1.0 (open) and a small residual (closed) to
     * avoid clicks. hangover_left keeps the gate open for a short tail
     * after VAD drops so trailing consonants are not chopped. */
    float vad_ema;
    float gate_gain;
    int hangover_left;

    float rms_in_sum;
    float rms_out_sum;
    int rms_n;
    /* Per-emit-window aligned dry-minus-processed signal energy. */
    float rnn_diff_sum;
    int rnn_diff_n;
};

static struct rt_state g_rt;
static struct post_ring g_post; /* post-DSP samples for virtual source */
static struct post_ring g_mon;  /* post-DSP samples for monitor playback */

/* ---------------------------------------------------------------------------
 *   Output (Two-Way) Noise Cancellation RT state
 *
 *   The sink pipeline captures stereo audio from applications (Discord, Zoom,
 *   browser calls, etc.), denoises each channel independently through its own
 *   RNNoise instance, applies a soft aggressiveness gate, and pushes clean
 *   audio to the physical speakers / headphones.
 *
 *   Architecture:
 *     [Apps] -> sink_in_stream (Audio/Sink, stereo 48 kHz)
 *                  |
 *           cb_sink_in_process (dual RNNoise + gate)
 *                  |
 *           g_sink_l / g_sink_r (SPSC rings)
 *                  |
 *           cb_sink_out_process -> sink_out_stream (Playback, stereo)
 *                  |
 *           [Physical Speakers / Headphones]
 * ------------------------------------------------------------------------- */

/* Per-channel RNNoise state for the output sink. Mirrors the relevant
 * subset of rt_state but without the vocoder / EQ / delay / reverb stages
 * (output noise cancellation is pure denoise + soft gate). */
struct sink_channel_state
{
    DenoiseState *rn;
    float rn_in[RNN_FRAME];
    int rn_fill;
    float raw_frame[3][RNN_FRAME];
    int raw_frame_index;
    float rn_out_ring[RING_CAP];
    int rn_out_head, rn_out_tail;
    float vad_ema;
    float gate_gain;
    int hangover_left;
    struct biquad rnn_hpf; /* pre-RNNoise 70 Hz rumble filter */
};

static struct sink_channel_state g_sink_ch_l;
static struct sink_channel_state g_sink_ch_r;
static struct post_ring g_sink_l; /* denoised left -> sink_out_stream */
static struct post_ring g_sink_r; /* denoised right -> sink_out_stream */

/* Load each parameter once and compare actual values: an arithmetic hash can
 * collide for valid frequency/gain combinations and silently skip a redesign. */
static struct eq_settings eq_read(const struct eq_band *band)
{
    return (struct eq_settings){
        .type = atomic_load(&band->type),
        .freq_hz = atomic_load(&band->freq_hz),
        .q_mille = atomic_load(&band->q_mille),
        .gain_centidb = atomic_load(&band->gain_centidb),
    };
}

static bool eq_changed(struct eq_settings a, struct eq_settings b)
{
    return a.type != b.type || a.freq_hz != b.freq_hz ||
           a.q_mille != b.q_mille || a.gain_centidb != b.gain_centidb;
}

static void eq_design(struct biquad *filter, struct eq_settings settings)
{
    biquad_design(filter, settings.type, SAMPLE_RATE, settings.freq_hz,
                  fmaxf(0.1f, settings.q_mille * 0.001f), settings.gain_centidb * 0.01f);
}

static void rt_refresh_eq(void)
{
    for (int i = 0; i < GHA_EQ_BANDS; i++)
    {
        struct eq_settings settings = eq_read(&g_params.eq[i]);
        if (eq_changed(settings, g_rt.eq_settings[i]))
        {
            eq_design(&g_rt.eq[i], settings);
            g_rt.eq_settings[i] = settings;
        }
    }
}

static struct biquad g_sink_eq_l[GHA_EQ_BANDS];
static struct biquad g_sink_eq_r[GHA_EQ_BANDS];
static struct eq_settings g_sink_eq_settings[GHA_EQ_BANDS];

static void rt_refresh_sink_eq(void)
{
    for (int i = 0; i < GHA_EQ_BANDS; i++)
    {
        struct eq_settings settings = eq_read(&g_params.out_eq[i]);
        if (eq_changed(settings, g_sink_eq_settings[i]))
        {
            eq_design(&g_sink_eq_l[i], settings);
            eq_design(&g_sink_eq_r[i], settings);
            g_sink_eq_settings[i] = settings;
        }
    }
}

/* Lazy redesign of the voice-stage band-pass biquads. Called once per
 * capture callback so a preset change that drops a new HPF / LPF cutoff
 * is picked up on the next sample without paying biquad_design cost
 * during the inner per-sample loop. */
static void rt_refresh_voice_bpf(void)
{
    int hpf = atomic_load(&g_params.voice_bpf_hpf_hz);
    int lpf = atomic_load(&g_params.voice_bpf_lpf_hz);
    if (hpf != g_rt.voice_hpf_designed_hz)
    {
        if (hpf > 0)
        {
            biquad_design(&g_rt.voice_hpf, 3 /* HPF */, (float)SAMPLE_RATE,
                          (float)hpf, 0.707f, 0.0f);
        }
        g_rt.voice_hpf_designed_hz = hpf;
    }
    if (lpf != g_rt.voice_lpf_designed_hz)
    {
        if (lpf > 0)
        {
            biquad_design(&g_rt.voice_lpf, 4 /* LPF */, (float)SAMPLE_RATE,
                          (float)lpf, 0.707f, 0.0f);
        }
        g_rt.voice_lpf_designed_hz = lpf;
    }
}

struct sink_rt_state
{
    struct delay_state delay_l, delay_r;
    struct reverb_state reverb_l, reverb_r;
    int was_delay_on;
    int was_reverb_on;
    int rnn_running;
    int rnn_warmup;
    float rnn_wet;
    struct pitch_shifter psh_l, psh_r;
    struct pitch_tracker pitch;
    struct vocoder_state vocoder_l, vocoder_r;
    struct bitcrusher bitcrusher_l, bitcrusher_r;
    struct biquad voice_hpf_l, voice_hpf_r;
    struct biquad voice_lpf_l, voice_lpf_r;
    int voice_hpf_designed_hz;
    int voice_lpf_designed_hz;
    float autotune_ratio_smooth;
    float stutter_phase;
    float stutter_gain;
};
static struct sink_rt_state g_sink_rt;

static void rt_refresh_sink_voice_bpf(void)
{
    int hpf = atomic_load(&g_params.out_voice_bpf_hpf_hz);
    int lpf = atomic_load(&g_params.out_voice_bpf_lpf_hz);
    if (hpf != g_sink_rt.voice_hpf_designed_hz)
    {
        if (hpf > 0)
        {
            biquad_design(&g_sink_rt.voice_hpf_l, 3 /* HPF */, (float)SAMPLE_RATE,
                          (float)hpf, 0.707f, 0.0f);
            biquad_design(&g_sink_rt.voice_hpf_r, 3 /* HPF */, (float)SAMPLE_RATE,
                          (float)hpf, 0.707f, 0.0f);
        }
        g_sink_rt.voice_hpf_designed_hz = hpf;
    }
    if (lpf != g_sink_rt.voice_lpf_designed_hz)
    {
        if (lpf > 0)
        {
            biquad_design(&g_sink_rt.voice_lpf_l, 4 /* LPF */, (float)SAMPLE_RATE,
                          (float)lpf, 0.707f, 0.0f);
            biquad_design(&g_sink_rt.voice_lpf_r, 4 /* LPF */, (float)SAMPLE_RATE,
                          (float)lpf, 0.707f, 0.0f);
        }
        g_sink_rt.voice_lpf_designed_hz = lpf;
    }
}

/* ---------------------------------------------------------------------------
 *   PipeWire glue (capture + virtual source + output sink)
 * ------------------------------------------------------------------------- */

struct app
{
    struct pw_main_loop *loop;
    struct pw_stream *in_stream;       /* captures from a real mic */
    struct pw_stream *out_stream;      /* exposed as Audio/Source */
    struct pw_stream *mon_stream;      /* optional monitor playback */
    struct pw_stream *sink_in_stream;  /* virtual sink capturing app audio (stereo) */
    struct pw_stream *sink_out_stream; /* playback to real speakers (stereo) */
    struct spa_source *timer;
    struct spa_source *stdin_src;
    uint32_t seq_emit;
    /* Pending capture-source change requested from the main loop.
     * mon_pending: 0=no change, 1=connect monitor, 2=disconnect monitor.
     * src_pending: 0=no change, 1=apply src_pending_target.
     * sink_pending: 0=no change, 1=connect sink pipeline, 2=disconnect.
     * sink_tgt_pending: 0=no change, 1=retarget sink_out to sink_pending_target. */
    _Atomic int src_pending;
    _Atomic int mon_pending;
    _Atomic int sink_pending;
    _Atomic int sink_tgt_pending;
    char src_pending_target[256];
    char sink_pending_target[256];
    char current_source[256];
    char current_sink[256];
};
static struct app g_app;

/* The two callbacks need different pw_stream pointers, so we wrap each in
 * a small carrier struct so the callback knows which stream it owns. */
struct stream_ctx
{
    struct pw_stream *stream;
};
static struct stream_ctx g_in_ctx;
static struct stream_ctx g_out_ctx;
static struct stream_ctx g_mon_ctx;
static struct stream_ctx g_sink_in_ctx;
static struct stream_ctx g_sink_out_ctx;

/* The producer owns head, the consumer owns tail. Float bits and sequence
 * travel in one atomic word, so the reader never observes a torn sample. */
static inline uint32_t ring_push(struct post_ring *r, uint32_t head, float v)
{
    uint32_t bits;
    memcpy(&bits, &v, sizeof(bits));
    uint64_t packed = ((uint64_t)head << 32) | bits;
    atomic_store_explicit(&r->buf[head % POST_RING_CAP], packed, memory_order_release);
    head++;
    atomic_store_explicit(&r->head, head, memory_order_release);
    return head;
}

static inline float ring_pop(struct post_ring *r, uint32_t *tail)
{
    uint32_t head = atomic_load_explicit(&r->head, memory_order_acquire);
    if (head - *tail > POST_RING_CAP)
        *tail = head - POST_RING_CAP;
    if (*tail == head)
        return 0.0f;
    uint64_t packed = atomic_load_explicit(&r->buf[*tail % POST_RING_CAP],
                                           memory_order_acquire);
    if ((uint32_t)(packed >> 32) != *tail)
        return 0.0f;
    uint32_t bits = (uint32_t)packed;
    float value;
    memcpy(&value, &bits, sizeof(value));
    (*tail)++;
    return value;
}

/* Both playback channels share one consumer position. The right producer
 * head is published after the left sample, so it marks complete frames. */
static inline void ring_pop_stereo(struct post_ring *left, struct post_ring *right,
                                   uint32_t *tail, float *out_l, float *out_r)
{
    *out_l = *out_r = 0.0f;
    uint32_t head = atomic_load_explicit(&right->head, memory_order_acquire);
    if (head - *tail > POST_RING_CAP)
        *tail = head - POST_RING_CAP;
    if (*tail == head)
        return;
    uint32_t slot = *tail % POST_RING_CAP;
    uint64_t l = atomic_load_explicit(&left->buf[slot], memory_order_acquire);
    uint64_t r = atomic_load_explicit(&right->buf[slot], memory_order_acquire);
    if ((uint32_t)(l >> 32) != *tail || (uint32_t)(r >> 32) != *tail)
        return;
    uint32_t lb = (uint32_t)l, rb = (uint32_t)r;
    memcpy(out_l, &lb, sizeof(lb));
    memcpy(out_r, &rb, sizeof(rb));
    (*tail)++;
}

/* Capture stream RT callback (always-on producer).
 *
 * For each input sample: tap-in, run the full DSP chain
 * (rnnoise -> EQ -> delay -> reverb), tap-out, push to post-DSP ring.
 * The post-DSP ring is what cb_out_process drains when an app is recording.
 * Visualization snapshots are published from here so the UI keeps updating
 * even when no app is consuming the virtual mic. */
static void cb_in_process(void *userdata)
{
    struct stream_ctx *ctx = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(ctx->stream);
    if (!b)
        return;
    struct spa_buffer *buf = b->buffer;
    if (!buf->datas[0].data)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t stride = sizeof(float);
    uint32_t maxsize = buf->datas[0].maxsize;
    uint32_t offset = maxsize ? buf->datas[0].chunk->offset % maxsize : 0;
    uint32_t size = buf->datas[0].chunk->size;
    if (size > maxsize)
        size = maxsize;
    int32_t frame_stride = buf->datas[0].chunk->stride;
    if (frame_stride == 0)
        frame_stride = (int32_t)stride;
    if (frame_stride < (int32_t)stride || frame_stride % sizeof(float) != 0 ||
        (uint32_t)frame_stride > maxsize)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t n_samples = size / (uint32_t)frame_stride;
    const uint8_t *in = buf->datas[0].data;
    uint32_t pos = offset;

    rt_refresh_eq();
    rt_refresh_voice_bpf();

    int rnn_on = atomic_load(&g_params.rnnoise_on);
    int voc_on = atomic_load(&g_params.vocoder_on);
    int eq_on = atomic_load(&g_params.eq_on);
    int delay_on = atomic_load(&g_params.delay_on);
    int reverb_on = atomic_load(&g_params.reverb_on);
    if (delay_on && !g_rt.was_delay_on)
        memset(&g_rt.delay, 0, sizeof(g_rt.delay));
    if (reverb_on && !g_rt.was_reverb_on)
        memset(&g_rt.reverb, 0, sizeof(g_rt.reverb));
    g_rt.was_delay_on = delay_on;
    g_rt.was_reverb_on = reverb_on;

    int d_samples = (atomic_load(&g_params.delay_ms) * SAMPLE_RATE) / 1000;
    float d_fb = atomic_load(&g_params.delay_feedback) * 0.001f;
    float d_mix = atomic_load(&g_params.delay_mix) * 0.001f;

    float r_room = atomic_load(&g_params.reverb_room) * 0.001f;
    float r_damp = atomic_load(&g_params.reverb_damp) * 0.001f;
    float r_width = atomic_load(&g_params.reverb_width) * 0.001f;
    float r_mix = atomic_load(&g_params.reverb_mix) * 0.001f;

    int voc_carrier_hz = atomic_load(&g_params.vocoder_carrier_hz);
    int voc_detune = atomic_load(&g_params.vocoder_detune);
    int voc_attack_ms = atomic_load(&g_params.vocoder_attack_ms);
    int voc_release_ms = atomic_load(&g_params.vocoder_release_ms);
    float voc_mix = atomic_load(&g_params.vocoder_mix) * 0.001f;
    int voc_follow = atomic_load(&g_params.vocoder_follow);
    int voc_shift = atomic_load(&g_params.vocoder_shift_semis);

    /* Master output gain, applied LAST in the chain (after all DSP stages
     * including delay/reverb) so the virtual source AND the monitor both
     * hear the exact same signal. Per-mille; 1000 = unity. Loaded once per
     * callback - same cadence as the rest of the param atomics. */
    float master_gain = atomic_load(&g_params.master_vol_mille) * 0.001f;

    /* Post-EQ uniform gain. Converted from centi-dB to a linear multiplier
     * once per callback. Only applied if the EQ chain is active so toggling
     * the EQ off bypasses both the bands and the makeup gain coherently. */
    float eq_post_gain = powf(10.0f,
                              (float)atomic_load(&g_params.eq_gain_centidb) / 100.0f / 20.0f);

    /* Cheap reactive re-design only when attack/release changed; avoids
     * mutating filter coefs on every sample. carrier_hz changes are
     * picked up directly inside vocoder_tick. */
    if (g_rt.vocoder.designed_attack_ms != voc_attack_ms ||
        g_rt.vocoder.designed_release_ms != voc_release_ms)
    {
        vocoder_design(&g_rt.vocoder, (float)SAMPLE_RATE,
                       voc_attack_ms, voc_release_ms);
    }

    /* Voice-effects parameters (pitch shift / autotune / bitcrush) are
     * resolved into per-sample numbers once per callback. The autotune
     * ratio uses the pitch tracker's currently held tracked_hz; if that
     * is zero (silence / unvoiced), we hold the previous ratio so a
     * vowel doesn't drop pitch as it ends. */
    int psh_cs = atomic_load(&g_params.pitch_shift_centisemis);
    int autotune_on = atomic_load(&g_params.autotune_on);
    int autotune_target = atomic_load(&g_params.voice_autotune_target_hz);
    float target_ratio;
    if (autotune_on)
    {
        target_ratio = autotune_ratio_for(g_rt.pitch.valid ? g_rt.pitch.tracked_hz : 0.0f, autotune_target);
    }
    else if (psh_cs != 0)
    {
        target_ratio = powf(2.0f, (float)psh_cs / 1200.0f);
    }
    else
    {
        target_ratio = 1.0f;
    }
    /* One-pole smoother on the ratio so manual / autotune changes don't
     * step the read-head jump cadence and click. Two regimes:
     *   - manual shift: 20 ms time constant - feels stable, no audible
     *     ramp when the user moves the pitch slider.
     *   - autotune: 3 ms - the snap should feel like the classic T-Pain /
     *     Cher "quantise" effect, not a glide. The pitch tracker still
     *     refreshes only at the ~10 ms hop boundary so the smoother
     *     converges between hops without smearing the snap. */
    const float ratio_tc = autotune_on ? 0.003f : 0.020f;
    const float ratio_coef = 1.0f - expf(-1.0f / ((float)SAMPLE_RATE * ratio_tc));
    int bc_bits = atomic_load(&g_params.bitcrush_bits);
    int bc_ds = atomic_load(&g_params.bitcrush_downsample);
    int matrix_mille = atomic_load(&g_params.matrix_intensity_mille);

    /* Voice-stage band-pass and stutter parameters resolved once per
     * callback so the inner loop just reads scalars. */
    int voice_hpf_hz = g_rt.voice_hpf_designed_hz;
    int voice_lpf_hz = g_rt.voice_lpf_designed_hz;
    int stutter_hz = atomic_load(&g_params.voice_stutter_hz);
    int stutter_duty = atomic_load(&g_params.voice_stutter_duty_mille);
    /* Clamp duty to [50, 950] per-mille so neither the open nor the
     * closed phase shrinks below ~1 ms - shorter and the smoother just
     * blurs the gate into nothing. */
    if (stutter_duty < 50)
        stutter_duty = 50;
    if (stutter_duty > 950)
        stutter_duty = 950;
    float stutter_duty_norm = (float)stutter_duty * 0.001f;
    /* Pre-compute the one-pole smoothing coefficient for the stutter
     * gate (5 ms time constant - inaudible click prevention without
     * blurring the chop). */
    const float stutter_gate_coef = 1.0f - expf(-1.0f / ((float)SAMPLE_RATE * 0.005f));



    uint32_t phead = atomic_load_explicit(&g_post.head, memory_order_relaxed);
    uint32_t mhead = atomic_load_explicit(&g_mon.head, memory_order_relaxed);
    int mon_on = atomic_load(&g_params.monitor_on);

    for (uint32_t i = 0; i < n_samples; i++)
    {
        float x;
        if (pos + sizeof(x) <= maxsize)
            memcpy(&x, in + pos, sizeof(x));
        else
        {
            uint32_t first = maxsize - pos;
            memcpy(&x, in + pos, first);
            memcpy((uint8_t *)&x + first, in, sizeof(x) - first);
        }
        pos += (uint32_t)frame_stride;
        if (pos >= maxsize)
            pos -= maxsize;

        g_rt.rms_in_sum += x * x;

        /* Stage 1: rnnoise ALWAYS runs so its VAD is meaningful even when
         * the user has the denoise stage bypassed. The bypass switch picks
         * which buffer (raw input or denoised) is fed downstream. The cost
         * is a constant ~3% CPU per channel - cheaper than the wiring
         * needed to keep VAD and reduction metrics honest.
         *
         * Pre-HPF only applies when denoise is on: we do not want to colour
         * the raw mic path when the user has deliberately bypassed the
         * whole denoise stage. */
        float rn_input_sample = rnn_on ? biquad_tick(&g_rt.rnn_hpf, x) : x;
        g_rt.raw_frame[g_rt.raw_frame_index][g_rt.rn_fill] = x;
        g_rt.rn_in[g_rt.rn_fill++] = rn_input_sample * 32768.0f;
        if (g_rt.rn_fill == RNN_FRAME)
        {
            float denoised[RNN_FRAME];
            float vad = rnnoise_process_frame(g_rt.rn, denoised, g_rt.rn_in);
            const float *dry = g_rt.raw_frame[(g_rt.raw_frame_index + 1) % 3];

            /* Asymmetric EMA on VAD: snap up so onsets are not chopped,
             * decay slowly so brief gaps between syllables do not trip
             * the gate closed. alpha_up ~= 0.6 corresponds to one-frame
             * rise; alpha_dn ~= 0.05 corresponds to ~200 ms decay at
             * the 10 ms RNNoise frame rate. */
            const float alpha_up = 0.6f;
            const float alpha_dn = 0.05f;
            float prev = g_rt.vad_ema;
            g_rt.vad_ema = (vad > prev)
                               ? prev + alpha_up * (vad - prev)
                               : prev + alpha_dn * (vad - prev);
            atomic_store(&g_snap.vad_prob, g_rt.vad_ema);

            /* Soft post-RNNoise gate. Aggressiveness picks how deep the
             * attenuation goes during sustained silence (target gain at
             * full close = 1 - 0.94 * aggro -> aggro 1.0 yields -24 dB).
             * Two thresholds form hysteresis so the gate does not chatter
             * around a single boundary; a 250 ms hangover keeps it open
             * after VAD drops. The gain itself is single-pole-smoothed
             * per sample so transitions never click. */
            float aggro = atomic_load(&g_params.rnn_aggressiveness) * 0.001f;
            if (aggro < 0.0f)
                aggro = 0.0f;
            if (aggro > 1.0f)
                aggro = 1.0f;
            const float open_th = 0.50f;
            const float close_th = 0.15f;
            const int hangover_samples = SAMPLE_RATE / 4; /* 250 ms */
            const float close_target = 1.0f - 0.98f * aggro;
            /* One-pole coefficient for a ~5 ms time constant: gain
             * reaches 63% of its new target in 5 ms, fully there in
             * ~20 ms. Short enough to feel instant, long enough to
             * eliminate audible clicks. */
            const float gain_coeff = 1.0f - expf(-1.0f / (SAMPLE_RATE * 0.005f));

            float diff_sum = 0.0f;
            for (int k = 0; k < RNN_FRAME; k++)
            {
                float s = denoised[k] * (1.0f / 32768.0f);

                if (rnn_on)
                {
                    /* Hysteresis state machine: open above the high
                     * threshold, close only after hangover expires
                     * below the low threshold. Avoids fluttering on
                     * borderline-voiced segments. */
                    float v = g_rt.vad_ema;
                    float target;
                    if (v >= open_th)
                    {
                        target = 1.0f;
                        g_rt.hangover_left = hangover_samples;
                    }
                    else if (v <= close_th && g_rt.hangover_left == 0)
                    {
                        target = close_target;
                    }
                    else
                    {
                        target = 1.0f;
                        if (g_rt.hangover_left > 0)
                            g_rt.hangover_left--;
                    }
                    g_rt.gate_gain += gain_coeff * (target - g_rt.gate_gain);

                    /* Continuous dry/wet blend: at aggro=0, 100% raw audio passes through.
                     * At aggro=1.0, 100% denoised + gated audio passes through. */
                    float processed = s * g_rt.gate_gain;
                    s = (1.0f - aggro) * dry[k] + aggro * processed;
                }
                else
                    s = dry[k];

                /* Measure the final blend against its aligned dry input.
                 * This reports signal change, not isolated noise removal. */
                if (rnn_on)
                {
                    float d = (dry[k] - s) * 32768.0f;
                    diff_sum += d * d;
                }

                int next = (g_rt.rn_out_head + 1) % RING_CAP;
                if (next != g_rt.rn_out_tail)
                {
                    g_rt.rn_out_ring[g_rt.rn_out_head] = s;
                    g_rt.rn_out_head = next;
                }
            }
            if (rnn_on)
            {
                g_rt.rnn_diff_sum += diff_sum;
                g_rt.rnn_diff_n += RNN_FRAME;
            }
            g_rt.rn_fill = 0;
            g_rt.raw_frame_index = (g_rt.raw_frame_index + 1) % 3;
        }

        float y = 0.0f;
        if (g_rt.rn_out_head != g_rt.rn_out_tail)
        {
            y = g_rt.rn_out_ring[g_rt.rn_out_tail];
            g_rt.rn_out_tail = (g_rt.rn_out_tail + 1) % RING_CAP;
        }

        /* Pitch tracker reads the unprocessed (post-rnnoise) signal so the
         * autotune and follow-mode estimates reflect the speaker's actual
         * pitch, not a pitch-shifted version of it. */
        pitch_tracker_push(&g_rt.pitch, y, (float)SAMPLE_RATE);

        /* Stage 2a: pitch shifter / autotune. Smoothed ratio drives the
         * granular shifter; bypassed automatically when the ratio is at
         * unity so the default (preset-less) path is bit-perfect. */
        g_rt.autotune_ratio_smooth +=
            ratio_coef * (target_ratio - g_rt.autotune_ratio_smooth);
        y = psh_tick(&g_rt.pitch_shifter, y, g_rt.autotune_ratio_smooth);

        /* Stage 2b: vocoder (sits between pitch shifter and EQ so the EQ
         * shapes the vocoded output, not the dry voice). The vocoder
         * receives the pitch-shifted voice, so a "Vader" preset that
         * combines shift = -5 st + voc_mix = 0 outputs the shifted dry
         * voice; a vocoder preset keeps shift = 0 and a high voc_mix. */
        if (voc_on)
        {
            y = vocoder_tick(&g_rt.vocoder, y, (float)SAMPLE_RATE,
                             voc_carrier_hz, voc_detune, voc_mix,
                             g_rt.pitch.valid ? g_rt.pitch.tracked_hz : 0.0f, voc_follow, voc_shift,
                             matrix_mille);
        }

        if (eq_on)
        {
            for (int bi = 0; bi < GHA_EQ_BANDS; bi++)
                y = biquad_tick(&g_rt.eq[bi], y);
            /* Post-EQ uniform gain (the "drag the line" parameter). Placed
             * inside the eq_on block so it shares the bypass switch with
             * the bands themselves - users who turn the EQ off should hear
             * neither the bands nor the makeup gain. */
            y *= eq_post_gain;
        }
        if (delay_on)
            y = delay_tick(&g_rt.delay, y, d_samples, d_fb, d_mix);
        if (reverb_on)
            y = reverb_tick(&g_rt.reverb, y, r_room, r_damp, r_width, r_mix);

        /* Master gain. Applied here (rather than at the virtual-source
         * drain) so the monitor playback hears the exact same signal the
         * virtual source sends out: both rings hold the already-gained
         * samples and the consumer callbacks drain as-is. The output
         * waveform tap and rms_out also see this final signal, so the
         * meters and visualizations reflect what the device hears. */
        y *= master_gain;

        /* Voice-stage band-pass: HPF then LPF in series. Drives the
         * "Robot Phone" 300-3400 Hz telephone band and the "Darth Vader"
         * muffled-cathedral low-pass. Both biquads run unconditionally
         * once designed (state stays warm through bypass cycles); each
         * is gated by its respective cutoff being non-zero. */
        if (voice_hpf_hz > 0)
            y = biquad_tick(&g_rt.voice_hpf, y);
        if (voice_lpf_hz > 0)
            y = biquad_tick(&g_rt.voice_lpf, y);

        /* Stutter gate: square-wave amplitude modulation at speech rate.
         * The Cylon "By Your Command" cadence is exactly this - a voice
         * chopped 4..8 times per second produces the "talking machine"
         * rhythm. Phase advances per sample; gate value is one-pole
         * smoothed so the closed-to-open edges do not click. */
        if (stutter_hz > 0)
        {
            g_rt.stutter_phase += (float)stutter_hz / (float)SAMPLE_RATE;
            if (g_rt.stutter_phase >= 1.0f)
                g_rt.stutter_phase -= 1.0f;
            float target_gate = (g_rt.stutter_phase < stutter_duty_norm) ? 1.0f : 0.0f;
            g_rt.stutter_gain += stutter_gate_coef * (target_gate - g_rt.stutter_gain);
            y *= g_rt.stutter_gain;
        }
        else if (g_rt.stutter_gain < 1.0f)
        {
            /* Smoothly re-open the gate when the preset turns stutter off
             * - same coef so the transition is symmetric. */
            g_rt.stutter_gain += stutter_gate_coef * (1.0f - g_rt.stutter_gain);
            y *= g_rt.stutter_gain;
        }

        /* Bit crusher: the very last stage so it captures the entire
         * chain's output as if it had gone through a low-rate ADC. Used
         * by the "Robot Phone" preset to nail the AM-radio / lo-fi
         * character. Bypassed cheaply when bits == 0 and ds == 1. */
        if (bc_bits > 0 || bc_ds > 1)
            y = bitcrush_tick(&g_rt.bitcrusher, y, bc_bits, bc_ds);

        g_rt.rms_out_sum += y * y;
        g_rt.rms_n++;

        /* Overwrite stale samples without touching consumer state. */
        phead = ring_push(&g_post, phead, y);
        if (mon_on)
            mhead = ring_push(&g_mon, mhead, y);
    }
    pw_stream_queue_buffer(ctx->stream, b);

    /* Publish snapshot every ~16 ms */
    if (g_rt.rms_n >= FRAME_EMIT_SAMPLES)
    {
        uint32_t cur = atomic_load(&g_snap.seq);
        int slot = 1 - atomic_load(&g_snap.published_slot);
        int expected = 0;
        if (!atomic_compare_exchange_strong(&g_snap.slot_state[slot], &expected, 2))
        {
            expected = 1;
            if (!atomic_compare_exchange_strong(&g_snap.slot_state[slot], &expected, 2))
            {
                slot = 1 - slot;
                expected = 0;
                if (!atomic_compare_exchange_strong(&g_snap.slot_state[slot], &expected, 2))
                {
                    expected = 1;
                    if (!atomic_compare_exchange_strong(&g_snap.slot_state[slot], &expected, 2))
                        slot = -1;
                }
            }
        }
        if (slot >= 0)
        {
            g_snap.rms_in_sum[slot] = g_rt.rms_in_sum;
            g_snap.rms_out_sum[slot] = g_rt.rms_out_sum;
            g_snap.rms_n[slot] = g_rt.rms_n;
            g_snap.rnn_diff_sum[slot] = g_rt.rnn_diff_sum;
            g_snap.rnn_diff_n[slot] = g_rt.rnn_diff_n;
            atomic_store(&g_snap.tracked_pitch_hz, g_rt.pitch.valid ? g_rt.pitch.tracked_hz : 0.0f);
            int out_rnn = atomic_load(&g_params.out_rnnoise_on);
            uint32_t flags = (rnn_on ? GHA_FLAG_RNNOISE_ON : 0u) |
                             (eq_on ? GHA_FLAG_EQ_ON : 0u) |
                             (delay_on ? GHA_FLAG_DELAY_ON : 0u) |
                             (reverb_on ? GHA_FLAG_REVERB_ON : 0u) |
                             (mon_on ? GHA_FLAG_MONITOR_ON : 0u) |
                             (voc_on ? GHA_FLAG_VOCODER_ON : 0u) |
                             (out_rnn ? GHA_FLAG_OUT_RNNOISE_ON : 0u) |
                             (atomic_load(&g_params.out_eq_on) ? GHA_FLAG_OUT_EQ_ON : 0u);
            atomic_store(&g_snap.flags, flags);
            atomic_store(&g_snap.slot_state[slot], 1);
            atomic_store(&g_snap.published_slot, slot);
            atomic_store(&g_snap.seq, cur + 1);
        }
        g_rt.rms_in_sum = g_rt.rms_out_sum = 0.0f;
        g_rt.rms_n = 0;
        g_rt.rnn_diff_sum = 0.0f;
        g_rt.rnn_diff_n = 0;
    }
}

/* Virtual-source stream RT callback (consumer).
 *
 * Drains the post-DSP ring into the output PipeWire buffer. Emits silence
 * if the producer hasn't filled the ring yet (e.g. mic not yet linked). */
static void cb_out_process(void *userdata)
{
    struct stream_ctx *ctx = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(ctx->stream);
    if (!b)
        return;
    struct spa_buffer *buf = b->buffer;
    if (!buf->datas[0].data)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t stride = sizeof(float);
    uint32_t n_avail = buf->datas[0].maxsize / stride;
    uint32_t n_samples = n_avail;
    if (b->requested && b->requested < n_samples)
        n_samples = (uint32_t)b->requested;
    float *out = buf->datas[0].data;

    uint32_t tail = atomic_load_explicit(&g_post.tail, memory_order_relaxed);

    /* The ring already holds master-gained samples (gain applied in
     * cb_in_process, the last step of the DSP chain, so virtual source and
     * monitor drain the same signal). Drain straight to the PipeWire
     * output buffer - no gain math here. */
    for (uint32_t i = 0; i < n_samples; i++)
    {
        out[i] = ring_pop(&g_post, &tail);
    }
    atomic_store_explicit(&g_post.tail, tail, memory_order_release);

    buf->datas[0].chunk->offset = 0;
    buf->datas[0].chunk->stride = (int32_t)stride;
    buf->datas[0].chunk->size = n_samples * stride;
    pw_stream_queue_buffer(ctx->stream, b);
}

/* Monitor playback stream RT callback (consumer of g_mon ring). */
static void cb_mon_process(void *userdata)
{
    struct stream_ctx *ctx = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(ctx->stream);
    if (!b)
        return;
    struct spa_buffer *buf = b->buffer;
    if (!buf->datas[0].data)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t stride = sizeof(float);
    uint32_t n_avail = buf->datas[0].maxsize / stride;
    uint32_t n_samples = n_avail;
    if (b->requested && b->requested < n_samples)
        n_samples = (uint32_t)b->requested;
    float *out = buf->datas[0].data;

    uint32_t tail = atomic_load_explicit(&g_mon.tail, memory_order_relaxed);

    for (uint32_t i = 0; i < n_samples; i++)
    {
        out[i] = ring_pop(&g_mon, &tail);
    }
    atomic_store_explicit(&g_mon.tail, tail, memory_order_release);

    buf->datas[0].chunk->offset = 0;
    buf->datas[0].chunk->stride = (int32_t)stride;
    buf->datas[0].chunk->size = n_samples * stride;
    pw_stream_queue_buffer(ctx->stream, b);
}

/* ---------------------------------------------------------------------------
 *   Output (Two-Way) Noise Cancellation: Sink RT callbacks
 *
 *   cb_sink_in_process: RT callback for the virtual sink stream. Captures
 *   interleaved stereo F32 audio from apps (Discord, Zoom, browser, games),
 *   deinterleaves into L/R, runs each through its own RNNoise + soft gate,
 *   and pushes the denoised samples into per-channel SPSC rings.
 *
 *   cb_sink_out_process: RT callback for the playback stream. Drains the
 *   per-channel rings, interleaves L/R, and writes stereo F32 to the output
 *   PipeWire buffer destined for physical speakers / headphones.
 * ------------------------------------------------------------------------- */

/* Process one RNNoise frame for a single sink channel. Identical algorithm
 * to the input pipeline's gate, but parameterised per-channel so L and R
 * can gate independently (one person talking on L, silence on R). */
static void sink_channel_reset(struct sink_channel_state *ch)
{
    rnnoise_init(ch->rn, NULL);
    memset(ch->rn_in, 0, sizeof(ch->rn_in));
    memset(ch->raw_frame, 0, sizeof(ch->raw_frame));
    ch->rn_fill = 0;
    ch->raw_frame_index = 0;
    ch->rn_out_head = ch->rn_out_tail = 0;
    ch->vad_ema = 0.0f;
    ch->gate_gain = 1.0f;
    ch->hangover_left = 0;
    ch->rnn_hpf.z1 = ch->rnn_hpf.z2 = 0.0f;
}

static void sink_channel_process_frame(struct sink_channel_state *ch, int aggro_mille)
{
    float denoised[RNN_FRAME];
    float vad = rnnoise_process_frame(ch->rn, denoised, ch->rn_in);

    /* Asymmetric EMA on VAD (same constants as input pipeline). */
    const float alpha_up = 0.6f;
    const float alpha_dn = 0.05f;
    float prev = ch->vad_ema;
    ch->vad_ema = (vad > prev)
                      ? prev + alpha_up * (vad - prev)
                      : prev + alpha_dn * (vad - prev);

    /* Soft post-RNNoise gate with hysteresis and hangover. */
    float aggro = aggro_mille * 0.001f;
    if (aggro < 0.0f) aggro = 0.0f;
    if (aggro > 1.0f) aggro = 1.0f;
    const float open_th = 0.50f;
    const float close_th = 0.15f;
    const int hangover_samples = SAMPLE_RATE / 4; /* 250 ms */
    const float close_target = 1.0f - 0.98f * aggro;
    const float gain_coeff = 1.0f - expf(-1.0f / (SAMPLE_RATE * 0.005f));

    for (int k = 0; k < RNN_FRAME; k++)
    {
        float s = denoised[k] * (1.0f / 32768.0f);

        float v = ch->vad_ema;
        float target;
        if (v >= open_th)
        {
            target = 1.0f;
            ch->hangover_left = hangover_samples;
        }
        else if (v <= close_th && ch->hangover_left == 0)
        {
            target = close_target;
        }
        else
        {
            target = 1.0f;
            if (ch->hangover_left > 0)
                ch->hangover_left--;
        }
        ch->gate_gain += gain_coeff * (target - ch->gate_gain);

        /* Continuous dry/wet blend: at aggro=0, 100% raw audio passes through.
         * At aggro=1.0, 100% denoised + gated audio passes through. */
        float raw = ch->raw_frame[(ch->raw_frame_index + 1) % 3][k];
        float processed = s * ch->gate_gain;
        s = (1.0f - aggro) * raw + aggro * processed;

        int next = (ch->rn_out_head + 1) % RING_CAP;
        if (next != ch->rn_out_tail)
        {
            ch->rn_out_ring[ch->rn_out_head] = s;
            ch->rn_out_head = next;
        }
    }
    ch->rn_fill = 0;
    ch->raw_frame_index = (ch->raw_frame_index + 1) % 3;
}

/* Feed one sample to a sink channel's RNNoise input buffer. When the
 * buffer fills a complete 480-sample frame, process it. Returns the
 * next available denoised sample (or 0 if the output ring is empty). */
static float sink_channel_tick(struct sink_channel_state *ch, float x, int aggro_mille)
{
    /* Pre-RNNoise 70 Hz high-pass (same as input pipeline). */
    float rn_in = biquad_tick(&ch->rnn_hpf, x);
    ch->raw_frame[ch->raw_frame_index][ch->rn_fill] = x;
    ch->rn_in[ch->rn_fill++] = rn_in * 32768.0f;
    if (ch->rn_fill == RNN_FRAME)
        sink_channel_process_frame(ch, aggro_mille);

    /* Drain one sample from the output ring. */
    if (ch->rn_out_head != ch->rn_out_tail)
    {
        float y = ch->rn_out_ring[ch->rn_out_tail];
        ch->rn_out_tail = (ch->rn_out_tail + 1) % RING_CAP;
        return y;
    }
    return 0.0f;
}

/* Sink capture RT callback. The virtual sink receives interleaved stereo
 * F32 from apps routed into "Dusky Audio".
 * When out_rnnoise_on is active, each channel is independently denoised.
 * When bypassed, audio passes through untouched. */
static void cb_sink_in_process(void *userdata)
{
    struct stream_ctx *ctx = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(ctx->stream);
    if (!b)
        return;
    struct spa_buffer *buf = b->buffer;
    if (!buf->datas[0].data)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t stride = sizeof(float) * 2; /* stereo interleaved */
    uint32_t maxsize = buf->datas[0].maxsize;
    uint32_t offset = maxsize ? buf->datas[0].chunk->offset % maxsize : 0;
    uint32_t size = buf->datas[0].chunk->size;
    if (size > maxsize)
        size = maxsize;
    int32_t frame_stride = buf->datas[0].chunk->stride;
    if (frame_stride == 0)
        frame_stride = (int32_t)stride;
    if (frame_stride < (int32_t)stride || frame_stride % sizeof(float) != 0 ||
        (uint32_t)frame_stride > maxsize)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t n_samples = size / (uint32_t)frame_stride;
    const uint8_t *in = buf->datas[0].data;
    uint32_t pos = offset;

    rt_refresh_sink_eq();
    rt_refresh_sink_voice_bpf();

    int rnn_on = atomic_load(&g_params.out_rnnoise_on);
    int aggro = atomic_load(&g_params.out_rnn_aggressiveness);
    if (rnn_on && !g_sink_rt.rnn_running)
    {
        sink_channel_reset(&g_sink_ch_l);
        sink_channel_reset(&g_sink_ch_r);
        g_sink_rt.rnn_running = 1;
        g_sink_rt.rnn_warmup = 0;
    }
    int out_eq_on = atomic_load(&g_params.out_eq_on);
    float out_eq_post_gain = powf(10.0f,
                                  (float)atomic_load(&g_params.out_eq_gain_centidb) / 100.0f / 20.0f);

    int out_delay_on = atomic_load(&g_params.out_delay_on);
    int d_samples = (atomic_load(&g_params.out_delay_ms) * SAMPLE_RATE) / 1000;
    float d_fb = atomic_load(&g_params.out_delay_feedback) * 0.001f;
    float d_mix = atomic_load(&g_params.out_delay_mix) * 0.001f;

    int out_reverb_on = atomic_load(&g_params.out_reverb_on);
    if (out_delay_on && !g_sink_rt.was_delay_on)
    {
        memset(&g_sink_rt.delay_l, 0, sizeof(g_sink_rt.delay_l));
        memset(&g_sink_rt.delay_r, 0, sizeof(g_sink_rt.delay_r));
    }
    if (out_reverb_on && !g_sink_rt.was_reverb_on)
    {
        memset(&g_sink_rt.reverb_l, 0, sizeof(g_sink_rt.reverb_l));
        memset(&g_sink_rt.reverb_r, 0, sizeof(g_sink_rt.reverb_r));
    }
    g_sink_rt.was_delay_on = out_delay_on;
    g_sink_rt.was_reverb_on = out_reverb_on;
    float r_room = atomic_load(&g_params.out_reverb_room) * 0.001f;
    float r_damp = atomic_load(&g_params.out_reverb_damp) * 0.001f;
    float r_width = atomic_load(&g_params.out_reverb_width) * 0.001f;
    float r_mix = atomic_load(&g_params.out_reverb_mix) * 0.001f;

    int out_voc_on = atomic_load(&g_params.out_vocoder_on);
    int out_voc_carrier_hz = atomic_load(&g_params.out_vocoder_carrier_hz);
    int out_voc_detune = atomic_load(&g_params.out_vocoder_detune);
    int out_voc_attack_ms = atomic_load(&g_params.out_vocoder_attack_ms);
    int out_voc_release_ms = atomic_load(&g_params.out_vocoder_release_ms);
    float out_voc_mix = atomic_load(&g_params.out_vocoder_mix) * 0.001f;
    int out_voc_follow = atomic_load(&g_params.out_vocoder_follow);
    int out_voc_shift = atomic_load(&g_params.out_vocoder_shift_semis);
    int out_matrix_mille = atomic_load(&g_params.out_matrix_intensity_mille);

    int out_psh_centisemis = atomic_load(&g_params.out_pitch_shift_centisemis);
    int out_autotune_on = atomic_load(&g_params.out_autotune_on);
    int out_atn_target_hz = atomic_load(&g_params.out_voice_autotune_target_hz);
    int out_bitcrush_bits = atomic_load(&g_params.out_bitcrush_bits);
    int out_bitcrush_ds = atomic_load(&g_params.out_bitcrush_downsample);
    int out_voice_hpf_hz = g_sink_rt.voice_hpf_designed_hz;
    int out_voice_lpf_hz = g_sink_rt.voice_lpf_designed_hz;
    int out_stutter_hz = atomic_load(&g_params.out_voice_stutter_hz);
    int out_stutter_duty = atomic_load(&g_params.out_voice_stutter_duty_mille);

    if (g_sink_rt.vocoder_l.designed_attack_ms != out_voc_attack_ms ||
        g_sink_rt.vocoder_l.designed_release_ms != out_voc_release_ms)
    {
        vocoder_design(&g_sink_rt.vocoder_l, (float)SAMPLE_RATE,
                       out_voc_attack_ms, out_voc_release_ms);
        vocoder_design(&g_sink_rt.vocoder_r, (float)SAMPLE_RATE,
                       out_voc_attack_ms, out_voc_release_ms);
    }

    float target_ratio = 1.0f;
    if (out_autotune_on)
    {
        target_ratio = autotune_ratio_for(g_sink_rt.pitch.valid ? g_sink_rt.pitch.tracked_hz : 0.0f, out_atn_target_hz);
    }
    else if (out_psh_centisemis != 0)
    {
        target_ratio = powf(2.0f, (float)out_psh_centisemis / 1200.0f);
    }

    uint32_t lhead = atomic_load_explicit(&g_sink_l.head, memory_order_relaxed);
    uint32_t rhead = atomic_load_explicit(&g_sink_r.head, memory_order_relaxed);

    for (uint32_t i = 0; i < n_samples; i++)
    {
        float frame[2];
        if (pos + sizeof(frame) <= maxsize)
            memcpy(frame, in + pos, sizeof(frame));
        else
        {
            uint32_t first = maxsize - pos;
            memcpy(frame, in + pos, first);
            memcpy((uint8_t *)frame + first, in, sizeof(frame) - first);
        }
        pos += (uint32_t)frame_stride;
        if (pos >= maxsize)
            pos -= maxsize;
        float l = frame[0];
        float r = frame[1];

        /* 1. Two-Way RNNoise. Keep the low-latency bypass and fade across
         * changes only after fresh RNNoise history has filled. */
        if (g_sink_rt.rnn_running)
        {
            float processed_l = sink_channel_tick(&g_sink_ch_l, l, aggro);
            float processed_r = sink_channel_tick(&g_sink_ch_r, r, aggro);
            if (g_sink_rt.rnn_warmup < 3 * RNN_FRAME)
                g_sink_rt.rnn_warmup++;
            if (rnn_on && g_sink_rt.rnn_warmup >= 3 * RNN_FRAME)
                g_sink_rt.rnn_wet = fminf(1.0f, g_sink_rt.rnn_wet + 1.0f / RNN_FRAME);
            else if (!rnn_on)
                g_sink_rt.rnn_wet = fmaxf(0.0f, g_sink_rt.rnn_wet - 1.0f / RNN_FRAME);
            l += g_sink_rt.rnn_wet * (processed_l - l);
            r += g_sink_rt.rnn_wet * (processed_r - r);
            if (!rnn_on && g_sink_rt.rnn_wet == 0.0f)
                g_sink_rt.rnn_running = 0;
        }

        /* 2. Stereo Pitch Shifter & Autotune.
         * The pitch tracker's autocorrelation pass is far too expensive to
         * burn on every playback sample when nothing consumes it - only
         * output autotune and vocoder follow-mode read tracked_hz. When
         * both are inactive the last estimate simply holds; vocoder_tick
         * falls back to the fixed carrier for stale/zero values, and the
         * tracker re-converges within one ~10 ms hop after re-enabling. */
        if (out_autotune_on || (out_voc_on && out_voc_follow))
        {
            float mono_mid = 0.5f * (l + r);
            pitch_tracker_push(&g_sink_rt.pitch, mono_mid, (float)SAMPLE_RATE);
        }
        g_sink_rt.autotune_ratio_smooth += 0.05f * (target_ratio - g_sink_rt.autotune_ratio_smooth);
        l = psh_tick(&g_sink_rt.psh_l, l, g_sink_rt.autotune_ratio_smooth);
        r = psh_tick(&g_sink_rt.psh_r, r, g_sink_rt.autotune_ratio_smooth);

        /* 3. Stereo Vocoder (Independent L/R Carrier and Filterbanks) */
        if (out_voc_on)
        {
            l = vocoder_tick(&g_sink_rt.vocoder_l, l, (float)SAMPLE_RATE,
                             out_voc_carrier_hz, out_voc_detune, out_voc_mix,
                             g_sink_rt.pitch.valid ? g_sink_rt.pitch.tracked_hz : 0.0f, out_voc_follow, out_voc_shift,
                             out_matrix_mille);
            r = vocoder_tick(&g_sink_rt.vocoder_r, r, (float)SAMPLE_RATE,
                             out_voc_carrier_hz, out_voc_detune, out_voc_mix,
                             g_sink_rt.pitch.valid ? g_sink_rt.pitch.tracked_hz : 0.0f, out_voc_follow, out_voc_shift,
                             out_matrix_mille);
        }

        /* 4. Bitcrusher */
        if (out_bitcrush_bits > 0 || out_bitcrush_ds > 1)
        {
            l = bitcrush_tick(&g_sink_rt.bitcrusher_l, l, out_bitcrush_bits, out_bitcrush_ds);
            r = bitcrush_tick(&g_sink_rt.bitcrusher_r, r, out_bitcrush_bits, out_bitcrush_ds);
        }

        /* 5. 9-Band Stereo Parametric EQ */
        if (out_eq_on)
        {
            for (int bi = 0; bi < GHA_EQ_BANDS; bi++)
            {
                l = biquad_tick(&g_sink_eq_l[bi], l);
                r = biquad_tick(&g_sink_eq_r[bi], r);
            }
            l *= out_eq_post_gain;
            r *= out_eq_post_gain;
        }

        /* 6. Voice Bandpass (HPF & LPF) */
        if (out_voice_hpf_hz > 0)
        {
            l = biquad_tick(&g_sink_rt.voice_hpf_l, l);
            r = biquad_tick(&g_sink_rt.voice_hpf_r, r);
        }
        if (out_voice_lpf_hz > 0)
        {
            l = biquad_tick(&g_sink_rt.voice_lpf_l, l);
            r = biquad_tick(&g_sink_rt.voice_lpf_r, r);
        }

        /* 7. Stutter Gate */
        if (out_stutter_hz > 0)
        {
            g_sink_rt.stutter_phase += (float)out_stutter_hz / (float)SAMPLE_RATE;
            if (g_sink_rt.stutter_phase >= 1.0f)
                g_sink_rt.stutter_phase -= 1.0f;
            float target_gate = (g_sink_rt.stutter_phase < (float)out_stutter_duty * 0.001f) ? 1.0f : 0.0f;
            g_sink_rt.stutter_gain += 0.05f * (target_gate - g_sink_rt.stutter_gain);
            l *= g_sink_rt.stutter_gain;
            r *= g_sink_rt.stutter_gain;
        }
        else if (g_sink_rt.stutter_gain < 1.0f)
        {
            /* Smoothly re-open the gate when stutter is disabled - without
             * this branch the gain froze at whatever value it held when the
             * effect switched off, leaving playback permanently attenuated
             * until another preset re-enabled the chopper. */
            g_sink_rt.stutter_gain += 0.05f * (1.0f - g_sink_rt.stutter_gain);
            l *= g_sink_rt.stutter_gain;
            r *= g_sink_rt.stutter_gain;
        }

        /* 8. Stereo Tape Delay */
        if (out_delay_on)
        {
            l = delay_tick(&g_sink_rt.delay_l, l, d_samples, d_fb, d_mix);
            r = delay_tick(&g_sink_rt.delay_r, r, d_samples, d_fb, d_mix);
        }

        /* 9. Stereo Schroeder Reverb */
        if (out_reverb_on)
        {
            l = reverb_tick(&g_sink_rt.reverb_l, l, r_room, r_damp, r_width, r_mix);
            r = reverb_tick(&g_sink_rt.reverb_r, r, r_room, r_damp, r_width, r_mix);
        }

        lhead = ring_push(&g_sink_l, lhead, l);
        rhead = ring_push(&g_sink_r, rhead, r);
    }
    pw_stream_queue_buffer(ctx->stream, b);
}

/* Sink playback RT callback. Drains per-channel rings and interleaves
 * into a stereo F32 buffer for the physical output device. */
static void cb_sink_out_process(void *userdata)
{
    struct stream_ctx *ctx = userdata;
    struct pw_buffer *b = pw_stream_dequeue_buffer(ctx->stream);
    if (!b)
        return;
    struct spa_buffer *buf = b->buffer;
    if (!buf->datas[0].data)
    {
        pw_stream_queue_buffer(ctx->stream, b);
        return;
    }
    uint32_t stride = sizeof(float) * 2; /* stereo interleaved */
    uint32_t n_avail = buf->datas[0].maxsize / stride;
    uint32_t n_samples = n_avail;
    if (b->requested && b->requested < n_samples)
        n_samples = (uint32_t)b->requested;
    float *out = buf->datas[0].data;

    uint32_t tail = atomic_load_explicit(&g_sink_r.tail, memory_order_relaxed);

    for (uint32_t i = 0; i < n_samples; i++)
    {
        float l, r;
        ring_pop_stereo(&g_sink_l, &g_sink_r, &tail, &l, &r);
        out[i * 2 + 0] = l;
        out[i * 2 + 1] = r;
    }
    atomic_store_explicit(&g_sink_l.tail, tail, memory_order_release);
    atomic_store_explicit(&g_sink_r.tail, tail, memory_order_release);

    buf->datas[0].chunk->offset = 0;
    buf->datas[0].chunk->stride = (int32_t)stride;
    buf->datas[0].chunk->size = n_samples * stride;
    pw_stream_queue_buffer(ctx->stream, b);
}

static void cb_state_changed(void *userdata, enum pw_stream_state old,
                             enum pw_stream_state state, const char *error)
{
    struct stream_ctx *ctx = userdata;
    const char *name = (ctx == &g_in_ctx)       ? "capture "
                       : (ctx == &g_out_ctx)    ? "source  "
                       : (ctx == &g_mon_ctx)    ? "monitor "
                       : (ctx == &g_sink_in_ctx)  ? "sink-in "
                       : (ctx == &g_sink_out_ctx) ? "sink-out"
                                                  : "?       ";
    fprintf(stderr, "[ghelper-audio] %s %s -> %s%s%s\n",
            name,
            pw_stream_state_as_string(old),
            pw_stream_state_as_string(state),
            error ? " err=" : "",
            error ? error : "");
    if (state == PW_STREAM_STATE_ERROR &&
        (ctx == &g_out_ctx || ctx == &g_sink_in_ctx))
        pw_main_loop_quit(g_app.loop);
}

static const struct pw_stream_events in_stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = cb_state_changed,
    .process = cb_in_process,
};

static const struct pw_stream_events out_stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = cb_state_changed,
    .process = cb_out_process,
};

static const struct pw_stream_events mon_stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = cb_state_changed,
    .process = cb_mon_process,
};

static const struct pw_stream_events sink_in_stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = cb_state_changed,
    .process = cb_sink_in_process,
};

static const struct pw_stream_events sink_out_stream_events = {
    PW_VERSION_STREAM_EVENTS,
    .state_changed = cb_state_changed,
    .process = cb_sink_out_process,
};

/* g_in_ctx / g_out_ctx / g_sink_in_ctx / g_sink_out_ctx are defined ahead
 * of the callbacks so the state-change handler can identify which stream
 * emitted an event. */

/* ---------------------------------------------------------------------------
 *   Audio frame emitter (60 Hz timer in main loop)
 * ---------------------------------------------------------------------------*/

/* Create (or recreate) the capture stream and connect it. When target is
 * non-NULL and not "default", PW_KEY_TARGET_OBJECT pins the stream to that
 * source. DONT_FALLBACK prevents routing into our own virtual devices;
 * DONT_MOVE keeps metadata from moving it. The controller retries failed
 * streams after target hotplug.
 * For "System default" (NULL / "" / "default") WirePlumber picks the default
 * source and normal automatic routing tracks default changes.
 *
 * Baking PW_KEY_TARGET_OBJECT into the initial properties (rather than
 * updating them on a live stream) guarantees WirePlumber sees the target on
 * the node's very first link evaluation — no timing dependency on whether
 * the stream has reached STREAMING state yet. */
static int create_capture_stream(struct app *app, const char *target)
{
    int has_target = (target && target[0] && strcmp(target, "default") != 0);

    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_NODE_NAME, "ghelper-audio-capture",
        PW_KEY_NODE_DESCRIPTION, "G-Helper Audio capture",
        PW_KEY_NODE_AUTOCONNECT, "true",
        "node.dont-fallback", has_target ? "true" : "false",
        "node.dont-move", has_target ? "true" : "false",
        NULL);

    if (has_target)
        pw_properties_set(props, PW_KEY_TARGET_OBJECT, target);

    app->in_stream = pw_stream_new_simple(
        pw_main_loop_get_loop(app->loop),
        "ghelper-audio-capture",
        props,
        &in_stream_events,
        &g_in_ctx);
    if (!app->in_stream)
        return -1;
    g_in_ctx.stream = app->in_stream;

    uint8_t pod_buf[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    const struct spa_pod *params[1];
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat,
                                           &SPA_AUDIO_INFO_RAW_INIT(
                                                   .format = SPA_AUDIO_FORMAT_F32,
                                                   .channels = 1,
                                                   .rate = SAMPLE_RATE));

    return pw_stream_connect(app->in_stream,
                             PW_DIRECTION_INPUT, PW_ID_ANY,
                             PW_STREAM_FLAG_AUTOCONNECT |
                                 PW_STREAM_FLAG_MAP_BUFFERS |
                                 PW_STREAM_FLAG_RT_PROCESS,
                             params, 1);
}

/* Retarget the capture stream to a new source. Destroys the old stream and
 * creates a fresh one via create_capture_stream so the target is in the
 * initial properties — see the comment there for why this matters. */
static void apply_pending_source(struct app *app)
{
    if (!atomic_load(&app->src_pending))
        return;
    atomic_store(&app->src_pending, 0);

    const char *target = app->src_pending_target;

    if (app->in_stream)
        pw_stream_destroy(app->in_stream);
    app->in_stream = NULL;
    g_in_ctx.stream = NULL;

    if (create_capture_stream(app, target) < 0)
        fprintf(stderr, "[ghelper-audio] reconnect to '%s' failed\n", target);
    else
    {
        snprintf(app->current_source, sizeof(app->current_source), "%s", target);
        fprintf(stderr, "[ghelper-audio] capture target set to '%s'\n",
                target[0] ? target : "default");
    }
}

/* Create (or recreate) the monitor playback stream targeting the active
 * physical output device. When target is non-NULL and not "default",
 * PW_KEY_TARGET_OBJECT pins the stream to that device. */
static int create_monitor_stream(struct app *app, const char *target)
{
    int has_target = (target && target[0] && strcmp(target, "default") != 0);

    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Playback",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_NODE_NAME, "ghelper-audio-monitor",
        PW_KEY_NODE_DESCRIPTION, "Dusky Audio monitor",
        PW_KEY_NODE_AUTOCONNECT, "true",
        "node.dont-fallback", has_target ? "true" : "false",
        "node.dont-move", has_target ? "true" : "false",
        "node.link-group", "ghelper-sink-group",
        NULL);

    if (has_target)
        pw_properties_set(props, PW_KEY_TARGET_OBJECT, target);

    app->mon_stream = pw_stream_new_simple(
        pw_main_loop_get_loop(app->loop),
        "ghelper-audio-monitor",
        props,
        &mon_stream_events,
        &g_mon_ctx);
    if (!app->mon_stream)
        return -1;
    g_mon_ctx.stream = app->mon_stream;

    uint8_t pod_buf[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    const struct spa_pod *params[1];
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat,
                                           &SPA_AUDIO_INFO_RAW_INIT(
                                                   .format = SPA_AUDIO_FORMAT_F32,
                                                   .channels = 1,
                                                   .rate = SAMPLE_RATE));

    return pw_stream_connect(app->mon_stream,
                             PW_DIRECTION_OUTPUT,
                             PW_ID_ANY,
                             PW_STREAM_FLAG_AUTOCONNECT |
                                 PW_STREAM_FLAG_MAP_BUFFERS |
                                 PW_STREAM_FLAG_RT_PROCESS,
                             params, 1);
}

/* Connect or disconnect the monitor playback stream. */
static void apply_pending_monitor(struct app *app)
{
    int p = atomic_exchange(&app->mon_pending, 0);
    if (p == 0)
        return;

    if (p == 2)
    {
        if (app->mon_stream)
        {
            pw_stream_destroy(app->mon_stream);
            app->mon_stream = NULL;
            g_mon_ctx.stream = NULL;
        }
        fprintf(stderr, "[ghelper-audio] monitor disconnected\n");
        return;
    }

    if (p == 1)
    {
        if (app->mon_stream)
        {
            pw_stream_destroy(app->mon_stream);
            app->mon_stream = NULL;
            g_mon_ctx.stream = NULL;
        }

        const char *target = app->sink_pending_target;
        if (create_monitor_stream(app, target) < 0)
            fprintf(stderr, "[ghelper-audio] monitor connect failed\n");
        else
            fprintf(stderr, "[ghelper-audio] monitor connected targeting '%s'\n",
                    target[0] ? target : "default");
    }
}

/* Create (or recreate) the sink output stream targeting a specific physical
 * output device. When target is non-NULL and not "default", PW_KEY_TARGET_OBJECT
 * pins the stream to that device. DONT_FALLBACK and DONT_MOVE preserve
 * that choice; the controller retries failed streams after target hotplug.
 * For "default" (NULL / "" / "default") WirePlumber picks the default sink.
 * Both cases share the same link-group as sink_in to prevent feedback loops. */
static int create_sink_out_stream(struct app *app, const char *target)
{
    int has_target = (target && target[0] && strcmp(target, "default") != 0);

    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Playback",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_NODE_NAME, "ghelper-audio-sink-out",
        PW_KEY_NODE_DESCRIPTION, "Dusky Audio playback",
        PW_KEY_NODE_AUTOCONNECT, "true",
        "node.dont-fallback", has_target ? "true" : "false",
        "node.dont-move", has_target ? "true" : "false",
        /* Must share link-group with sink_in so WirePlumber routes
         * to real hardware, not back into our own virtual sink. */
        "node.link-group", "ghelper-sink-group",
        NULL);

    if (has_target)
        pw_properties_set(props, PW_KEY_TARGET_OBJECT, target);

    app->sink_out_stream = pw_stream_new_simple(
        pw_main_loop_get_loop(app->loop),
        "ghelper-audio-sink-out",
        props,
        &sink_out_stream_events,
        &g_sink_out_ctx);
    if (!app->sink_out_stream)
        return -1;
    g_sink_out_ctx.stream = app->sink_out_stream;

    uint8_t pod_buf[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    const struct spa_pod *params[1];
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat,
                                           &SPA_AUDIO_INFO_RAW_INIT(
                                                   .format = SPA_AUDIO_FORMAT_F32,
                                                   .channels = 2,
                                                   .rate = SAMPLE_RATE));

    return pw_stream_connect(app->sink_out_stream,
                             PW_DIRECTION_OUTPUT, PW_ID_ANY,
                             PW_STREAM_FLAG_AUTOCONNECT |
                                 PW_STREAM_FLAG_MAP_BUFFERS |
                                 PW_STREAM_FLAG_RT_PROCESS,
                             params, 1);
}

/* Retarget the sink output stream to a new physical output device.
 * Destroys and recreates the stream — same pattern as apply_pending_source. */
static void apply_pending_sink_target(struct app *app)
{
    if (!atomic_load(&app->sink_tgt_pending))
        return;
    atomic_store(&app->sink_tgt_pending, 0);

    const char *target = app->sink_pending_target;

    if (app->sink_out_stream)
        pw_stream_destroy(app->sink_out_stream);
    app->sink_out_stream = NULL;
    g_sink_out_ctx.stream = NULL;

    if (create_sink_out_stream(app, target) < 0)
        fprintf(stderr, "[ghelper-audio] sink-out reconnect to '%s' failed\n", target);
    else
    {
        snprintf(app->current_sink, sizeof(app->current_sink), "%s", target);
        fprintf(stderr, "[ghelper-audio] sink-out target set to '%s'\n",
                target[0] ? target : "default");
    }

    /* Retarget monitor stream if active */
    if (app->mon_stream)
    {
        pw_stream_destroy(app->mon_stream);
        app->mon_stream = NULL;
        g_mon_ctx.stream = NULL;
        create_monitor_stream(app, target);
    }
}

static void on_timer(void *userdata, uint64_t expirations)
{
    struct app *app = userdata;
    (void)expirations;

    apply_pending_source(app);
    apply_pending_monitor(app);
    apply_pending_sink_target(app);

    static uint32_t last_seq = 0;
    uint32_t seq = atomic_load(&g_snap.seq);
    if (seq == last_seq)
        return;
    int slot = atomic_load(&g_snap.published_slot);
    int expected = 1;
    if (!atomic_compare_exchange_strong(&g_snap.slot_state[slot], &expected, 3))
        return;
    last_seq = seq;

    struct gha_frame f;
    memset(&f, 0, sizeof(f));
    f.magic = GHA_MAGIC;
    f.version = GHA_PROTOCOL_VERSION;
    f.seq = app->seq_emit++;
    f.flags = atomic_load(&g_snap.flags);
    f.vad_prob = atomic_load(&g_snap.vad_prob);

    int n = g_snap.rms_n[slot];
    if (n > 0)
    {
        float rms_in = sqrtf(g_snap.rms_in_sum[slot] / n);
        float rms_out = sqrtf(g_snap.rms_out_sum[slot] / n);
        f.rms_in_db = fmaxf(-80.0f, 20.0f * log10f(rms_in + 1e-9f));
        f.rms_out_db = fmaxf(-80.0f, 20.0f * log10f(rms_out + 1e-9f));
    }
    else
    {
        f.rms_in_db = f.rms_out_db = -80.0f;
    }

    /* Level of the aligned dry-minus-final-blend signal, not an estimate
     * of removed noise. Speech changed by the model contributes too. */
    int dn = g_snap.rnn_diff_n[slot];
    if (dn > 0)
    {
        float diff_rms_pcm = sqrtf(g_snap.rnn_diff_sum[slot] / dn);
        float diff_rms = diff_rms_pcm * (1.0f / 32768.0f);
        f.processing_delta_dbfs = fmaxf(-80.0f,
                                         20.0f * log10f(diff_rms + 1e-9f));
    }
    else
    {
        f.processing_delta_dbfs = -80.0f;
    }

    f.tracked_pitch_hz = atomic_load(&g_snap.tracked_pitch_hz);

    fwrite(&f, sizeof(f), 1, stdout);
    fflush(stdout);
    atomic_store(&g_snap.slot_state[slot], 0);
}

/* ---------------------------------------------------------------------------
 *   Stdin command parser (line-based, runs on main loop via spa_source)
 * ------------------------------------------------------------------------- */

static void parse_cmd(char *line)
{
    while (*line == ' ' || *line == '\t')
        line++;
    char *nl = strchr(line, '\n');
    if (nl)
        *nl = '\0';
    if (line[0] == '\0' || line[0] == '#')
        return;

    if (!strncmp(line, "RNN ", 4))
    {
        atomic_store(&g_params.rnnoise_on, atoi(line + 4) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_NOISE ", 10))
    {
        /* Two-Way Output Noise Cancellation on/off. When enabled, the
         * cb_sink_in_process callback runs RNNoise on each stereo channel.
         * When disabled, audio passes through the sink untouched. */
        int on = atoi(line + 10) ? 1 : 0;
        atomic_store(&g_params.out_rnnoise_on, on);
        fprintf(stderr, "[ghelper-audio] output noise cancellation %s\n",
                on ? "ON" : "OFF");
    }
    else if (!strncmp(line, "OUT_AGG ", 8))
    {
        /* Output RNNoise aggressiveness (per-mille, 0..1000). Same semantics
         * as the input pipeline's AGG command. */
        int v = atoi(line + 8);
        if (v < 0) v = 0;
        if (v > 1000) v = 1000;
        atomic_store(&g_params.out_rnn_aggressiveness, v);
    }
    else if (!strncmp(line, "SINK_TGT ", 9))
    {
        /* Retarget the sink output playback to a specific physical output
         * device. Same deferred-apply pattern as SRC for input capture. */
        const char *val = line + 9;
        while (*val == ' ') val++;
        if (strcmp(g_app.current_sink, val) == 0 && !atomic_load(&g_app.sink_tgt_pending) &&
            g_app.sink_out_stream &&
            pw_stream_get_state(g_app.sink_out_stream, NULL) >= PW_STREAM_STATE_CONNECTING)
            return;
        snprintf(g_app.sink_pending_target, sizeof(g_app.sink_pending_target),
                 "%s", val);
        atomic_store(&g_app.sink_tgt_pending, 1);
        fprintf(stderr, "[ghelper-audio] sink target pending: '%s'\n", val);
    }
    else if (!strncmp(line, "EQ ", 3))
    {
        atomic_store(&g_params.eq_on, atoi(line + 3) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_EQ ", 7))
    {
        atomic_store(&g_params.out_eq_on, atoi(line + 7) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_EGN ", 8))
    {
        int v = atoi(line + 8);
        if (v < -3600) v = -3600;
        if (v > 3600) v = 3600;
        atomic_store(&g_params.out_eq_gain_centidb, v);
    }
    else if (!strncmp(line, "OUT_EQB ", 8))
    {
        int idx, type, hz, q, g;
        if (sscanf(line + 8, "%d %d %d %d %d", &idx, &type, &hz, &q, &g) == 5 && idx >= 0 && idx < GHA_EQ_BANDS)
        {
            if (type < 0) type = 0;
            if (type > 6) type = 6;
            if (hz < 10) hz = 10;
            if (hz > 20000) hz = 20000;
            if (q < 50) q = 50;
            if (q > 5000) q = 5000;
            if (g < -3600) g = -3600;
            if (g > 3600) g = 3600;
            atomic_store(&g_params.out_eq[idx].type, type);
            atomic_store(&g_params.out_eq[idx].freq_hz, hz);
            atomic_store(&g_params.out_eq[idx].q_mille, q);
            atomic_store(&g_params.out_eq[idx].gain_centidb, g);
        }
    }
    else if (!strncmp(line, "OUT_VOC ", 8))
    {
        atomic_store(&g_params.out_vocoder_on, atoi(line + 8) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_VOP ", 8))
    {
        int mix, hz, atk, rel, det, follow, shift;
        if (sscanf(line + 8, "%d %d %d %d %d %d %d",
                   &mix, &hz, &atk, &rel, &det, &follow, &shift) == 7)
        {
            if (mix < 0) mix = 0;
            if (mix > 1000) mix = 1000;
            if (det < 0) det = 0;
            if (det > 200) det = 200;
            if (hz < 50) hz = 50;
            if (hz > 880) hz = 880;
            if (atk < 1) atk = 1;
            if (atk > 200) atk = 200;
            if (rel < 5) rel = 5;
            if (rel > 500) rel = 500;
            if (shift < -24) shift = -24;
            if (shift > 24) shift = 24;
            atomic_store(&g_params.out_vocoder_mix, mix);
            atomic_store(&g_params.out_vocoder_carrier_hz, hz);
            atomic_store(&g_params.out_vocoder_attack_ms, atk);
            atomic_store(&g_params.out_vocoder_release_ms, rel);
            atomic_store(&g_params.out_vocoder_detune, det);
            atomic_store(&g_params.out_vocoder_follow, follow ? 1 : 0);
            atomic_store(&g_params.out_vocoder_shift_semis, shift);
        }
    }
    else if (!strncmp(line, "OUT_MTX ", 8))
    {
        int v = atoi(line + 8);
        if (v < 0) v = 0;
        if (v > 1000) v = 1000;
        atomic_store(&g_params.out_matrix_intensity_mille, v);
    }
    else if (!strncmp(line, "OUT_PSH ", 8))
    {
        int v = atoi(line + 8);
        if (v < -2400) v = -2400;
        if (v > 2400) v = 2400;
        atomic_store(&g_params.out_pitch_shift_centisemis, v);
    }
    else if (!strncmp(line, "OUT_ATN ", 8))
    {
        atomic_store(&g_params.out_autotune_on, atoi(line + 8) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_ATT ", 8))
    {
        int v = atoi(line + 8);
        if (v < 0) v = 0;
        if (v > 1000) v = 1000;
        atomic_store(&g_params.out_voice_autotune_target_hz, v);
    }
    else if (!strncmp(line, "OUT_BCR ", 8))
    {
        int bits = 0, ds = 1;
        if (sscanf(line + 8, "%d %d", &bits, &ds) >= 1)
        {
            if (bits < 0) bits = 0;
            if (bits > 15) bits = 15;
            if (ds < 1) ds = 1;
            if (ds > 64) ds = 64;
            atomic_store(&g_params.out_bitcrush_bits, bits);
            atomic_store(&g_params.out_bitcrush_downsample, ds);
        }
    }
    else if (!strncmp(line, "OUT_BPF ", 8))
    {
        int hpf = 0, lpf = 0;
        if (sscanf(line + 8, "%d %d", &hpf, &lpf) >= 1)
        {
            if (hpf < 0) hpf = 0;
            if (hpf > 2000) hpf = 2000;
            if (lpf < 0) lpf = 0;
            if (lpf > 20000) lpf = 20000;
            atomic_store(&g_params.out_voice_bpf_hpf_hz, hpf);
            atomic_store(&g_params.out_voice_bpf_lpf_hz, lpf);
        }
    }
    else if (!strncmp(line, "OUT_STT ", 8))
    {
        int hz = 0, duty = 500;
        if (sscanf(line + 8, "%d %d", &hz, &duty) >= 1)
        {
            if (hz < 0) hz = 0;
            if (hz > 40) hz = 40;
            if (duty < 50) duty = 50;
            if (duty > 950) duty = 950;
            atomic_store(&g_params.out_voice_stutter_hz, hz);
            atomic_store(&g_params.out_voice_stutter_duty_mille, duty);
        }
    }
    else if (!strncmp(line, "OUT_DLY ", 8))
    {
        atomic_store(&g_params.out_delay_on, atoi(line + 8) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_DLP ", 8))
    {
        int ms, fb, mix;
        if (sscanf(line + 8, "%d %d %d", &ms, &fb, &mix) == 3)
        {
            if (ms < 0) ms = 0;
            if (ms > 1000) ms = 1000;
            if (fb < 0) fb = 0;
            if (fb > 950) fb = 950;
            if (mix < 0) mix = 0;
            if (mix > 1000) mix = 1000;
            atomic_store(&g_params.out_delay_ms, ms);
            atomic_store(&g_params.out_delay_feedback, fb);
            atomic_store(&g_params.out_delay_mix, mix);
        }
    }
    else if (!strncmp(line, "OUT_RVB ", 8))
    {
        atomic_store(&g_params.out_reverb_on, atoi(line + 8) ? 1 : 0);
    }
    else if (!strncmp(line, "OUT_RVP ", 8))
    {
        int rm, dp, wd, mx;
        if (sscanf(line + 8, "%d %d %d %d", &rm, &dp, &wd, &mx) == 4)
        {
            if (rm < 0) rm = 0;
            if (rm > 1000) rm = 1000;
            if (dp < 0) dp = 0;
            if (dp > 1000) dp = 1000;
            if (wd < 0) wd = 0;
            if (wd > 1000) wd = 1000;
            if (mx < 0) mx = 0;
            if (mx > 1000) mx = 1000;
            atomic_store(&g_params.out_reverb_room, rm);
            atomic_store(&g_params.out_reverb_damp, dp);
            atomic_store(&g_params.out_reverb_width, wd);
            atomic_store(&g_params.out_reverb_mix, mx);
        }
    }
    else if (!strncmp(line, "DLY ", 4))
    {
        atomic_store(&g_params.delay_on, atoi(line + 4) ? 1 : 0);
    }
    else if (!strncmp(line, "RVB ", 4))
    {
        atomic_store(&g_params.reverb_on, atoi(line + 4) ? 1 : 0);
    }
    else if (!strncmp(line, "VOC ", 4))
    {
        atomic_store(&g_params.vocoder_on, atoi(line + 4) ? 1 : 0);
    }
    else if (!strncmp(line, "VOP ", 4))
    {
        int mix, hz, atk, rel, det, follow, shift;
        if (sscanf(line + 4, "%d %d %d %d %d %d %d",
                   &mix, &hz, &atk, &rel, &det, &follow, &shift) == 7)
        {
            if (mix < 0) mix = 0;
            if (mix > 1000) mix = 1000;
            if (det < 0) det = 0;
            if (det > 200) det = 200;
            if (hz < 50)
                hz = 50;
            if (hz > 880)
                hz = 880;
            if (atk < 1)
                atk = 1;
            if (atk > 200)
                atk = 200;
            if (rel < 5)
                rel = 5;
            if (rel > 500)
                rel = 500;
            if (shift < -24)
                shift = -24;
            if (shift > 24)
                shift = 24;
            atomic_store(&g_params.vocoder_mix, mix);
            atomic_store(&g_params.vocoder_carrier_hz, hz);
            atomic_store(&g_params.vocoder_attack_ms, atk);
            atomic_store(&g_params.vocoder_release_ms, rel);
            atomic_store(&g_params.vocoder_detune, det);
            atomic_store(&g_params.vocoder_follow, follow ? 1 : 0);
            atomic_store(&g_params.vocoder_shift_semis, shift);
        }
    }
    else if (!strncmp(line, "MON ", 4))
    {
        int on = atoi(line + 4) ? 1 : 0;
        atomic_store(&g_params.monitor_on, on);
        if ((g_app.mon_stream != NULL) == (on != 0) && !atomic_load(&g_app.mon_pending))
            return;
        /* Queue a connect/disconnect for the loop thread to process. The
         * actual pw_stream_connect/disconnect must NOT run from this stdin
         * callback - we defer to the main-loop iteration via a flag. */
        atomic_store(&g_app.mon_pending, on ? 1 : 2);
    }
    else if (!strncmp(line, "SRC ", 4))
    {
        const char *t = line + 4;
        while (*t == ' ')
            t++;
        if (strcmp(g_app.current_source, t) == 0 && !atomic_load(&g_app.src_pending) &&
            g_app.in_stream &&
            pw_stream_get_state(g_app.in_stream, NULL) >= PW_STREAM_STATE_CONNECTING)
            return;
        size_t n = strlen(t);
        if (n >= sizeof(g_app.src_pending_target))
            n = sizeof(g_app.src_pending_target) - 1;
        memcpy(g_app.src_pending_target, t, n);
        g_app.src_pending_target[n] = '\0';
        atomic_store(&g_app.src_pending, 1);
    }
    else if (!strncmp(line, "EQB ", 4))
    {
        int idx, type, hz, q, g;
        if (sscanf(line + 4, "%d %d %d %d %d", &idx, &type, &hz, &q, &g) == 5 && idx >= 0 && idx < GHA_EQ_BANDS)
        {
            /* Clamp to the ranges biquad_design is numerically well-behaved
             * in: freq at/below ~10 Hz or at/above Nyquist yields degenerate
             * coefficients, tiny Q explodes alpha, and gain beyond +/-36 dB
             * serves no purpose the post-EQ trim cannot do safely. */
            if (type < 0) type = 0;
            if (type > 6) type = 6;
            if (hz < 10) hz = 10;
            if (hz > 20000) hz = 20000;
            if (q < 50) q = 50;
            if (q > 5000) q = 5000;
            if (g < -3600) g = -3600;
            if (g > 3600) g = 3600;
            atomic_store(&g_params.eq[idx].type, type);
            atomic_store(&g_params.eq[idx].freq_hz, hz);
            atomic_store(&g_params.eq[idx].q_mille, q);
            atomic_store(&g_params.eq[idx].gain_centidb, g);
        }
    }
    else if (!strncmp(line, "DLP ", 4))
    {
        int ms, fb, mix;
        if (sscanf(line + 4, "%d %d %d", &ms, &fb, &mix) == 3)
        {
            /* Clamp: feedback >= 1000 per-mille is a marginally-stable comb
             * that howls; ms beyond the 1 s line just wraps against the
             * delay buffer clamp anyway. */
            if (ms < 0) ms = 0;
            if (ms > 1000) ms = 1000;
            if (fb < 0) fb = 0;
            if (fb > 950) fb = 950;
            if (mix < 0) mix = 0;
            if (mix > 1000) mix = 1000;
            atomic_store(&g_params.delay_ms, ms);
            atomic_store(&g_params.delay_feedback, fb);
            atomic_store(&g_params.delay_mix, mix);
        }
    }
    else if (!strncmp(line, "RVP ", 4))
    {
        int rm, dp, wd, mx;
        if (sscanf(line + 4, "%d %d %d %d", &rm, &dp, &wd, &mx) == 4)
        {
            /* Clamp: room drives comb feedback (0.28 + room*0.7), so any
             * value above 1000 per-mille makes the tank diverge. */
            if (rm < 0) rm = 0;
            if (rm > 1000) rm = 1000;
            if (dp < 0) dp = 0;
            if (dp > 1000) dp = 1000;
            if (wd < 0) wd = 0;
            if (wd > 1000) wd = 1000;
            if (mx < 0) mx = 0;
            if (mx > 1000) mx = 1000;
            atomic_store(&g_params.reverb_room, rm);
            atomic_store(&g_params.reverb_damp, dp);
            atomic_store(&g_params.reverb_width, wd);
            atomic_store(&g_params.reverb_mix, mx);
        }
    }
    else if (!strncmp(line, "VOL ", 4))
    {
        /* Master gain in per-mille. 0=mute, 1000=unity, 2000=+6 dB.
         * The same post-gain signal feeds the virtual source and monitor.
         * Values above unity may exceed full scale; there is no limiter. */
        int v = atoi(line + 4);
        if (v < 0)
            v = 0;
        if (v > 2000)
            v = 2000;
        atomic_store(&g_params.master_vol_mille, v);
    }
    else if (!strncmp(line, "EGN ", 4))
    {
        /* Post-EQ uniform gain in centi-dB. Drives the response-curve
         * vertical drag gesture. Clamped to +/- 36 dB to keep the linear
         * multiplier in a sane range (10^(36/20) ~ 63 - still well below
         * float32 headroom for typical voice signals after EQ). */
        int v = atoi(line + 4);
        if (v < -3600)
            v = -3600;
        if (v > 3600)
            v = 3600;
        atomic_store(&g_params.eq_gain_centidb, v);
    }
    else if (!strncmp(line, "AGG ", 4))
    {
        /* RNNoise post-gate aggressiveness, per-mille (0..1000). Higher
         * values cut more residual hiss between phrases at the cost of
         * a faintly more "gated" character on speech tails. Range is
         * clamped so the gain at full close stays within [-24 dB, 0]. */
        int v = atoi(line + 4);
        if (v < 0)
            v = 0;
        if (v > 1000)
            v = 1000;
        atomic_store(&g_params.rnn_aggressiveness, v);
    }
    else if (!strncmp(line, "PSH ", 4))
    {
        /* Pitch-shift offset in centi-semitones (1 semitone = 100 centi).
         * Clamped to +/- 24 st - the granular shifter's quality degrades
         * past that range, and presets stay well within it. */
        int v = atoi(line + 4);
        if (v < -2400)
            v = -2400;
        if (v > 2400)
            v = 2400;
        atomic_store(&g_params.pitch_shift_centisemis, v);
    }
    else if (!strncmp(line, "ATN ", 4))
    {
        /* Autotune toggle. When on, the pitch tracker's tracked_hz is
         * snapped to the nearest equal-tempered semitone and the shifter
         * uses that ratio instead of the manual PSH offset. */
        int v = atoi(line + 4) ? 1 : 0;
        atomic_store(&g_params.autotune_on, v);
    }
    else if (!strncmp(line, "BCR ", 4))
    {
        /* Bit-crusher: "<bits> <downsample>" or just "<bits>". bits=0
         * keeps the bypass path; downsample defaults to 1 if omitted. */
        int bits = 0, ds = 1;
        if (sscanf(line + 4, "%d %d", &bits, &ds) >= 1)
        {
            if (bits < 0)
                bits = 0;
            if (bits > 15)
                bits = 15;
            if (ds < 1)
                ds = 1;
            if (ds > 64)
                ds = 64;
            atomic_store(&g_params.bitcrush_bits, bits);
            atomic_store(&g_params.bitcrush_downsample, ds);
        }
    }
    else if (!strncmp(line, "MTX ", 4))
    {
        /* Matrix intensity per-mille (0..1000). Picks how much ring-mod
         * + tanh drive + sub-octave blend the vocoder carrier gets.
         * 0 = clean Kraftwerk / Hawking timbre; 1000 = full Sentinel
         * comm-rig character. */
        int v = atoi(line + 4);
        if (v < 0)
            v = 0;
        if (v > 1000)
            v = 1000;
        atomic_store(&g_params.matrix_intensity_mille, v);
    }
    else if (!strncmp(line, "BPF ", 4))
    {
        /* Voice band-pass: "<hpf_hz> <lpf_hz>". 0 in either field
         * bypasses that side. Telephone band would be "300 3400";
         * Vader-muffled would be "0 3000". */
        int hpf = 0, lpf = 0;
        if (sscanf(line + 4, "%d %d", &hpf, &lpf) >= 1)
        {
            if (hpf < 0)
                hpf = 0;
            if (hpf > 2000)
                hpf = 2000;
            if (lpf < 0)
                lpf = 0;
            if (lpf > 20000)
                lpf = 20000;
            atomic_store(&g_params.voice_bpf_hpf_hz, hpf);
            atomic_store(&g_params.voice_bpf_lpf_hz, lpf);
        }
    }
    else if (!strncmp(line, "STT ", 4))
    {
        /* Stutter gate: "<hz> <duty_mille>". 0 Hz disables. Typical
         * Cylon range is 4..8 Hz with duty around 500. */
        int hz = 0, duty = 500;
        if (sscanf(line + 4, "%d %d", &hz, &duty) >= 1)
        {
            if (hz < 0)
                hz = 0;
            if (hz > 40)
                hz = 40;
            if (duty < 50)
                duty = 50;
            if (duty > 950)
                duty = 950;
            atomic_store(&g_params.voice_stutter_hz, hz);
            atomic_store(&g_params.voice_stutter_duty_mille, duty);
        }
    }
    else if (!strncmp(line, "ATT ", 4))
    {
        /* Autotune target Hz. 0 = chromatic snap (T-Pain mode);
         * >0 = monotone snap to this Hz (Hawking mode). */
        int v = atoi(line + 4);
        if (v < 0)
            v = 0;
        if (v > 1000)
            v = 1000;
        atomic_store(&g_params.voice_autotune_target_hz, v);
    }
    else if (!strcmp(line, "QUIT"))
    {
        fprintf(stderr, "[ghelper-audio] received QUIT\n");
        pw_main_loop_quit(g_app.loop);
    }
}

static char stdin_buf[4096];
static size_t stdin_len;
static bool stdin_discarding;

static void on_stdin(void *userdata, int fd, uint32_t mask)
{
    struct app *app = userdata;
    if (mask & SPA_IO_IN)
    {
        char input[4096];
        ssize_t n = read(fd, input, sizeof(input));
        if (n < 0)
        {
            if (errno == EINTR || errno == EAGAIN)
                return;
            pw_main_loop_quit(app->loop);
            return;
        }
        if (n == 0)
        {
            pw_main_loop_quit(app->loop);
            return;
        }
        for (ssize_t i = 0; i < n; i++)
        {
            if (input[i] == '\n')
            {
                if (!stdin_discarding)
                {
                    stdin_buf[stdin_len] = '\0';
                    parse_cmd(stdin_buf);
                }
                stdin_len = 0;
                stdin_discarding = false;
            }
            else if (!stdin_discarding)
            {
                if (stdin_len == sizeof(stdin_buf) - 1)
                {
                    stdin_discarding = true;
                    fprintf(stderr, "[ghelper-audio] discarded oversized stdin line\n");
                }
                else
                    stdin_buf[stdin_len++] = input[i];
            }
        }
    }
    else if (mask & (SPA_IO_HUP | SPA_IO_ERR))
        pw_main_loop_quit(app->loop);
}

/* ---------------------------------------------------------------------------
 *   Main
 * ------------------------------------------------------------------------- */

static void do_quit(void *userdata, int signal_number)
{
    struct app *app = userdata;
    (void)signal_number;
    pw_main_loop_quit(app->loop);
}

int main(int argc, char *argv[])
{
    if (argc == 2 && strcmp(argv[1], "--protocol-version") == 0)
    {
        printf("%u\n", GHA_PROTOCOL_VERSION);
        return 0;
    }
    pw_init(&argc, &argv);
    params_init();

    g_rt.rn = rnnoise_create(NULL);
    if (!g_rt.rn)
    {
        fprintf(stderr, "rnnoise_create failed\n");
        return 1;
    }
    /* Pre-design EQ defaults */
    rt_refresh_eq();
    /* Pre-RNNoise rumble filter: 70 Hz high-pass with Butterworth Q. Q
     * is intentionally low (0.707) so the slope is gentle and male voice
     * fundamentals (~80-200 Hz) are not coloured. Biquad type 3 in this
     * file is the high-pass design (type 4 is the low-pass - passing 4
     * here silently turned the whole denoise path into a sub-70 Hz LPF
     * and made the virtual mic inaudible whenever Denoise was on). */
    biquad_design(&g_rt.rnn_hpf, 3 /* HPF */, (float)SAMPLE_RATE,
                  70.0f, 0.707f, 0.0f);
    /* Start with the gate fully open so the first frame after launch
     * does not begin in the attenuated state and slowly ramp up. */
    g_rt.gate_gain = 1.0f;
    g_rt.hangover_left = 0;
    g_rt.vad_ema = 0.0f;
    /* Autotune ratio smoother starts at unity so the very first sample
     * is not multiplied by zero (which would output silence even after
     * the smoother has converged). */
    g_rt.autotune_ratio_smooth = 1.0f;
    /* Voice-stage biquads start "undesigned"; the per-callback refresh
     * detects the first non-zero param and triggers biquad_design. */
    g_rt.voice_hpf_designed_hz = -1;
    g_rt.voice_lpf_designed_hz = -1;
    /* Stutter gate starts fully open so any preset transition is silent. */
    g_rt.stutter_gain = 1.0f;
    g_rt.stutter_phase = 0.0f;
    /* Pre-design vocoder so the first audio frame doesn't pay the cost. */
    vocoder_design(&g_rt.vocoder, (float)SAMPLE_RATE,
                   atomic_load(&g_params.vocoder_attack_ms),
                   atomic_load(&g_params.vocoder_release_ms));

    /* ---- Output (Two-Way) Noise Cancellation: initialise dual RNNoise
     * instances for independent stereo channel denoising. Each gets its
     * own 70 Hz rumble HPF and starts with the gate fully open. */
    g_sink_ch_l.rn = rnnoise_create(NULL);
    g_sink_ch_r.rn = rnnoise_create(NULL);
    if (!g_sink_ch_l.rn || !g_sink_ch_r.rn)
    {
        fprintf(stderr, "rnnoise_create failed for output sink channels\n");
        return 1;
    }
    biquad_design(&g_sink_ch_l.rnn_hpf, 3 /* HPF */, (float)SAMPLE_RATE,
                  70.0f, 0.707f, 0.0f);
    biquad_design(&g_sink_ch_r.rnn_hpf, 3 /* HPF */, (float)SAMPLE_RATE,
                  70.0f, 0.707f, 0.0f);
    g_sink_ch_l.gate_gain = 1.0f;
    g_sink_ch_l.vad_ema = 0.0f;
    g_sink_ch_l.hangover_left = 0;
    g_sink_ch_r.gate_gain = 1.0f;
    g_sink_ch_r.vad_ema = 0.0f;
    g_sink_ch_r.hangover_left = 0;

    /* ---- Output Playback DSP RT State Initialization ---- */
    rt_refresh_sink_eq();

    g_sink_rt.autotune_ratio_smooth = 1.0f;
    g_sink_rt.voice_hpf_designed_hz = -1;
    g_sink_rt.voice_lpf_designed_hz = -1;
    g_sink_rt.stutter_gain = 1.0f;
    g_sink_rt.stutter_phase = 0.0f;

    vocoder_design(&g_sink_rt.vocoder_l, (float)SAMPLE_RATE,
                   atomic_load(&g_params.out_vocoder_attack_ms),
                   atomic_load(&g_params.out_vocoder_release_ms));
    vocoder_design(&g_sink_rt.vocoder_r, (float)SAMPLE_RATE,
                   atomic_load(&g_params.out_vocoder_attack_ms),
                   atomic_load(&g_params.out_vocoder_release_ms));

    struct app *app = &g_app;
    memset(app, 0, sizeof(*app));
    app->loop = pw_main_loop_new(NULL);
    if (!app->loop)
    {
        fprintf(stderr, "PipeWire main loop creation failed\n");
        return 1;
    }

    pw_loop_add_signal(pw_main_loop_get_loop(app->loop), SIGINT, do_quit, app);
    pw_loop_add_signal(pw_main_loop_get_loop(app->loop), SIGTERM, do_quit, app);

    uint8_t pod_buf[1024];

    /* ---- Capture stream (PW_DIRECTION_INPUT): records from the default
     * mic into our raw ring. media.category=Capture so wireplumber routes
     * a real source into it. NULL target = system default. */
    if (create_capture_stream(app, NULL) < 0)
    {
        fprintf(stderr, "in_stream connect failed\n");
        return 1;
    }
    snprintf(app->current_source, sizeof(app->current_source), "default");

    /* ---- Virtual-source stream (PW_DIRECTION_OUTPUT): exposed as a
     * recordable Audio/Source visible to PulseAudio compat (pactl,
     * pavucontrol). Apps record FROM this stream. */
    struct pw_properties *out_props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Playback",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_NODE_NAME, "ghelper-audio",
        PW_KEY_NODE_NICK, "Dusky Mic",
        PW_KEY_NODE_DESCRIPTION, "Dusky Mic",
        PW_KEY_MEDIA_CLASS, "Audio/Source",
        PW_KEY_NODE_VIRTUAL, "true",
        "audio.channels", "1",
        "audio.position", "MONO",
        PW_KEY_PRIORITY_SESSION, "1000",
        NULL);

    app->out_stream = pw_stream_new_simple(
        pw_main_loop_get_loop(app->loop),
        "ghelper-audio",
        out_props,
        &out_stream_events,
        &g_out_ctx);
    if (!app->out_stream)
    {
        fprintf(stderr, "virtual source creation failed\n");
        return 1;
    }
    g_out_ctx.stream = app->out_stream;

    struct spa_pod_builder b_out = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    const struct spa_pod *out_params[1];
    out_params[0] = spa_format_audio_raw_build(&b_out, SPA_PARAM_EnumFormat,
                                               &SPA_AUDIO_INFO_RAW_INIT(
                                                       .format = SPA_AUDIO_FORMAT_F32,
                                                       .channels = 1,
                                                       .rate = SAMPLE_RATE));

    if (pw_stream_connect(app->out_stream,
                          PW_DIRECTION_OUTPUT,
                          PW_ID_ANY,
                          PW_STREAM_FLAG_MAP_BUFFERS |
                              PW_STREAM_FLAG_RT_PROCESS,
                          out_params, 1) < 0)
    {
        fprintf(stderr, "out_stream connect failed\n");
        return 1;
    }

    /* ---- Monitor playback stream: created on-demand by apply_pending_monitor()
     * with PW_KEY_TARGET_OBJECT dynamically bound to the active physical output sink. */
    app->mon_stream = NULL;
    g_mon_ctx.stream = NULL;

    /* ---- Output Noise Cancellation: Virtual Sink (Audio/Sink, stereo).
     * Apps (Discord, Zoom, browsers, games) route their audio into this
     * sink when the user selects it as their output device. The
     * cb_sink_in_process callback optionally runs RNNoise on each channel.
     *
     * The sink_in_stream is ALWAYS connected from startup so apps can find
     * it and route audio into it (even when noise cancellation is off —
     * in that case audio passes through untouched). The sink_out_stream
     * (playback to real speakers) is also always connected so there is
     * no audio gap when toggling noise cancellation on/off. */
    struct pw_properties *sink_in_props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Communication",
        PW_KEY_NODE_NAME, "ghelper-audio-sink",
        PW_KEY_NODE_NICK, "Dusky Audio",
        PW_KEY_NODE_DESCRIPTION, "Dusky Audio",
        PW_KEY_MEDIA_CLASS, "Audio/Sink",
        PW_KEY_NODE_VIRTUAL, "true",
        "audio.channels", "2",
        "audio.position", "FL,FR",
        PW_KEY_PRIORITY_SESSION, "900",
        /* link-group tells WirePlumber that this sink and its playback
         * partner (sink_out_stream) form a pair: WP must not route the
         * playback stream back into this sink (feedback loop). This is
         * the standard PipeWire pattern for virtual sink + loopback. */
        "node.link-group", "ghelper-sink-group",
        NULL);

    app->sink_in_stream = pw_stream_new_simple(
        pw_main_loop_get_loop(app->loop),
        "ghelper-audio-sink",
        sink_in_props,
        &sink_in_stream_events,
        &g_sink_in_ctx);
    if (!app->sink_in_stream)
    {
        fprintf(stderr, "virtual sink creation failed\n");
        return 1;
    }
    g_sink_in_ctx.stream = app->sink_in_stream;

    struct spa_pod_builder b_sink_in = SPA_POD_BUILDER_INIT(pod_buf, sizeof(pod_buf));
    const struct spa_pod *sink_in_params[1];
    sink_in_params[0] = spa_format_audio_raw_build(&b_sink_in, SPA_PARAM_EnumFormat,
                                                   &SPA_AUDIO_INFO_RAW_INIT(
                                                           .format = SPA_AUDIO_FORMAT_F32,
                                                           .channels = 2,
                                                           .rate = SAMPLE_RATE));

    if (pw_stream_connect(app->sink_in_stream,
                          PW_DIRECTION_INPUT, PW_ID_ANY,
                          PW_STREAM_FLAG_AUTOCONNECT |
                              PW_STREAM_FLAG_MAP_BUFFERS |
                              PW_STREAM_FLAG_RT_PROCESS,
                          sink_in_params, 1) < 0)
    {
        fprintf(stderr, "sink_in_stream connect failed\n");
        return 1;
    }

    /* Sink playback stream: routes cleaned audio to real speakers/headphones.
     * Always connected from startup for seamless passthrough/denoise toggle.
     * Uses create_sink_out_stream so it supports target retargeting later. */
    if (create_sink_out_stream(app, "default") < 0)
    {
        fprintf(stderr, "sink_out_stream connect failed\n");
        return 1;
    }
    snprintf(app->current_sink, sizeof(app->current_sink), "default");

    /* 60 Hz timer to emit audio frames on stdout */
    struct timespec interval = {0, 16000000}; /* 16 ms */
    app->timer = pw_loop_add_timer(pw_main_loop_get_loop(app->loop),
                                   on_timer, app);
    if (!app->timer)
    {
        fprintf(stderr, "PipeWire timer creation failed\n");
        return 1;
    }
    pw_loop_update_timer(pw_main_loop_get_loop(app->loop),
                         app->timer, &interval, &interval, false);

    /* stdin watch for line commands */
    app->stdin_src = pw_loop_add_io(pw_main_loop_get_loop(app->loop),
                                    STDIN_FILENO,
                                    SPA_IO_IN | SPA_IO_HUP | SPA_IO_ERR,
                                    false,
                                    on_stdin, app);
    if (!app->stdin_src)
    {
        fprintf(stderr, "PipeWire command input creation failed\n");
        return 1;
    }

    fprintf(stderr, "[ghelper-audio] ready (proto v%u, fs=%d Hz, frame=%d)\n",
            GHA_PROTOCOL_VERSION, SAMPLE_RATE, RNN_FRAME);

    pw_main_loop_run(app->loop);

    if (app->stdin_src)
        pw_loop_destroy_source(pw_main_loop_get_loop(app->loop), app->stdin_src);
    if (app->timer)
        pw_loop_destroy_source(pw_main_loop_get_loop(app->loop), app->timer);

    /* Guard every destroy: mon_stream (and in pathological startup-failure
     * paths, others) may legitimately be NULL, and pw_stream_destroy(NULL)
     * faults on PipeWire 1.6.x - the old unguarded sequence SIGSEGV'd on
     * every clean shutdown where the monitor was never enabled. */
    if (app->sink_out_stream)
        pw_stream_destroy(app->sink_out_stream);
    if (app->sink_in_stream)
        pw_stream_destroy(app->sink_in_stream);
    if (app->mon_stream)
        pw_stream_destroy(app->mon_stream);
    if (app->out_stream)
        pw_stream_destroy(app->out_stream);
    if (app->in_stream)
        pw_stream_destroy(app->in_stream);
    pw_main_loop_destroy(app->loop);
    rnnoise_destroy(g_sink_ch_r.rn);
    rnnoise_destroy(g_sink_ch_l.rn);
    rnnoise_destroy(g_rt.rn);
    pw_deinit();
    return 0;
}
