"""Two-pass palette GIFs without whole-video buffering."""

from pathlib import Path
from .core import Media, ffmpeg, input_args, clip_duration, decode_seek, output_trim_args

PRESETS = frozendict(web=(480, 15), balanced=(720, 24), quality=(960, 30))


def convert(media: Media, output: Path, args, ui):
    video = media.video
    width, fps = PRESETS[args.preset]
    width = args.width or width
    fps = args.fps or fps
    duration = clip_duration(media, args)
    scale = f"scale=w='min(iw,{width})':h=-1:flags=lanczos:reset_sar=1"
    filters = []
    if decode_seek(media, args):
        filters += [f"trim=start={args.start}", "setpts=PTS-STARTPTS"]
    if args.interpolate:
        filters.append(scale)
        if duration is not None:
            # Supply lookahead frames so minterpolate does not truncate short clips.
            filters.append("tpad=stop_mode=clone:stop=2")
        filters.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1")
        if duration is not None:
            filters.append(f"trim=duration={duration}")
        ui.message("Motion interpolation enabled; this can be CPU intensive.", "33")
    else:
        # Do not inflate a low-frame-rate source merely to reach the preset cap.
        if source_fps := media.fps:
            fps = min(fps, float(source_fps))
        filters.append(f"fps={fps}")
        filters.append(scale)
    chain = ",".join(filters)
    palette = output.parent / "palette.png"
    mapping = ["-map", f"0:{video['index']}"]
    ffmpeg([*input_args(media, args), *mapping, "-vf", chain + ",palettegen=stats_mode=full",
            "-frames:v", "1", "-update", "1", str(palette)], ui, "Palette · pass 1/2", duration)
    graph = (f"[0:{video['index']}]{chain}[frames];"
             "[frames][1:v]paletteuse=dither=sierra2_4a:diff_mode=rectangle[gif]")
    ffmpeg([*input_args(media, args), "-i", str(palette),
            *output_trim_args(media, args, seek_in_filter=True), "-filter_complex", graph,
            "-map", "[gif]", "-loop", "0", str(output)], ui, "GIF · pass 2/2", duration)
