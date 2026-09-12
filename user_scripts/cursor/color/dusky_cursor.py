#!/usr/bin/env python3
"""Dusky Cursor - matugen accent-colored cursor theme builder & applier.

Builds a 'Dusky' XCursor theme by recoloring the installed
Bibata-Modern-Classic bitmaps and applies it across every layer, on every
matugen theme switch (wired via '[templates.dusky_cursor]' post_hook).

Color model: the primary matugen accent becomes the cursor *outline* (light
accent border), and a deep dark version of the accent becomes the cursor
*fill* (middle portion of the cursor):

  regular shapes : dark pixels  -> DEEP_ACCENT (very dark accent fill)
                   light pixels -> ACCENT      (primary accent outline)
  spinner shapes : dark pixels  -> WATCH_BG    (matugen background)
  saturated pixels (watch hands) are copied byte-exact, never touched.

Pixel math runs entirely in XCursor's native *premultiplied* ARGB32 space:

  L  = 0.2126*R' + 0.7152*G' + 0.0722*B'     (premultiplied luma, 0..A)
  C' = (fill_c * (A - L) + outline_c * L) / 255

which is algebraically identical to unpremultiply -> sRGB lerp by
t = L/A -> re-premultiply, but with no division, no alpha==0 special case,
no float precision loss on low-alpha fringe pixels, and the invariant
C' <= A holds by construction. Alpha, hotspots, delays, nominal sizes and
comment chunks are byte-identical to the source.

Atomicity: the theme is built in a sibling staging directory inside the
icons dir, fingerprinted *inside itself*, then swapped into place with
renameat2(RENAME_EXCHANGE). The compositor never observes a half-built
theme, and a fingerprint can only ever describe the directory it lives in.

Layers applied (same conventions as 'cursor/size/cursor_size.py'):
  1. 'hyprctl setcursor Dusky <size>' (live compositor + XWayland).
     Same-name+same-size is a no-op inside Hyprland
     ('CCursorManager::loadTheme' early-return, upstream #6350), so a
     re-theme at an unchanged size stages size+/-2 first (nudge).
  2. 'gsettings' cursor-theme/cursor-size ('dconf' when no schemas).
  3. 'dbus-update-activation-environment --systemd' (new apps).
  4. '$XDG_CONFIG_HOME/hypr/edit_here/source/environment_variables.lua'
     (survives reboot / 'hyprctl reload' - atomic replace, Lua comment
     aware, only 'hl.env(...)' calls are ever rewritten).
  5. '$XDG_DATA_HOME/icons/default/index.theme' (XWayland fallback,
     only the 'Inherits=' line is touched).
  6. GTK 'settings.ini' cursor theme + size (gtk-3.0 / gtk-4.0).

Target stack: Arch Linux, Hyprland >= 0.56, Python >= 3.14, Pillow >= 12,
glibc >= 2.28 (renameat2). Nothing is hardcoded: theme, colors, sizes,
paths and user are derived from the matugen output, gsettings, Hyprland
IPC and the XDG base-directory spec.

Exit codes: 0 ok / nothing to do, 1 hard failure (source missing, build
failed, compositor or persistent Lua layer failed), 2 usage, 130 SIGINT.

Usage:
    dusky_cursor.py --apply        # matugen post_hook entrypoint (default)
    dusky_cursor.py --rebuild      # force rebuild + apply
    dusky_cursor.py --restore      # back to the source (Bibata) theme
    dusky_cursor.py --status       # print current state, change nothing
    dusky_cursor.py --check        # installed Dusky == what --apply would build?
    dusky_cursor.py --dry-run      # print what --apply/--restore would do
"""

import argparse
import colorsys
import contextlib
import ctypes
import fcntl
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

try:
    from PIL import Image, ImageMath
except ImportError:
    print("dusky_cursor: ERROR: the 'Pillow' module is required "
          "(Arch Linux: pacman -S python-pillow).", file=sys.stderr)
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

THEME_NAME = "Dusky"
DEFAULT_SOURCE_THEME = "Bibata-Modern-Classic"  # --source / $DUSKY_CURSOR_SOURCE
# Dusky's index.theme must NOT inherit the source theme: Hyprland 0.56
# CXCursorManager::loadTheme walks every inherited dir in sorted path order
# and keeps the first copy of each shape - "Bibata-..." < "Dusky" would
# silently shadow every Dusky pixel. Dusky mirrors every source entry, so
# hicolor is the only sane last resort.
THEME_INHERITS = "hicolor"
FINGERPRINT_VERSION = 5
FINGERPRINT_NAME = ".dusky-fingerprint.json"
SPINNER_SHAPES = frozenset({"wait", "left_ptr_watch"})
# Chroma threshold in premultiplied space: (max-min) <= max(0.30*max, 1 LSB).
# Bibata Classic is pure black/white/alpha except for the watch hands
# (chroma ~1.0); black<->hand AA keeps chroma constant, white<->hand AA
# halves it at worst at 50% coverage (0.5 > 0.30). The 1-LSB floor absorbs
# premultiplication rounding noise on low-alpha pixels, which is where the
# old straight-space threshold could misclassify (a=3 quantizes to 85-steps).
SAT_THRESHOLD = 0.30

XCUR_MAGIC = 0x72756358  # "Xcur" little-endian
XCUR_IMAGE_TYPE = 0xFFFD0002
XCUR_FILE_HEADER = 16
XCUR_IMAGE_HEADER = 36
XCUR_MAX_DIM = 0x7FFF  # libXcursor: width/height must be < 0x8000
XCUR_MAX_TOC = 4096
MAX_SHAPE_BYTES = 64 << 20
MAX_CURSOR_SIZE = 256
LOCK_TIMEOUT_S = 30.0
LOG_MAX_BYTES = 256 << 10

