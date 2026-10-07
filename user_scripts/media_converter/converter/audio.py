"""Extract a selected audio track into a common delivery format."""

from pathlib import Path
from .core import ConversionError, Media, ffmpeg, input_args, clip_duration, output_trim_args

FORMATS = frozendict(
    flac=("-c:a", "flac"), wav=("-c:a", "pcm_s24le"),
    mp3=("-c:a", "libmp3lame", "-q:a", "2"),
    m4a=("-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"),
)


def convert(media: Media, output: Path, args, ui):
    if args.track >= len(media.audio):
        raise ConversionError(f"Audio track {args.track} is unavailable ({len(media.audio)} tracks found).")
    options = list(FORMATS[args.format])
    if args.format == "flac":
        # Float decoders do not carry a meaningful integer bit depth; encode 24-bit.
        depth = int(media.audio[args.track].get("bits_per_raw_sample", 0) or 0)
        if depth == 0 or depth > 24:
            options += ["-bits_per_raw_sample", "24"]
    ffmpeg([*input_args(media, args), *output_trim_args(media, args), "-map", f"0:a:{args.track}",
            *options, str(output)], ui, f"Extract audio · {args.format}",
           clip_duration(media, args))
