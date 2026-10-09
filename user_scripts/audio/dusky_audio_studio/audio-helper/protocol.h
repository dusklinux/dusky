/* Dusky Audio Studio: shared IPC protocol header.
 *
 * Binary audio-frame layout written on stdout (little-endian),
 * line-based commands read on stdin.
 *
 * Telemetry timer: 16 ms (~62.5 Hz) while capture frames are arriving.
 * Packet size: 36 bytes (16 header + 20 scalars).
 *
 * Protocol v4:
 *   - VAD always runs (RNNoise model invoked regardless of bypass flag),
 *     so vad_prob is meaningful even when rnnoise is bypassed.
 *   - processing_delta_dbfs is the RMS level of the aligned difference
 *     between dry input and the final RNNoise-stage blend. It is not noise attenuation.
 *     Reads -80 dBFS when RNNoise is bypassed.
 *   - VOP command takes 7 args including pitch-follow + transpose.
 */
#ifndef GHELPER_AUDIO_PROTOCOL_H
#define GHELPER_AUDIO_PROTOCOL_H

#include <stdint.h>

#define GHA_MAGIC 0x47484146u /* "GHAF" */
#define GHA_PROTOCOL_VERSION 4u

#define GHA_EQ_BANDS 9

#define GHA_FLAG_RNNOISE_ON     (1u << 0)
#define GHA_FLAG_EQ_ON          (1u << 1)
#define GHA_FLAG_DELAY_ON       (1u << 2)
#define GHA_FLAG_REVERB_ON      (1u << 3)
#define GHA_FLAG_MONITOR_ON     (1u << 4)
#define GHA_FLAG_VOCODER_ON     (1u << 5)
#define GHA_FLAG_OUT_RNNOISE_ON (1u << 6)
#define GHA_FLAG_OUT_EQ_ON      (1u << 7)

#pragma pack(push, 4)

struct gha_frame
{
    uint32_t magic;   /* GHA_MAGIC */
    uint32_t version; /* GHA_PROTOCOL_VERSION */
    uint32_t seq;     /* monotonically increasing */
    uint32_t flags;   /* GHA_FLAG_* */

    float vad_prob;           /* 0..1 voice activity probability from RNNoise */
    float rms_in_db;          /* raw input RMS dBFS, floor -80 */
    float rms_out_db;         /* post-chain RMS dBFS, floor -80; may exceed 0 */
    float processing_delta_dbfs; /* aligned dry-minus-processed RMS, dBFS */
    float tracked_pitch_hz;   /* detected voice pitch (0 when silence/no track) */

};

#pragma pack(pop)

static_assert(sizeof(struct gha_frame) == 36, "telemetry frame layout changed");

/* Stdin command format (line-terminated; ASCII controls, UTF-8 node names).
 * Maximum line length: 4095 bytes, excluding the terminating newline.
 *
 *   SRC <pw-node-name|default>
 *                       point the capture stream at a specific source
 *                       node. "default" or empty arg = let wireplumber
 *                       choose (PW_ID_ANY).
 *
 *   SINK_TGT <pw-node-name|default>
 *                       point the sink playback stream at a specific physical
 *                       output sink (speakers, headphones, bluetooth).
 *                       "default" or empty arg = let wireplumber choose.
 *
 *   MON <0|1>           monitor processed microphone on selected sink
 *
 *   RNN <0|1>           enable/disable microphone rnnoise (Input)
 *   OUT_NOISE <0|1>     enable/disable output speaker/headphone rnnoise (Two-Way)
 *   OUT_AGG <0..1000>   set output rnnoise aggressiveness per-mille
 *   VOC <0|1>           enable/disable vocoder
 *   EQ  <0|1>           enable/disable parametric EQ
 *   DLY <0|1>           enable/disable delay
 *   RVB <0|1>           enable/disable reverb
 *
 *   EQB <idx> <type> <freq_hz> <q_mille> <gain_centidb>
 *                       set EQ band idx (0..8), type 0=peak 1=lowshelf
 *                       2=highshelf 3=highpass 4=lowpass 5=notch
 *
 *   DLP <time_ms> <feedback_mille> <mix_mille>
 *                       set delay params
 *
 *   RVP <room_mille> <damp_mille> <tail_mille> <mix_mille>
 *                       set reverb params (Schroeder)
 *
 *   VOP <mix_mille> <carrier_hz> <attack_ms> <release_ms> <detune_mille> <follow_0_1> <shift_semis>
 *                       set vocoder params.
 *                       follow=1 makes the carrier track detected voice
 *                       pitch; shift_semis (-24..+24) transposes when
 *                       following. carrier_hz is used only when follow=0.
 *
 *   VOL <0..2000>       master gain per-mille for microphone and monitor.
 *                       1000 = unity, 2000 = +6 dB; peaks may exceed 0 dBFS.
 *   AGG <0..1000>       microphone RNNoise dry/wet and residual gate
 *   EGN <centidb>      microphone EQ post gain (-3600..3600)
 *   PSH <centisemis>   microphone pitch shift (-2400..2400)
 *   ATN <0|1> / ATT <hz>  microphone autotune and target (0=chromatic)
 *   BCR <bits> <hold_samples> / BPF <high_hz> <low_hz>
 *   STT <hz> <duty_mille> / MTX <intensity_mille>
 *   OUT_EQ / OUT_EQB / OUT_EGN, OUT_VOC / OUT_VOP / OUT_MTX,
 *   OUT_PSH / OUT_ATN / OUT_ATT / OUT_BCR / OUT_BPF / OUT_STT,
 *   OUT_DLY / OUT_DLP / OUT_RVB / OUT_RVP:
 *                       playback equivalents of microphone controls.
 *
 *   QUIT
 *
 * Frame flags layout (uint32 LE):
 *   bit 0: rnnoise on (Input)
 *   bit 1: EQ on
 *   bit 2: delay on
 *   bit 3: reverb on
 *   bit 4: monitor on
 *   bit 5: vocoder on
 *   bit 6: out_rnnoise on (Output / Two-Way)
 *   bit 7: output EQ on
 */

#endif