NOTIFY_TAG = "dusky_cursor"
GSETTINGS_SCHEMA = "org.gnome.desktop.interface"
ENV_KEYS = ("XCURSOR_SIZE", "HYPRCURSOR_SIZE", "XCURSOR_THEME", "HYPRCURSOR_THEME")

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
THEME_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
ENV_LINE_RE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")
INT_RE = re.compile(r"[0-9]{1,4}")

FALLBACK_ACCENT = "#faba72"
FALLBACK_BACKGROUND = "#18120c"

type RGB = tuple[int, int, int]

log = logging.getLogger("dusky_cursor")

# ---------------------------------------------------------------------------
# XDG locations - no hardcoded $HOME / usernames anywhere
# ---------------------------------------------------------------------------


def _xdg(var: str, fallback: Path) -> Path:
    val = os.environ.get(var, "").strip()
    return Path(val) if val.startswith("/") else fallback


HOME = Path.home()
CONFIG_HOME = _xdg("XDG_CONFIG_HOME", HOME / ".config")
CACHE_HOME = _xdg("XDG_CACHE_HOME", HOME / ".cache")
DATA_HOME = _xdg("XDG_DATA_HOME", HOME / ".local" / "share")
RUNTIME_DIR = _xdg("XDG_RUNTIME_DIR", CACHE_HOME)

MATUGEN_ENV = CONFIG_HOME / "matugen" / "generated" / "dusky-cursor.env"
MATUGEN_TUI_JSON = CONFIG_HOME / "matugen" / "generated" / "dusky_tui.json"
THEME_STATE_CONF = CONFIG_HOME / "dusky" / "settings" / "dusky_theme" / "state.conf"
USER_ENV_LUA = CONFIG_HOME / "hypr" / "edit_here" / "source" / "environment_variables.lua"
SIZE_STATE_FILE = CACHE_HOME / "hypr-cursor-size"
WORK_DIR = CACHE_HOME / "dusky-cursor"
HOOK_LOG = WORK_DIR / "hook.log"
LOCK_FILE = RUNTIME_DIR / "dusky-cursor.lock"
ICONS_DIR = DATA_HOME / "icons"
THEME_ROOT = ICONS_DIR / THEME_NAME
DEFAULT_INDEX_THEME = ICONS_DIR / "default" / "index.theme"

# ---------------------------------------------------------------------------
# Logging (stderr + rotating hook.log so the post_hook needs no redirection)
# ---------------------------------------------------------------------------


class _TtyFormatter(logging.Formatter):
    _CODES = {logging.DEBUG: "34", logging.INFO: "32", logging.WARNING: "33", logging.ERROR: "31"}

    def __init__(self, color: bool) -> None:
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        tag = f"[{record.levelname}]"
        if self._color:
            tag = f"\033[0;{self._CODES.get(record.levelno, '0')}m{tag}\033[0m"
        return f"{tag} {record.getMessage()}"


