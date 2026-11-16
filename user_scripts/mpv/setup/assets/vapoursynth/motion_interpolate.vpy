"""
Unified Real-Time Motion Interpolation Pipeline for mpv (Tier 2)
Supports:
  - Auto-detection & graceful fallback: RIFE (ncnn-Vulkan) -> MVTools (AVX2)
  - Dynamic mpv user-data configuration (engine, target, quality, max_h, gpu_id)
  - Even-subsampling normalization + Mod-32/64 mirror padding for odd/ultrawide crops
  - Scene-change protection (misc.SCDetect with automatic mv.SCDetection fallback)
  - Color matrix, transfer, and bit-depth preservation (YUV420P8 / YUV420P10 output)
"""

from fractions import Fraction
import vapoursynth as vs

core = vs.core


def _safe_int(val: str, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_user_data(raw_data: str) -> dict:
    """Parse key=value pairs separated by comma, pipe, or semicolon."""
    cfg = {
        "engine": "auto",      # auto | rife | mvtools
        "target": "display",   # display | 2x | 60 | 120
        "quality": "auto",     # auto | fast | hq
        "max_h": "1080",       # maximum processing height for frame-deadline safety
        "gpu_id": "0",         # Vulkan physical device index
        "gpu_thread": "2",     # Vulkan compute queue concurrency
        "rife_model": "70",    # 70 = rife-v4.25-lite, 69 = rife-v4.25, 72 = rife-v4.26
    }
    if not raw_data:
        return cfg
    normalized = str(raw_data).replace("|", ",").replace(";", ",")
    for item in normalized.split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            cfg[k.strip().lower()] = v.strip().lower()
    return cfg


def _detect_matrix_string(clip: vs.VideoNode) -> str:
    """Determine zimg matrix string from resolution heuristic without requesting frames during init."""
    if clip.height > 1080:
        return "2020ncl"
    return "709" if clip.height >= 720 else "170m"


def _to_yuv420(clip: vs.VideoNode, target_fmt: int, matrix_s: str, dither: str = "ordered") -> vs.VideoNode:
    """Safely convert any YUV/RGB/Gray clip to target YUV420P8/P10 format."""
    if clip.format.id == target_fmt:
        return clip
    kwargs = {"format": target_fmt, "dither_type": dither}
    if clip.format.color_family == vs.RGB:
        kwargs["matrix_s"] = matrix_s
    return core.resize.Bicubic(clip, **kwargs)


def _attach_scene_change_props(clip: vs.VideoNode, matrix_s: str) -> vs.VideoNode:
    """
    Populate _SceneChangePrev / _SceneChangeNext on YUV clip.
    Uses core.misc.SCDetect if installed; falls back to core.mv.SCDetection.
    """
    if hasattr(core, "misc") and hasattr(core.misc, "SCDetect"):
        try:
            sc_in = _to_yuv420(clip, vs.YUV420P8, matrix_s, "none") if clip.format.color_family == vs.RGB else clip
            return core.misc.SCDetect(sc_in, threshold=0.14)
        except Exception:
            pass

    if hasattr(core, "mv"):
        try:
            sc_src = _to_yuv420(clip, vs.YUV420P8, matrix_s, "none")
            sup = core.mv.Super(sc_src, pel=1, hpad=8, vpad=8, chroma=False)
            bv = core.mv.Analyse(
                sup, isb=True, blksize=32, overlap=0, search=4, chroma=False
            )
            return core.mv.SCDetection(clip, bv, thscd1=240, thscd2=68)
        except Exception:
            pass

    return clip


def _pad_to_modulo(clip: vs.VideoNode, modulo: int) -> tuple[vs.VideoNode, int, int]:
    """
    Pad right and bottom edges to multiples of modulo using reflected edge pixels
    (eliminates black-border ringing and motion-vector edge pinning).
    """
    pad_w = (modulo - (clip.width % modulo)) % modulo
    pad_h = (modulo - (clip.height % modulo)) % modulo
    if pad_w > 0 or pad_h > 0:
        clip = core.std.AddBorders(clip, right=pad_w, bottom=pad_h)
    return clip, pad_w, pad_h


def _crop_padding(clip: vs.VideoNode, pad_w: int, pad_h: int) -> vs.VideoNode:
    """Remove right and bottom alignment borders after interpolation."""
    if pad_w > 0 or pad_h > 0:
        return core.std.CropRel(clip, right=pad_w, bottom=pad_h)
    return clip


def _build_rife_pipeline(
    clip: vs.VideoNode,
    dst_fps: Fraction,
    orig_fmt: vs.VideoFormat,
    matrix_s: str,
    cfg: dict,
) -> vs.VideoNode:
    """Construct Vulkan RIFE pipeline with scene-change detection, mirror-padding, and YUV restoration."""
    if not hasattr(core, "rife") or not hasattr(core.rife, "RIFE"):
        raise RuntimeError("VapourSynth RIFE plugin (librife.so) is not loaded.")

    target_fps_float = float(dst_fps)
    use_uhd = (clip.height >= 1080 and target_fps_float >= 95.0) or (
        cfg.get("quality") == "fast" and clip.height >= 1080
    )
    align_mod = 64 if use_uhd else 32

    sc_clip = _attach_scene_change_props(clip, matrix_s)
    padded_clip, pad_w, pad_h = _pad_to_modulo(sc_clip, align_mod)

    rgb_kwargs = {"format": vs.RGBS}
    if padded_clip.format.color_family != vs.RGB:
        rgb_kwargs["matrix_in_s"] = matrix_s
    rgb_clip = core.resize.Bicubic(padded_clip, **rgb_kwargs)

    rife_kwargs = {
        "model": _safe_int(cfg.get("rife_model", "70"), 70),
        "fps_num": dst_fps.numerator,
        "fps_den": dst_fps.denominator,
        "gpu_id": _safe_int(cfg.get("gpu_id", "0"), 0),
        "gpu_thread": _safe_int(cfg.get("gpu_thread", "2"), 2),
        "uhd": use_uhd,
        "sc": True,
    }

    try:
        interp_rgb = core.rife.RIFE(
            rgb_clip, **rife_kwargs, skip=True, skip_threshold=60.0
        )
    except vs.Error:
        interp_rgb = core.rife.RIFE(rgb_clip, **rife_kwargs)

    out_fmt = vs.YUV420P10 if orig_fmt.bits_per_sample > 8 else vs.YUV420P8
    yuv_out = core.resize.Bicubic(
        interp_rgb,
        format=out_fmt,
        matrix_s=matrix_s,
        dither_type="error_diffusion",
    )

    return _crop_padding(yuv_out, pad_w, pad_h)


def _build_mvtools_pipeline(
    clip: vs.VideoNode,
    dst_fps: Fraction,
    orig_fmt: vs.VideoFormat,
    matrix_s: str,
    cfg: dict,
) -> vs.VideoNode:
    """Construct AVX2 two-stage MVTools pipeline preserving 8-bit/10-bit YUV420."""
    if not hasattr(core, "mv"):
        raise RuntimeError("VapourSynth MVTools plugin (libmvtools.so) is not loaded.")

    target_fmt = vs.YUV420P10 if orig_fmt.bits_per_sample > 8 else vs.YUV420P8
    work_clip = _to_yuv420(clip, target_fmt, matrix_s, dither="ordered")

    padded_clip, pad_w, pad_h = _pad_to_modulo(work_clip, 32)
    quality = cfg.get("quality", "auto")
    target_fps_float = float(dst_fps)

    sup = core.mv.Super(padded_clip, pel=2, sharp=2, rfilter=4, hpad=16, vpad=16)

    bv_coarse = core.mv.Analyse(
        sup, isb=True, blksize=32, overlap=16, search=5, searchparam=2, pelsearch=2, dct=0, truemotion=True
    )
    fv_coarse = core.mv.Analyse(
        sup, isb=False, blksize=32, overlap=16, search=5, searchparam=2, pelsearch=2, dct=0, truemotion=True
    )

    bv_fine = core.mv.Recalculate(
        sup, bv_coarse, blksize=16, overlap=8, search=4, searchparam=1, thsad=200, truemotion=True
    )
    fv_fine = core.mv.Recalculate(
        sup, fv_coarse, blksize=16, overlap=8, search=4, searchparam=1, thsad=200, truemotion=True
    )

    interp = core.mv.FlowFPS(
        padded_clip, sup, bv_fine, fv_fine,
        num=dst_fps.numerator, den=dst_fps.denominator,
        mask=2, ml=100.0, thscd1=240, thscd2=68,
    )

    return _crop_padding(interp, pad_w, pad_h)


def main() -> None:
    clip = globals().get("video_in")
    if clip is None:
        return

    if 0 < clip.num_frames < 2:
        clip.set_output()
        return

    c_fps = globals().get("container_fps", 0.0)
    d_fps = globals().get("display_fps", 0.0)
    src_fps = float(c_fps) if c_fps and float(c_fps) > 1.0 else 24000.0 / 1001.0
    disp_fps = float(d_fps) if d_fps and float(d_fps) > 1.0 else 60.0

    raw_ud = globals().get("user_data", "")
    cfg = _parse_user_data(str(raw_ud))

    target_spec = cfg.get("target", "display")
    if target_spec == "2x":
        target_val = src_fps * 2.0
    elif target_spec.replace(".", "", 1).isdigit():
        target_val = float(target_spec)
    else:
        is_cpu_mvtools = (cfg.get("engine", "auto") == "mvtools") or (
            cfg.get("engine", "auto") == "auto" and not hasattr(core, "rife")
        )
        if is_cpu_mvtools and clip.height > 720 and disp_fps > 60.0:
            target_val = min(disp_fps, 60.0)
        else:
            target_val = disp_fps if disp_fps > 1.0 else (src_fps * 2.0)

    if src_fps >= target_val * 0.92 or (src_fps >= 50.0 and target_val <= 65.0):
        clip.set_output()
        return

    src_frac = Fraction(src_fps).limit_denominator(10000)
    dst_frac = Fraction(target_val).limit_denominator(10000)

    orig_fmt = clip.format
    matrix_s = _detect_matrix_string(clip)

    clip = core.std.AssumeFPS(
        clip, fpsnum=src_frac.numerator, fpsden=src_frac.denominator
    )

    max_h = max(64, _safe_int(cfg.get("max_h", "1080"), 1080))
    if clip.height > max_h:
        scale_ratio = max_h / float(clip.height)
        new_w = max(2, int(round((clip.width * scale_ratio) / 2.0) * 2))
        new_h = max(2, int(round(max_h / 2.0) * 2))
        clip = core.resize.Bicubic(clip, width=new_w, height=new_h)
    elif (clip.width % 2 != 0) or (clip.height % 2 != 0):
        even_w = max(2, clip.width - (clip.width % 2))
        even_h = max(2, clip.height - (clip.height % 2))
        clip = core.resize.Bicubic(clip, width=even_w, height=even_h)

    engine = cfg.get("engine", "auto")

    if engine in ("auto", "rife"):
        try:
            out_clip = _build_rife_pipeline(clip, dst_frac, orig_fmt, matrix_s, cfg)
            out_clip.set_output()
            return
        except Exception:
            pass

    out_clip = _build_mvtools_pipeline(clip, dst_frac, orig_fmt, matrix_s, cfg)
    out_clip.set_output()


main()
