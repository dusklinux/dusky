"""H.264/AAC MP4 presets inspired by HandBrake's quality-based encoding."""

from pathlib import Path
from .core import Media, ffmpeg, input_args, clip_duration, output_trim_args

PRESETS = frozendict(fast=(23, "fast", 1920), balanced=(22, "medium", 1920),
                    quality=(18, "slow", 3840))


def convert(media: Media, output: Path, args, ui):
    video = media.video
    crf, speed, width = PRESETS[args.preset]
    crf = args.crf if args.crf is not None else crf
    width = args.width or width
    # Normalize SAR and retain aspect ratio, including portrait inputs; no upscaling.
    filters = [f"scale=w='min(iw,{width})':h=-1:flags=lanczos:reset_sar=1",
               "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
    command = [*input_args(media, args), *output_trim_args(media, args),
               "-map", f"0:{video['index']}", "-map", "0:a?",
               "-vf", ",".join(filters), "-c:v", "libx264", "-preset", speed,
               "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if media.audio:
        command += ["-c:a", "aac", "-b:a", "192k"]
    if args.fps:
        command += ["-r", str(args.fps), "-fps_mode:v", "cfr"]
    else:
        command += ["-fps_mode:v", "vfr"]
    command += ["-movflags", "+faststart", str(output)]
    ffmpeg(command, ui, f"H.264 · {speed} · CRF {crf}", clip_duration(media, args))