def setup_logging(quiet: bool, verbose: bool, log_file: bool = True) -> None:
    log.setLevel(logging.DEBUG)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR if quiet else logging.DEBUG if verbose else logging.INFO)
    err.setFormatter(_TtyFormatter(color=sys.stderr.isatty() and not os.environ.get("NO_COLOR")))
    log.addHandler(err)
    if not log_file:
        return  # read-only actions leave no trace (no dir, no hook.log)
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            HOOK_LOG, maxBytes=LOG_MAX_BYTES, backupCount=1, encoding="utf-8")
        fh.setLevel(logging.DEBUG if verbose else logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(fh)
    except OSError as e:
        log.debug("hook.log unavailable: %s", e)


# ---------------------------------------------------------------------------
# Process helpers (list-form argv only - never a shell)
# ---------------------------------------------------------------------------


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out")


def in_hyprland() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def atomic_write(path: Path, data: str | bytes) -> None:
    """POSIX atomic replace: temp file in the same directory + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o644
    with contextlib.suppress(OSError):
        if not path.is_symlink():
            mode = path.stat().st_mode & 0o777
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def notify(title: str, body: str, urgency: str = "low") -> None:
    if have("notify-send"):
        run(["notify-send", "-a", "Dusky Cursor", "-h",
             f"string:x-canonical-private-synchronous:{NOTIFY_TAG}",
             "-u", urgency, "-t", "2500", title, body])
    elif in_hyprland() and have("hyprctl"):
        run(["hyprctl", "notify", "5", "2500", "0", f"{title}: {body}"])


@contextlib.contextmanager
def exclusive_lock(path: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Non-blocking flock with a deadline: a wedged hook can never pile up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"lock {path} held by another instance for >{timeout:.0f}s") from None
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# Palette & mode
# ---------------------------------------------------------------------------


class Mode(StrEnum):
    DARK = "dark"
    LIGHT = "light"


@dataclass(frozen=True, slots=True)
class Palette:
    accent: str
    deep_accent: str
    outline: str
    background: str
    mode: Mode
    origin: str


def derive_deep_accent(accent_hex: str) -> str:
    """Derive a deep, very dark version of the accent color for the cursor middle fill."""
    r, g, b = hex_to_rgb(accent_hex)
    h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    # Target a deep, very dark accent tone (L ~ 0.08 - 0.12) with rich saturation
    target_l = max(0.06, min(0.12, l * 0.22)) if l > 0.15 else max(0.03, l * 0.4)
    target_s = min(1.0, max(s * 1.3, 0.7)) if s > 0.05 else s
    dr, dg, db = colorsys.hls_to_rgb(h, target_l, target_s)
    return f"#{int(round(dr * 255)):02x}{int(round(dg * 255)):02x}{int(round(db * 255)):02x}"


def valid_hex(value: object) -> str | None:
    if isinstance(value, str) and HEX_RE.fullmatch(value.strip()):
        return value.strip().lower()
    return None


def hex_to_rgb(value: str) -> RGB:
    r, g, b = bytes.fromhex(value.lstrip("#"))
    return (r, g, b)


def rel_luma(rgb: RGB) -> float:
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if m := ENV_LINE_RE.fullmatch(line):
            key, val = m.groups()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            out[key] = val.strip()
    return out


def detect_mode(background: str) -> Mode:
    """theme_ctl state owns THEME_MODE; otherwise derive it from the background."""
    with contextlib.suppress(OSError):
        for line in THEME_STATE_CONF.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.strip().partition("=")
            if sep and key.strip() == "THEME_MODE":
                val = val.strip().strip("'\"")
                if val in Mode:
                    return Mode(val)
    return Mode.LIGHT if rel_luma(hex_to_rgb(background)) > 0.5 else Mode.DARK


def load_palette() -> Palette:
    """matugen env > dusky_tui.json > fallback."""
    env = read_env_file(MATUGEN_ENV)
    accent = valid_hex(env.get("DUSKY_CURSOR_ACCENT"))
    background = valid_hex(env.get("DUSKY_CURSOR_BACKGROUND"))
    origin = f"matugen env ({MATUGEN_ENV})" if accent else ""
    if accent is None:
        try:
            data = json.loads(MATUGEN_TUI_JSON.read_text(encoding="utf-8"))
        except OSError, ValueError:
            data = None
        if isinstance(data, dict) and (hx := valid_hex(data.get("accent"))):
            accent, origin = hx, f"dusky_tui.json ({MATUGEN_TUI_JSON})"
    accent = accent or FALLBACK_ACCENT
    background = background or FALLBACK_BACKGROUND
    deep_accent = (
        valid_hex(env.get("DUSKY_CURSOR_DEEP_ACCENT"))
        or valid_hex(env.get("DUSKY_CURSOR_ACCENT_DARK"))
        or valid_hex(os.environ.get("DUSKY_CURSOR_DEEP_ACCENT"))
        or derive_deep_accent(accent)
    )
    outline = (
        valid_hex(env.get("DUSKY_CURSOR_OUTLINE"))
        or valid_hex(os.environ.get("DUSKY_CURSOR_OUTLINE"))
        or accent
    )
    pal = Palette(accent, deep_accent, outline, background, detect_mode(background), origin or "fallback defaults")
    log.debug("palette %s", pal)
    return pal


# ---------------------------------------------------------------------------
# Source theme discovery & validation
# ---------------------------------------------------------------------------


def icon_search_dirs() -> list[Path]:
    dirs = [ICONS_DIR, HOME / ".icons"]
    for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if base.startswith("/"):
            dirs.append(Path(base) / "icons")
    return list(dict.fromkeys(dirs))


def find_source_cursors(name: str) -> Path | None:
    for base in icon_search_dirs():
        cand = base / name / "cursors"
        with contextlib.suppress(OSError):
            if cand.is_dir():
                return cand
    return None


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    link: str | None  # symlink target (single path component) or None for a file
    size: int


def scan_cursors(cursors: Path) -> list[Entry]:
    """Enumerate a cursors/ dir defensively: plain files and *local* symlinks only.

    Symlink targets are constrained to a single sibling name (what Bibata
    aliases look like); anything absolute, dotted or slashed is rejected so
    a tampered source can never make Dusky point outside its own dir.
    """
    entries: list[Entry] = []
    with os.scandir(cursors) as it:
        for de in it:
            name = de.name
            if name.startswith("."):
                continue
            if de.is_symlink():
                target = os.readlink(de.path)
                if not target or "/" in target or target in {".", ".."}:
                    raise ValueError(f"{name}: unsafe symlink target {target!r}")
                entries.append(Entry(name, target, 0))
            elif de.is_file(follow_symlinks=False):
                size = de.stat(follow_symlinks=False).st_size
                if not XCUR_FILE_HEADER <= size <= MAX_SHAPE_BYTES:
                    raise ValueError(f"{name}: implausible size {size}")
                entries.append(Entry(name, None, size))
    if not entries:
        raise ValueError(f"{cursors}: no cursor entries")
    entries.sort(key=lambda e: e.name)
    names = {e.name for e in entries}
    kept: list[Entry] = []
    for e in entries:
        if e.link is not None and e.link not in names:
            log.warning("%s: dangling alias -> %s (skipped)", e.name, e.link)
            continue
        kept.append(e)
    return kept


def tree_digest(cursors: Path, entries: list[Entry]) -> str:
    """Content digest of a cursors/ tree (names, alias targets, file bytes)."""
    h = hashlib.blake2b(digest_size=20)
    buf = bytearray(1 << 20)
    view = memoryview(buf)
    for e in entries:
        h.update(e.name.encode() + b"\0")
        if e.link is not None:
            h.update(b"L" + e.link.encode() + b"\0")
            continue
        h.update(b"F")
        with open(cursors / e.name, "rb", buffering=0) as f:
            while n := f.readinto(view):
                h.update(view[:n])
    return h.hexdigest()


# ---------------------------------------------------------------------------
# XCursor parsing & premultiplied recolor
# ---------------------------------------------------------------------------


def iter_image_chunks(raw: bytearray, name: str) -> Iterator[tuple[int, int, int, int]]:
    """Yield (pixel_offset, pixel_count, width, height) for each image chunk,
    validating the container against the XCursor spec / libXcursor rules."""
    if len(raw) < XCUR_FILE_HEADER:
        raise ValueError(f"{name}: too short for XCursor")
    magic, hlen, _version, ntoc = struct.unpack_from("<4I", raw, 0)
    if magic != XCUR_MAGIC:
        raise ValueError(f"{name}: bad XCursor magic")
    if hlen < XCUR_FILE_HEADER or ntoc > XCUR_MAX_TOC or hlen + ntoc * 12 > len(raw):
        raise ValueError(f"{name}: corrupt file header (hlen={hlen} ntoc={ntoc})")
    for i in range(ntoc):
        typ, _subtype, pos = struct.unpack_from("<3I", raw, hlen + i * 12)
        if typ != XCUR_IMAGE_TYPE:
            continue  # comment chunks etc. are copied verbatim
        if pos + XCUR_IMAGE_HEADER > len(raw):
            raise ValueError(f"{name}: TOC entry {i} beyond EOF")
        chsize, ctyp, _csub, _cver, w, h, xhot, yhot, _delay = struct.unpack_from("<9I", raw, pos)
        if ctyp != typ or chsize < XCUR_IMAGE_HEADER:
            raise ValueError(f"{name}: chunk {i} header mismatch")
        if not (0 < w <= XCUR_MAX_DIM and 0 < h <= XCUR_MAX_DIM) or xhot > w or yhot > h:
            raise ValueError(f"{name}: chunk {i} bad geometry {w}x{h}+{xhot}+{yhot}")
        n = w * h
        off = pos + XCUR_IMAGE_HEADER  # libXcursor reads pixels right after the 36-byte header
        if off + n * 4 > len(raw):
            raise ValueError(f"{name}: chunk {i} pixel data truncated")
        yield off, n, w, h


_M = ImageMath.lambda_eval


def recolor_chunk(chunk: bytes, w: int, h: int, fill: RGB, outline: RGB) -> bytes:
    """Recolor one image chunk's BGRA pixels via C-level Pillow ops.

    Same premultiplied math as the docstring (integer threshold compare is
    exact for integer spread; ``int(v + 0.5)`` is half-up for v >= 0), so
    output is byte-identical to the previous numpy implementation (checked
    against stored output digests on every rebuild path test). Non-gray
    (saturated) and fully transparent pixels come back byte-exact.
    """
    try:
        im = Image.frombytes("RGBA", (w, h), chunk, "raw", "BGRA")
        bgra = True
    except ValueError:
        # No BGRA raw decoder in this Pillow: relabel bands and swap in C.
        q0, q1, q2, q3 = Image.frombytes("RGBA", (w, h), chunk, "raw", "RGBA").split()
        im = Image.merge("RGBA", (q2, q1, q0, q3))
        bgra = False
    R, G, B, A = im.split()
    RI, GI, BI, AI = R.convert("I"), G.convert("I"), B.convert("I"), A.convert("I")
    mx = _M(lambda a: a["max"](a["max"](a["b"], a["g"]), a["r"]), b=BI, g=GI, r=RI)
    mn = _M(lambda a: a["min"](a["min"](a["b"], a["g"]), a["r"]), b=BI, g=GI, r=RI)
    spread = _M(lambda a: a["s"] - a["m"], s=mx, m=mn)
    thr = _M(lambda a: a["int"](a["max"](a["m"] * SAT_THRESHOLD, 1.0)), m=mx.convert("F"))
    gray = _M(lambda a: (a["s"] <= a["t"]) & (a["a"] > 0), s=spread, t=thr, a=AI)
    if not gray.getbbox():
        return chunk
    Rf, Gf, Bf = R.convert("F"), G.convert("F"), B.convert("F")
    Af = AI.convert("F")
    lum = _M(lambda a: a["min"](0.2126 * a["r"] + 0.7152 * a["g"] + 0.0722 * a["b"], a["af"]),
             r=Rf, g=Gf, b=Bf, af=Af)
    dw = _M(lambda a: a["af"] - a["l"], af=Af, l=lum)
    inv = 1.0 / 255.0
    FR, FG, FB = float(fill[0]), float(fill[1]), float(fill[2])
    OR, OG, OB = float(outline[0]), float(outline[1]), float(outline[2])
    oR = _M(lambda a: a["int"](a["min"](a["v"] + 0.5, a["af"])),
            v=_M(lambda a: (FR * a["dw"] + OR * a["l"]) * inv, dw=dw, l=lum), af=Af)
    oG = _M(lambda a: a["int"](a["min"](a["v"] + 0.5, a["af"])),
            v=_M(lambda a: (FG * a["dw"] + OG * a["l"]) * inv, dw=dw, l=lum), af=Af)
    oB = _M(lambda a: a["int"](a["min"](a["v"] + 0.5, a["af"])),
            v=_M(lambda a: (FB * a["dw"] + OB * a["l"]) * inv, dw=dw, l=lum), af=Af)
    new = Image.merge("RGBA", (oR.convert("L"), oG.convert("L"), oB.convert("L"), A))
    # Mask pixels are exactly {0, 1}: table lookup, no per-value Python call.
    mask = gray.convert("L").point([0] + [255] * 255)
    res = Image.composite(new, im, mask)
    if bgra:
        return res.tobytes("raw", "BGRA")
    r2, g2, b2, a2 = res.split()
    return Image.merge("RGBA", (b2, g2, r2, a2)).tobytes()


def recolor_shape(src: Path, dst_dir: Path, fill: RGB, outline: RGB) -> None:
    raw = bytearray(src.read_bytes())
    for off, n, w, h in iter_image_chunks(raw, src.name):
        raw[off:off + n * 4] = recolor_chunk(bytes(raw[off:off + n * 4]), w, h, fill, outline)
    (dst_dir / src.name).write_bytes(raw)


# ---------------------------------------------------------------------------
# Theme build: staging dir -> fingerprint inside -> atomic directory exchange
# ---------------------------------------------------------------------------

_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def exchange_paths(a: Path, b: Path) -> None:
    """renameat2(2) RENAME_EXCHANGE: atomically swap two directory entries."""
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renameat2
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    fn.restype = ctypes.c_int
    if fn(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b), _RENAME_EXCHANGE) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(a), None, str(b))


def install_stage(stage: Path, dest: Path) -> None:
    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        dest.unlink()
    try:
        exchange_paths(stage, dest)
    except FileNotFoundError:
        os.rename(stage, dest)  # first install: nothing to exchange with
        return
    shutil.rmtree(stage, ignore_errors=True)  # stage now holds the previous theme


def fingerprint_path(root: Path | None = None) -> Path:
    return (root or THEME_ROOT) / FINGERPRINT_NAME


def read_fingerprint(root: Path | None = None) -> dict[str, object]:
    try:
        data = json.loads(fingerprint_path(root).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def want_fingerprint(pal: Palette, source_name: str, source_digest: str) -> dict[str, object]:
    return {
        "version": FINGERPRINT_VERSION,
        "accent": pal.accent,
        "deep_accent": pal.deep_accent,
        "outline": pal.outline,
        "watch_bg": pal.background,
        "mode": str(pal.mode),
        "source": source_name,
        "source_digest": source_digest,
    }


def fingerprint_matches(stored: dict[str, object], want: dict[str, object]) -> bool:
    return all(stored.get(k) == v for k, v in want.items())


def theme_meta(pal: Palette, source_name: str) -> tuple[str, str]:
    index = ("[Icon Theme]\n"
             f"Name={THEME_NAME}\n"
             f"Comment=Dusky Cursors - matugen accent {pal.accent} outline, deep accent {pal.deep_accent} fill "
             f"(recolored {source_name})\n"
             f"Inherits={THEME_INHERITS}\n")
    cursor = f"[Icon Theme]\nName={THEME_NAME}\nInherits={THEME_NAME}\n"
    return index, cursor


def build_theme(src_cursors: Path, entries: list[Entry], pal: Palette,
                source_name: str, source_digest: str) -> tuple[int, int]:
    """Recolor every shape into a staging dir and swap it live. Returns (files, links)."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{THEME_NAME}.build-", dir=ICONS_DIR))
    try:
        stage.chmod(0o755)
        cursors = stage / "cursors"
        cursors.mkdir(0o755)
        deep_accent = hex_to_rgb(pal.deep_accent)
        watch_bg = hex_to_rgb(pal.background)
        outline = hex_to_rgb(pal.outline)
        files = [e for e in entries if e.link is None]
        links = [e for e in entries if e.link is not None]

        def work(e: Entry) -> None:
            fill = watch_bg if e.name in SPINNER_SHAPES else deep_accent
            recolor_shape(src_cursors / e.name, cursors, fill, outline)

        workers = max(1, min(8, os.process_cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recolor") as ex:
            for _ in ex.map(work, files):
                pass  # re-raises the first worker exception
        for e in links:
            (cursors / e.name).symlink_to(e.link)  # type: ignore[arg-type]
        index, cursor = theme_meta(pal, source_name)
        (stage / "index.theme").write_text(index, encoding="utf-8")
        (stage / "cursor.theme").write_text(cursor, encoding="utf-8")
        fp = want_fingerprint(pal, source_name, source_digest)
        fp["output_digest"] = tree_digest(cursors, entries)
        fp["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (stage / FINGERPRINT_NAME).write_text(json.dumps(fp, indent=2) + "\n", encoding="utf-8")
        dfd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
        install_stage(stage, THEME_ROOT)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return len(files), len(links)


def verify_installed(entries: list[Entry], want: dict[str, object]) -> tuple[bool, str]:
    """Is the installed Dusky exactly what --apply would produce right now?"""
    stored = read_fingerprint()
    if not stored:
        return False, "not built (no fingerprint)"
    if not fingerprint_matches(stored, want):
        diff = [k for k, v in want.items() if stored.get(k) != v]
        return False, f"stale: {', '.join(diff)} changed"
    try:
        installed = scan_cursors(THEME_ROOT / "cursors")
    except (OSError, ValueError) as e:
        return False, f"cursors dir unreadable: {e}"
    if [(e.name, e.link) for e in installed] != [(e.name, e.link) for e in entries]:
        return False, "entry set differs from source"
    if tree_digest(THEME_ROOT / "cursors", installed) != stored.get("output_digest"):
        return False, "pixel data corrupted since build"
    if not (THEME_ROOT / "index.theme").is_file():
        return False, "index.theme missing"
    return True, f"{len(installed)} entries verified (content digest OK)"


# ---------------------------------------------------------------------------
# Size / theme detection (cursor_size.py owns sizing; gsettings is live)
# ---------------------------------------------------------------------------


def _gsettings_get(key: str) -> str | None:
    if not have("gsettings"):
        return None
    r = run(["gsettings", "get", GSETTINGS_SCHEMA, key])
    if r.returncode != 0:
        return None
    return r.stdout.strip().strip("'\"") or None


def _as_size(val: str | None) -> int | None:
    if val and INT_RE.fullmatch(val) and 0 < int(val) <= MAX_CURSOR_SIZE:
        return int(val)
    return None


def detect_size(fallback: int = 18) -> int:
    if size := _as_size(_gsettings_get("cursor-size")):
        return size
    for var in ("HYPRCURSOR_SIZE", "XCURSOR_SIZE"):
        if size := _as_size(os.environ.get(var, "").strip()):
            return size
    with contextlib.suppress(OSError):
        if size := _as_size(SIZE_STATE_FILE.read_text(encoding="utf-8").strip()):
            return size
    return fallback


def current_state() -> tuple[str | None, int | None]:
    return _gsettings_get("cursor-theme"), _as_size(_gsettings_get("cursor-size"))


# ---------------------------------------------------------------------------
# Apply layers
# ---------------------------------------------------------------------------


def apply_compositor(theme: str, size: int, nudge: bool) -> bool:
    if not have("hyprctl"):
        if in_hyprland():
            log.error("hyprctl not found inside a Hyprland session")
            return False
        log.warning("hyprctl not found; skipping compositor layer")
        return True
    calls: list[list[str]] = []
    if nudge:
        alt = size + 2 if size + 2 <= MAX_CURSOR_SIZE else size - 2
        calls.append(["hyprctl", "setcursor", theme, str(alt)])
    calls.append(["hyprctl", "setcursor", theme, str(size)])
    ok = True
    for cmd in calls:
        r = run(cmd)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0 or out.lower().startswith("couldn't"):
            ok = False
            log.log(logging.ERROR if in_hyprland() else logging.WARNING,
                    "%s failed (rc=%d): %s", " ".join(cmd), r.returncode, out[:200])
        else:
            log.debug("%s -> %s", " ".join(cmd), out[:120])
    if ok:
        log.info("Compositor cursor -> %r @ %dpx%s", theme, size, " (reload nudged)" if nudge else "")
    return ok or not in_hyprland()


def apply_dconf(theme: str, size: int) -> bool:
    r1 = run(["dconf", "write", "/org/gnome/desktop/interface/cursor-size", str(size)])
    r2 = run(["dconf", "write", "/org/gnome/desktop/interface/cursor-theme", f"'{theme}'"])
    if r1.returncode or r2.returncode:
        log.warning("dconf cursor sync failed: %s", (r1.stderr + r2.stderr).strip()[:200])
        return False
    return True


def apply_gsettings(theme: str, size: int) -> bool:
    if not have("gsettings"):
        return apply_dconf(theme, size) if have("dconf") else True
    r1 = run(["gsettings", "set", GSETTINGS_SCHEMA, "cursor-size", str(size)])
    r2 = run(["gsettings", "set", GSETTINGS_SCHEMA, "cursor-theme", theme])
    if r1.returncode == 0 and r2.returncode == 0:
        return True
    if "No schemas" in (r1.stderr + r2.stderr) and have("dconf"):
        return apply_dconf(theme, size)
    log.warning("gsettings cursor sync failed: %s", (r1.stderr + r2.stderr).strip()[:200])
    return False


def apply_dbus_env(theme: str, size: int) -> bool:
    if not have("dbus-update-activation-environment"):
        return True
    r = run(["dbus-update-activation-environment", "--systemd",
             f"XCURSOR_SIZE={size}", f"HYPRCURSOR_SIZE={size}",
             f"XCURSOR_THEME={theme}", f"HYPRCURSOR_THEME={theme}"])
    if r.returncode:
        log.debug("dbus env update rc=%d: %s", r.returncode, r.stderr.strip()[:200])
    return r.returncode == 0


def _split_code_comment(line: str) -> tuple[str, str]:
    """Split a Lua line at the first '--' that is outside a quoted string."""
    quote: str | None = None
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if quote is not None:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "-" and line.startswith("--", i):
            return line[:i], line[i:]
        i += 1
    return line, ""


_LUA_ENV_RE = {
    key: re.compile(
        r"""(?:hl\.env|hl_env)\s*\(\s*(["'])""" + key
        + r"""\1\s*,\s*(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|[^()\n]*?)\s*\)""")
    for key in ENV_KEYS
}


def persist_lua_env(theme: str, size: int) -> bool:
    """Upsert the four cursor hl.env() calls in the user Lua override."""
    try:
        original = USER_ENV_LUA.read_text(encoding="utf-8") if USER_ENV_LUA.is_file() else ""
    except OSError as e:
        log.error("Cannot read %s: %s", USER_ENV_LUA, e)
        return False
    values = {"XCURSOR_SIZE": str(size), "HYPRCURSOR_SIZE": str(size),
              "XCURSOR_THEME": theme, "HYPRCURSOR_THEME": theme}
    seen: set[str] = set()
    out_lines: list[str] = []
    for line in original.splitlines():
        code, comment = _split_code_comment(line)
        for key in ENV_KEYS:
            code, n = _LUA_ENV_RE[key].subn(f'hl.env("{key}", "{values[key]}")', code)
            if n:
                seen.add(key)
        out_lines.append(code + comment)
    text = "\n".join(out_lines)
    if text and not text.endswith("\n"):
        text += "\n"
    if not original.strip():
        text = "-- USER CONFIGURATION: environment_variables.lua (cursor vars managed by dusky_cursor.py)\n"
    missing = [k for k in ENV_KEYS if k not in seen]
    text += "".join(f'hl.env("{k}", "{values[k]}")\n' for k in missing)
    if text == original:
        return True
    try:
        atomic_write(USER_ENV_LUA, text)
    except OSError as e:
        log.error("Atomic write to %s failed: %s", USER_ENV_LUA, e)
        return False
    log.info("%s cursor vars in %s", "Appended" if missing else "Updated", USER_ENV_LUA)
    return True


def update_default_index(theme: str) -> bool:
    """Point icons/default at *theme*; rewrite only the Inherits= line."""
    try:
        current = DEFAULT_INDEX_THEME.read_text(encoding="utf-8") if DEFAULT_INDEX_THEME.is_file() else ""
    except OSError:
        current = ""
    if not current.strip():
        text = f"[Icon Theme]\nName=Default\nComment=Default Cursor Theme\nInherits={theme}\n"
    else:
        text, n = re.subn(r"(?m)^\s*Inherits\s*=.*$", f"Inherits={theme}", current, count=1)
        if not n:
            text = current.rstrip("\n") + f"\nInherits={theme}\n"
    if text == current:
        return True
    try:
        atomic_write(DEFAULT_INDEX_THEME, text)
        log.info("Default icon theme inherits -> %s", theme)
        return True
    except OSError as e:
        log.warning("Cannot write %s: %s", DEFAULT_INDEX_THEME, e)
        return False


_GTK_KEY_RE = re.compile(r"^\s*(gtk-cursor-theme-(?:name|size))\s*=")


def update_gtk_settings(theme: str, size: int) -> bool:
    ok = True
    values = {"gtk-cursor-theme-name": theme, "gtk-cursor-theme-size": str(size)}
    for name in ("gtk-3.0", "gtk-4.0"):
        path = CONFIG_HOME / name / "settings.ini"
        try:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
        except OSError:
            current = ""
        lines = current.splitlines()
        if not any(line.strip() == "[Settings]" for line in lines):
            lines.insert(0, "[Settings]")
        seen: set[str] = set()
        kept: list[str] = []
        for line in lines:
            if m := _GTK_KEY_RE.match(line):
                key = m.group(1)
                if key in seen:
                    continue  # drop duplicate keys - GTK's key-file parser would error
                seen.add(key)
                line = f"{key}={values[key]}"
            kept.append(line)
        idx = next(i for i, line in enumerate(kept) if line.strip() == "[Settings]")
        for key, val in values.items():
            if key not in seen:
                kept.insert(idx + 1, f"{key}={val}")
        text = "\n".join(kept) + "\n"
        if text == current:
            continue
        try:
            atomic_write(path, text)
        except OSError as e:
            log.warning("Cannot write %s: %s", path, e)
            ok = False
    return ok


def apply_all(theme: str, size: int, nudge: bool) -> bool:
    """Apply every layer; returns False only for failures that matter
    (live compositor inside Hyprland, persistent Lua env)."""
    ok_compositor = apply_compositor(theme, size, nudge)
    apply_gsettings(theme, size)
    apply_dbus_env(theme, size)
    ok_persist = persist_lua_env(theme, size)
    update_default_index(theme)
    update_gtk_settings(theme, size)
    return ok_compositor and ok_persist


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _locate_source(source_name: str) -> Path | None:
    src = find_source_cursors(source_name)
    if src is None:
        log.error("Source theme %r not found in %s. Install it via "
                  "375_cursor_theme_bibata_classic_modern.sh (or AUR: bibata-cursor-theme-bin), "
                  "or pass --source.", source_name, [str(d) for d in icon_search_dirs()])
    return src


def do_apply(args: argparse.Namespace, source_name: str) -> int:
    pal = load_palette()
    size = args.size or detect_size()
    log.info("Dusky Cursor: accent=%s (outline) deep_accent=%s (fill) watch_bg=%s mode=%s size=%dpx (%s)",
             pal.accent, pal.deep_accent, pal.background, pal.mode, size, pal.origin)

    src_cursors = _locate_source(source_name)
    if src_cursors is None:
        notify("Dusky Cursor", f"source theme {source_name} missing", "critical")
        return 1
    try:
        entries = scan_cursors(src_cursors)
        source_digest = tree_digest(src_cursors, entries)
    except (OSError, ValueError) as e:
        log.error("Source theme unusable: %s", e)
        notify("Dusky Cursor", f"source theme broken: {e}", "critical")
        return 1

    want = want_fingerprint(pal, source_name, source_digest)
    # Full verification (fingerprint + entry set + output digest), not just a
    # cache comparison: direct tampering, disk corruption or manual fiddling
    # with the installed tree forces a rebuild instead of a blind skip.
    ok, reason = verify_installed(entries, want)
    stale = args.rebuild or not ok
    if stale and not args.rebuild:
        log.info("Rebuild needed: %s", reason)

    if args.dry_run:
        print(f"would {'rebuild' if stale else 'keep'} {THEME_NAME} "
              f"(accent={pal.accent}, deep_accent={pal.deep_accent}, outline={pal.outline}, watch_bg={pal.background}) "
              f"and apply @ {size}px")
        return 0

    rebuilt = False
    if stale:
        t0 = time.monotonic()
        try:
            n_files, n_links = build_theme(src_cursors, entries, pal, source_name, source_digest)
        except (OSError, ValueError, AttributeError) as e:
            log.error("Build failed: %s", e)
            notify("Dusky Cursor", f"build failed: {e}", "critical")
            return 1
        rebuilt = True
        log.info("Built %s: %d shapes + %d aliases in %.2fs", THEME_NAME, n_files, n_links,
                 time.monotonic() - t0)
    else:
        log.info("Theme up to date; skipping rebuild.")

    cur_theme, cur_size = current_state()
    already = cur_theme == THEME_NAME and cur_size == size
    if not rebuilt and already and not args.force:
        log.info("Cursor already applied; nothing to do.")
        return 0
    # Hyprland early-returns on same name+size: nudge to force a reload of new pixels.
    if not apply_all(THEME_NAME, size, nudge=already):
        notify("Dusky Cursor", f"{THEME_NAME} partially applied (see {HOOK_LOG})", "critical")
        return 1
    return 0


def do_restore(args: argparse.Namespace, source_name: str) -> int:
    size = args.size or detect_size()
    if _locate_source(source_name) is None:
        return 1
    if args.dry_run:
        print(f"would restore {source_name} @ {size}px (Dusky build left in place)")
        return 0
    log.info("Restoring cursor -> %r @ %dpx", source_name, size)
    cur_theme, cur_size = current_state()
    if not apply_all(source_name, size, nudge=(cur_theme == source_name and cur_size == size)):
        return 1
    return 0


def do_check(source_name: str) -> int:
    src_cursors = _locate_source(source_name)
    if src_cursors is None:
        print(f"CHECK FAIL: source {source_name} missing")
        return 1
    try:
        entries = scan_cursors(src_cursors)
        want = want_fingerprint(load_palette(), source_name, tree_digest(src_cursors, entries))
    except (OSError, ValueError) as e:
        print(f"CHECK FAIL: source unusable: {e}")
        return 1
    ok, msg = verify_installed(entries, want)
    print(f"CHECK {'OK' if ok else 'FAIL'}: {msg}")
    return 0 if ok else 1


def do_status(source_name: str) -> int:
    pal = load_palette()
    cur_theme, cur_size = current_state()
    print(f"theme:      {THEME_NAME} (source: {source_name})")
    print(f"palette:    accent={pal.accent} (outline) deep_accent={pal.deep_accent} (fill) "
          f"background={pal.background} mode={pal.mode} ({pal.origin})")
    print(f"gsettings:  theme={cur_theme} size={cur_size}")
    print(f"detected:   size={detect_size()}px  hyprland={'yes' if in_hyprland() else 'no'}")
    src_cursors = find_source_cursors(source_name)
    if src_cursors is None:
        print(f"source:     MISSING ({source_name} not installed)")
    else:
        print(f"source:     {src_cursors}")
        try:
            entries = scan_cursors(src_cursors)
            ok, msg = verify_installed(entries, want_fingerprint(pal, source_name, tree_digest(src_cursors, entries)))
            print(f"theme dir:  {THEME_ROOT} ({'OK' if ok else 'NEEDS BUILD'}: {msg})")
        except (OSError, ValueError) as e:
            print(f"theme dir:  {THEME_ROOT} (source unusable: {e})")
    fp = read_fingerprint()
    print(f"built:      {fp.get('built_at', 'never')} accent={fp.get('accent', '-')} "
          f"deep_accent={fp.get('deep_accent', '-')} outline={fp.get('outline', '-')} "
          f"watch_bg={fp.get('watch_bg', '-')}")
    print(f"log:        {HOOK_LOG}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _size_arg(value: str) -> int:
    if (size := _as_size(value)) is None:
        raise argparse.ArgumentTypeError(f"size must be 1..{MAX_CURSOR_SIZE}, got {value!r}")
    return size


def _theme_arg(value: str) -> str:
    if not THEME_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"invalid theme name {value!r}")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build + apply the Dusky (matugen accent) cursor theme.",
        epilog="With no action flag, --apply is assumed (matugen post_hook entrypoint).")
    g = p.add_mutually_exclusive_group()
    for name, help_ in (("apply", "rebuild if stale, then apply"),
                        ("rebuild", "force rebuild + apply"),
                        ("restore", "revert every layer to the source theme"),
                        ("status", "print state, change nothing"),
                        ("check", "verify installed theme == what --apply would build (exit 1 if not)")):
        g.add_argument(f"--{name}", dest="action", action="store_const", const=name, help=help_)
    p.set_defaults(action="apply")
    p.add_argument("--dry-run", action="store_true", help="print the plan without changing anything")
    p.add_argument("--force", action="store_true", help="re-apply even when already current")
    p.add_argument("--size", type=_size_arg, default=None, help="cursor size (default: auto-detect)")
    p.add_argument("--source", type=_theme_arg, default=None,
                   help=f"source theme name (default: $DUSKY_CURSOR_SOURCE or {DEFAULT_SOURCE_THEME})")
    p.add_argument("--quiet", "-q", action="store_true", help="errors only on stderr (hook.log unaffected)")
    p.add_argument("--verbose", "-v", action="store_true", help="debug output")
    args = p.parse_args(argv)
    args.rebuild = args.action == "rebuild"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.quiet, args.verbose, log_file=args.action not in ("status", "check"))
    source_name = args.source or os.environ.get("DUSKY_CURSOR_SOURCE", "").strip() or DEFAULT_SOURCE_THEME
    if not THEME_NAME_RE.fullmatch(source_name):
        log.error("Invalid source theme name %r", source_name)
        return 2
    try:
        match args.action:
            case "status":
                return do_status(source_name)
            case "check":
                return do_check(source_name)
            case "restore":
                with exclusive_lock(LOCK_FILE):
                    return do_restore(args, source_name)
            case _:
                with exclusive_lock(LOCK_FILE):
                    return do_apply(args, source_name)
    except TimeoutError as e:
        log.error("%s", e)
        return 1
    except OSError as e:
        log.error("%s", e)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
