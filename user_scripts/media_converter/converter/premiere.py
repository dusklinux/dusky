"""Lossless remux where practical; ProRes editing masters otherwise."""

from pathlib import Path
from .core import ConversionError, Media, ffmpeg, input_args, clip_duration, output_trim_args

COPY_VIDEO = frozenset({"h264", "hevc", "prores", "dnxhd", "mpeg2video", "mjpeg"})
COPY_AUDIO = frozenset({"aac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"})


def convert(media: Media, output: Path, args, ui):
    video = media.video
    codec = video.get("codec_name")
    transcode = args.mode == "prores" or (args.mode == "auto" and codec not in COPY_VIDEO)
    if not transcode and codec not in COPY_VIDEO:
        raise ConversionError(f"{codec} is outside the supported MOV remux codecs; use --mode prores.")
    if not transcode and args.fps is not None:
        raise ConversionError("--fps requires --mode prores (remux preserves video timing).")
    if not transcode and (args.start or args.duration is not None):
        raise ConversionError("Accurate trimming requires --mode prores; remux cuts can be keyframe-bound.")
    command = [*input_args(media, args), *output_trim_args(media, args),
               "-map", f"0:{video['index']}", "-map", "0:a?"]
    if transcode:
        fps = str(args.fps) if args.fps else str(media.fps or 30)
        command += ["-c:v", "prores_ks", "-profile:v", "standard", "-pix_fmt", "yuv422p10le",
                    "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-r", fps, "-fps_mode:v", "cfr",
                    "-c:a", "pcm_s24le"]
        ui.message(f"ProRes 422 · CFR {fps} fps · all audio tracks as PCM 24-bit")
    else:
        command += ["-c:v", "copy"]
        if codec == "hevc":
            command += ["-tag:v", "hvc1"]
        for index, stream in enumerate(media.audio):
            codec = "copy" if stream.get("codec_name") in COPY_AUDIO else "pcm_s24le"
            command += [f"-c:a:{index}", codec]
        ui.message("Copy video · preserve all audio tracks · convert incompatible audio to PCM")
    command += ["-movflags", "+faststart", str(output)]
    ffmpeg(command, ui, "Premiere preparation", clip_duration(media, args))
