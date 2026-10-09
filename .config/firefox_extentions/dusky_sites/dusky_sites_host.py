#!/usr/bin/env -S python3 -u
"""
Dusky Sites — native-messaging host v6.2
Python 3.14+ · Linux only (inotify via libc) · single thread · wire v3 (delta)

Hot path per matugen tick: one stat, one 3.6 KB regex parse, one BLAKE2b, one ~4 KB frame.
The 174 KB site-rule map is re-parsed only when the directory signature changes and is sent
only when the extension's `known.websitesRev` differs. Dark-Reader-format fallback configs are
parsed lazily on the first GET_DOMAIN_FIX and cached by stat signature.

Framing (MDN Native messaging): uint32 length in native byte order + UTF-8 JSON; app→extension
frames ≤ 1 MiB — larger payloads are split into CHUNK frames and reassembled by background.js.

  ← HELLO {wire, extension, known:{websitesRev}}     → HELLO_ACK {wire, pid, peerWire}
  ← SET_CONFIG {config}                               → (persist config.json) + MATUGEN_UPDATE
  ← FETCH_NOW {known:{websitesRev}}                   → MATUGEN_UPDATE
  ← GET_DOMAIN_FIX {rid, domain}                      → DOMAIN_FIX_RESPONSE {rid, domain, css, isDarkSite, hints}
  ← LIVE_THEME_RESPONSE {theme}                       → (write live_theme_cache.json)
  ← PING {at} / PONG                                  → PONG {at} / —
  → MATUGEN_UPDATE {data:{colors, colorsRev, websitesRev, websites?, disabledSites,
                          webThemeEnabled, forceUnthemedWebsites, status, ok, timestamp}}
  → PING {at}          every 20 s while keepAlive is on (keeps the MV3 event page warm)
  → QUERY_LIVE_THEME   1.5 s after a settled palette change, or on SIGUSR1
  → CHUNK {id, seq, total, part}

Exit: stdin EOF (Firefox closed the port) or SIGTERM/SIGINT/SIGHUP. Config lives in
~/.config/dusky/settings/dusky_sites/config.json (camelCase keys, atomic writes).
"""

import colorsys
import ctypes
import hashlib
import json
import os
import re
import select
import selectors
import signal
import struct
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

WIRE = 3
MAX_MSG = 1 << 20                 # Gecko app→extension cap
MAX_INBOUND = 64 << 20            # sanity cap (the spec allows 4 GiB)
CHUNK_CHARS = 200_000             # ≤ ~800 KiB UTF-8 per CHUNK frame in the worst case
QUIET_S = 0.120                   # collapse write bursts: fire 120 ms after the latest relevant event …
MAX_WAIT_S = 0.300                # … with an immovable 300 ms deadline from the first event
SETTLE_S = 1.5                    # QUERY_LIVE_THEME after a settled change
KEEPALIVE_S = 20.0                # PING cadence (event-page idle timeout is 30 s)
POLL_S = 60.0                     # stat-poll safety net (inotify cannot see every filesystem)
WATCH_RETRY_S = 5.0

HOME = Path.home()
SETTINGS_DIR = HOME / ".config" / "dusky" / "settings" / "dusky_sites"
CONFIG_PATH = SETTINGS_DIR / "config.json"
LIVE_THEME_PATH = SETTINGS_DIR / "live_theme_cache.json"

DEBUG = False


def log(*parts: object) -> None:
    if DEBUG:
        print("[dusky_sites]", *parts, file=sys.stderr, flush=True)


def err(*parts: object) -> None:
    print("[dusky_sites]", *parts, file=sys.stderr, flush=True)


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stat_sig(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── Configuration ─────────────────────────────────────────────────────────────
CAMEL = {
    "colorsPath": "colors_path",
    "websitesDir": "websites_dir",
    "webThemeEnabled": "web_theme_enabled",
    "forceUnthemedWebsites": "force_unthemed_websites",
    "disabledSites": "disabled_sites",
    "keepAlive": "keep_alive",
    "debug": "debug",
}


@dataclass(slots=True)
class Config:
    colors_path: str = "~/.config/matugen/generated/dusky_sites.css"
    websites_dir: str = "~/.config/dusky_sites"
    web_theme_enabled: bool = False
    force_unthemed_websites: bool = False
    disabled_sites: list[str] = field(default_factory=list)
    keep_alive: bool = True
    debug: bool = False

    def apply(self, updates: dict) -> bool:
        """Typed, allow-listed merge. Returns True when something changed."""
        changed = False
        for camel, attr in CAMEL.items():
            if camel not in updates:
                continue
            val = updates[camel]
            match attr:
                case "colors_path" | "websites_dir":
                    if not isinstance(val, str) or not val.strip() or "\0" in val or len(val) > 4096:
                        continue
                    val = val.strip()
                case "disabled_sites":
                    if not isinstance(val, list):
                        continue
                    val = sorted({s.strip().lower() for s in val if isinstance(s, str) and s.strip()})[:512]
                case _:
                    if not isinstance(val, bool):
                        continue
            if getattr(self, attr) != val:
                setattr(self, attr, val)
                changed = True
        return changed

    def to_json(self) -> dict:
        return {camel: getattr(self, attr) for camel, attr in CAMEL.items()}

    @property
    def colors_file(self) -> Path:
        return Path(self.colors_path).expanduser()

    @property
    def sites_dir(self) -> Path:
        return Path(self.websites_dir).expanduser()


def load_config() -> Config:
    cfg = Config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except OSError, ValueError:                       # PEP 758
        err(f"config unreadable: {CONFIG_PATH}")
        return cfg
    if isinstance(raw, dict):
        cfg.apply(raw)
    return cfg


def save_config(cfg: Config) -> None:
    try:
        atomic_write(CONFIG_PATH, json.dumps(cfg.to_json(), indent=4) + "\n")
    except OSError as e:
        err(f"config write failed: {e}")


# ── Palette + site rules (stat-signature cached) ─────────────────────────────
_COLOR_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+?)\s*(?:!important)?\s*;", re.IGNORECASE)
_MOZ_DOC_RE = re.compile(r"@-moz-document\s+(?P<specs>[^{]+)\{", re.IGNORECASE)
_DOMAIN_SPEC_RE = re.compile(r"""domain\(\s*["']?([^"')\s]+)["']?\s*\)""", re.IGNORECASE)
_URL_SPEC_RE = re.compile(r"""(?:url|url-prefix)\(\s*["']?(?:https?://)?([^/"')\s]+)""", re.IGNORECASE)


def parse_colors(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return {}
    # CSS uses private names so unrelated userChrome palettes cannot override it.
    # Keep the existing native-message palette keys consumed by the signed XPI.
    colors = {name.replace("--dusky-palette-", "--", 1): value.strip()
              for name, value in _COLOR_RE.findall(text)}
    # Keep component-valued site tokens valid, and refresh them with Matugen.
    for name, value in tuple(colors.items()):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            continue
        r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
        h, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        colors.setdefault(f"{name}_rgb", f"{r} {g} {b}")
        colors.setdefault(f"{name}_rgb_comma", f"{r}, {g}, {b}")
        colors.setdefault(f"{name}_hsl", f"{h * 360:.6f} {saturation * 100:.6f}% {lightness * 100:.6f}%")
    return colors


def balanced_block(text: str, start: int) -> str:
    """text[start] == '{'. Returns the inner body; string- and comment-aware."""
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i].strip()
        i += 1
    return text[start + 1:].strip()


def parse_site_file(path: Path, out: dict[str, str]) -> None:
    """@-moz-document domain(...) blocks → per-domain bodies; files without one map to their stem.
    Several files targeting the same domain are concatenated (v5 silently kept the last one)."""
    text = path.read_text(encoding="utf-8")
    stem = path.stem.lower()
    blocks = list(_MOZ_DOC_RE.finditer(text))
    if not blocks:
        out[stem] = text.strip()
        return
    for m in blocks:
        specs = m.group("specs")
        domains = [d.lower() for d in _DOMAIN_SPEC_RE.findall(specs)] or [d.lower() for d in _URL_SPEC_RE.findall(specs)]
        body = balanced_block(text, m.end() - 1)
        if not domains:
            out[stem] = body or text.strip()
        for d in domains:
            out[d] = f"{out[d]}\n{body}" if d in out else body


@dataclass(slots=True)
class Palette:
    path: Path
    sig: tuple[int, int, int] | None = None
    colors: dict[str, str] = field(default_factory=dict)
    rev: str = digest("{}")

    def refresh(self) -> bool:
        sig = stat_sig(self.path)
        if sig == self.sig:
            return False
        colors = parse_colors(self.path) if sig else {}
        if sig and not colors and self.colors:
            return False              # truncated mid-write: keep the last good palette; CLOSE_WRITE re-arms us
        self.sig = sig
        rev = digest(canon(colors))
        changed = rev != self.rev
        self.colors, self.rev = colors, rev
        return changed


@dataclass(slots=True)
class SiteRules:
    dir: Path
    sig: tuple = ()
    sites: dict[str, str] = field(default_factory=dict)
    rev: str = digest("{}")

    def signature(self) -> tuple:
        entries: list[tuple[str, int, int]] = []
        try:
            with os.scandir(self.dir) as it:
                for e in it:
                    if e.name.endswith(".css") and e.is_file():
                        st = e.stat()
                        entries.append((e.name, st.st_mtime_ns, st.st_size))
        except OSError:
            return ()
        entries.sort()
        return tuple(entries)

    def refresh(self) -> bool:
        sig = self.signature()
        if sig == self.sig:
            return False
        self.sig = sig
        t0 = time.perf_counter()
        sites: dict[str, str] = {}
        for name, _, _ in sig:
            try:
                parse_site_file(self.dir / name, sites)
            except OSError, UnicodeDecodeError:
                err(f"site file skipped: {name}")
        rev = digest(canon(sites))
        changed = rev != self.rev
        self.sites, self.rev = sites, rev
        log(f"site rules: {len(sig)} files → {len(sites)} keys in {(time.perf_counter() - t0) * 1000:.1f} ms")
        return changed


# ── Fallback (Dark-Reader-format configs; forceUnthemedWebsites only; lazy) ──
_SEP_RE = re.compile(r"^\s*={5,}\s*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^[A-Z][A-Z ]{1,30}$")
_HEX_RE = re.compile(r"^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)
_SLOT_RE = re.compile(r"\$\{([^}]*)\}")
_DR_VAR_RE = re.compile(r"var\(\s*--darkreader-([a-z-]+)\s*\)")
DR_VARS = {
    "neutral-background": "var(--surface)",
    "neutral-text": "var(--on_surface)",
    "selection-background": "var(--primary_container)",
    "selection-text": "var(--on_primary_container)",
}
INVERT_DECL = "filter: invert(1) hue-rotate(180deg) !important;"
DYNAMIC_DECLS = {"INVERT": INVERT_DECL}
STATIC_DECLS = {
    "NEUTRAL BG": "background-color: var(--surface) !important;",
    "NEUTRAL BG ACTIVE": "background-color: var(--surface_container_high) !important;",
    "NEUTRAL TEXT": "color: var(--on_surface) !important;",
    "NEUTRAL TEXT ACTIVE": "color: var(--primary) !important;",
    "NEUTRAL BORDER": "border-color: var(--outline_variant) !important;",
    "RED BG": "background-color: var(--error_container) !important;",
    "RED BG ACTIVE": "background-color: var(--error) !important;",
    "RED TEXT": "color: var(--error) !important;",
    "RED TEXT ACTIVE": "color: var(--on_error_container) !important;",
    "RED BORDER": "border-color: var(--error) !important;",
    "GREEN BG": "background-color: var(--tertiary_container) !important;",
    "GREEN BG ACTIVE": "background-color: var(--tertiary) !important;",
    "GREEN TEXT": "color: var(--tertiary) !important;",
    "GREEN TEXT ACTIVE": "color: var(--on_tertiary_container) !important;",
    "GREEN BORDER": "border-color: var(--tertiary) !important;",
    "BLUE BG": "background-color: var(--primary_container) !important;",
    "BLUE BG ACTIVE": "background-color: var(--primary) !important;",
    "BLUE TEXT": "color: var(--primary) !important;",
    "BLUE TEXT ACTIVE": "color: var(--on_primary_container) !important;",
    "BLUE BORDER": "border-color: var(--primary) !important;",
    "FADE BG": "background-color: var(--surface_container_low) !important;",
    "FADE TEXT": "color: var(--on_surface_variant) !important;",
    "TRANSPARENT BG": "background-color: transparent !important;",
    "NO IMAGE": "background-image: none !important;",
    "INVERT": INVERT_DECL,
}

type Block = tuple[list[str], dict[str, list[str]]]


def parse_blocks(text: str) -> list[Block]:
    """Blocks separated by ===== lines: domain lines first, then UPPERCASE section headers."""
    blocks: list[Block] = []
    for chunk in _SEP_RE.split(text):
        domains: list[str] = []
        sections: dict[str, list[str]] = {}
        cur: str | None = None
        for line in chunk.splitlines():
            s = line.strip()
            if not s:
                continue
            if _SECTION_RE.match(s):
                cur = s
                sections.setdefault(cur, [])
            elif cur is None:
                domains.append(s.split("/", 1)[0].lower())
            else:
                sections[cur].append(line.rstrip())
        if domains:
            blocks.append((domains, sections))
    return blocks


def domain_score(host: str, pattern: str) -> int:
    """0 = no match; larger = more specific. '*' is the common block and never scores here."""
    p = pattern.strip().lower().rstrip(".")
    if not p or p == "*":
        return 0
    if p.startswith("*."):
        p = p[2:]
    if p.endswith(".*"):
        base = p[:-2]
        return len(base) + 2 if base in host.split(".")[:-1] else 0
    if host == p:
        return len(p) + 100
    if host.endswith("." + p):
        return len(p) + 50
    return 0


def tone(token: str) -> str | None:
    t = token.strip().lower()
    if _HEX_RE.match(t):
        h = t[1:]
        if len(h) in (3, 4):
            r, g, b = (int(c * 2, 16) for c in h[:3])
        else:
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return "light" if (0.2126 * r + 0.7152 * g + 0.0722 * b) > 128 else "dark"
    if t in {"white", "whitesmoke", "snow", "ivory"}:
        return "light"
    if t == "black":
        return "dark"
    return None


def substitute_css(css: str) -> str:
    """${colour} slots → palette roles by tone; --darkreader-* variables → palette roles."""
    def slot(m: re.Match[str]) -> str:
        match tone(m.group(1)):
            case "light":
                return "var(--surface)"
            case "dark":
                return "var(--on_surface)"
            case _:
                return m.group(1)
    css = _SLOT_RE.sub(slot, css)
    return _DR_VAR_RE.sub(lambda m: DR_VARS.get(m.group(1), "inherit"), css)


def block_css(sections: dict[str, list[str]], decls: dict[str, str]) -> str:
    parts: list[str] = []
    for name, lines in sections.items():
        if name == "CSS":
            parts.append(substitute_css("\n".join(lines)))
        elif name in decls:
            sel = ", ".join(l.strip() for l in lines if l.strip())
            if sel:
                parts.append(f"{sel} {{ {decls[name]} }}")
    return "\n".join(p for p in parts if p)


class Fallback:
    """Lookup order: sites/fallback, sites, settings/fallback, settings. Parsed lazily, cached by signature.
    inversion-fixes.config is intentionally not used: its INVERT lists only make sense under a
    page-level invert filter, which this engine never applies."""

    FILES = {
        "dark": "dark-sites.config",
        "hints": "detector-hints.config",
        "dynamic": "dynamic-theme-fixes.config",
        "static": "static-themes.config",
    }

    def __init__(self, sites_dir: Callable[[], Path]) -> None:
        self._sites_dir = sites_dir
        self._cache: dict[str, tuple[tuple | None, object]] = {}

    def locate(self, name: str) -> Path | None:
        d = self._sites_dir()
        for p in (d / "fallback" / name, d / name, SETTINGS_DIR / "fallback" / name, SETTINGS_DIR / name):
            if p.is_file():
                return p
        return None

    def load(self, key: str, parser: Callable[[str], object]) -> object:
        p = self.locate(self.FILES[key])
        sig = None
        if p is not None:
            s = stat_sig(p)
            if s is not None:
                sig = (str(p), *s)
        hit = self._cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        text = ""
        if sig is not None:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        t0 = time.perf_counter()
        data = parser(text)
        log(f"parsed {self.FILES[key]} ({len(text)} B) in {(time.perf_counter() - t0) * 1000:.1f} ms")
        self._cache[key] = (sig, data)
        return data

    def domain_fix(self, host: str) -> dict:
        dark = self.load("dark", lambda t: [l.strip().lower() for l in t.splitlines() if l.strip() and not l.startswith("#")])
        is_dark = any(domain_score(host, d) for d in dark)
        hints: list[str] = []
        for domains, sections in self.load("hints", parse_blocks):
            if any(domain_score(host, d) for d in domains):
                hints.extend(l.strip() for l in sections.get("MATCH", []) if l.strip())
        css: list[str] = []
        for key, decls in (("dynamic", DYNAMIC_DECLS), ("static", STATIC_DECLS)):
            common = None
            best, best_score = None, 0
            for domains, sections in self.load(key, parse_blocks):
                if "*" in domains:
                    common = sections
                    continue
                score = max((domain_score(host, d) for d in domains), default=0)
                if score > best_score:
                    best, best_score = sections, score
            if key == "static" and best is None:
                continue                                   # static themes are opt-in per site
            if common is not None and (best is None or "NO COMMON" not in best):
                css.append(block_css(common, decls))
            if best is not None:
                css.append(block_css(best, decls))
        return {"css": "\n".join(p for p in css if p), "isDarkSite": is_dark, "hints": hints[:16]}


# ── Wire (NMH framing over non-blocking stdin) ───────────────────────────────
class Wire:
    def __init__(self) -> None:
        self._in = bytearray()
        self._buf = bytearray(1 << 16)
        self._view = memoryview(self._buf)
        self._chunk_seq = 0

    def read(self) -> list[dict] | None:
        """Drain stdin. Returns decoded frames, or None on EOF (Firefox closed the port)."""
        while True:
            try:
                n = os.readinto(0, self._view)
            except BlockingIOError:
                break
            if n == 0:
                return None
            self._in += self._view[:n]
            if n < len(self._buf):
                break
        frames: list[dict] = []
        while len(self._in) >= 4:
            length = int.from_bytes(self._in[:4], sys.byteorder)
            if length > MAX_INBOUND:
                raise ValueError(f"inbound frame of {length} B exceeds cap")
            if len(self._in) < 4 + length:
                break
            body = bytes(self._in[4:4 + length])
            del self._in[:4 + length]
            try:
                msg = json.loads(body)
            except ValueError as e:
                err(f"bad frame: {e}")
                continue
            if isinstance(msg, dict):
                frames.append(msg)
        return frames

    def send(self, msg: dict) -> bool:
        text = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        data = text.encode("utf-8")
        if len(data) <= MAX_MSG - 64:
            return self._write(data)
        if len(data) > 8 * MAX_MSG:
            err(f"frame of {len(data)} B exceeds the 8 MiB chunk budget; dropped ({msg.get('type')})")
            return False
        self._chunk_seq += 1
        cid = f"{os.getpid()}-{self._chunk_seq}"
        parts = [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)]
        for seq, part in enumerate(parts):
            frame = {"type": "CHUNK", "id": cid, "seq": seq, "total": len(parts), "part": part}
            if not self._write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")):
                return False
        return True

    @staticmethod
    def _write(data: bytes) -> bool:
        view = memoryview(len(data).to_bytes(4, sys.byteorder) + data)
        try:
            while view:
                try:
                    view = view[os.write(1, view):]
                except BlockingIOError:
                    # stdout shares a non-blocking description with stdin when the browser hands us a
                    # socketpair instead of two pipes: wait for writability instead of dropping the frame.
                    select.select([], [1], [], 5.0)
            return True
        except BrokenPipeError:
            raise SystemExit(0) from None
        except OSError as e:
            err(f"stdout write failed: {e}")
            return False


# ── inotify (man 7 inotify) ──────────────────────────────────────────────────
IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF = 0x400, 0x800
IN_Q_OVERFLOW, IN_IGNORED = 0x4000, 0x8000
IN_ONLYDIR, IN_EXCL_UNLINK = 0x0100_0000, 0x0400_0000
WATCH_MASK = (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
              | IN_DELETE_SELF | IN_MOVE_SELF | IN_ONLYDIR | IN_EXCL_UNLINK)
_EVENT = struct.Struct("iIII")      # struct inotify_event { int wd; uint32_t mask, cookie, len; char name[]; }


class Inotify:
    def __init__(self) -> None:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.inotify_init1.argtypes = (ctypes.c_int,)
        libc.inotify_init1.restype = ctypes.c_int
        libc.inotify_add_watch.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
        libc.inotify_add_watch.restype = ctypes.c_int
        libc.inotify_rm_watch.argtypes = (ctypes.c_int, ctypes.c_int)
        libc.inotify_rm_watch.restype = ctypes.c_int
        self._libc = libc
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)   # IN_NONBLOCK / IN_CLOEXEC share these values
        if self.fd < 0:
            e = ctypes.get_errno()
            raise OSError(e, f"inotify_init1: {os.strerror(e)}")
        self._by_wd: dict[int, Path] = {}
        self._by_dir: dict[Path, int] = {}

    def sync(self, wanted: set[Path]) -> bool:
        """Watch exactly `wanted`; False when a directory does not exist yet (caller retries)."""
        for d in [d for d in self._by_dir if d not in wanted]:
            self._libc.inotify_rm_watch(self.fd, self._by_dir[d])
            self._by_wd.pop(self._by_dir.pop(d), None)
        ok = True
        for d in wanted:
            if d in self._by_dir:
                continue
            wd = self._libc.inotify_add_watch(self.fd, os.fsencode(d), WATCH_MASK)
            if wd < 0:
                ok = False
                continue
            self._by_wd[wd] = d
            self._by_dir[d] = wd
        return ok

    def drain(self) -> list[tuple[Path | None, int, str]]:
        out: list[tuple[Path | None, int, str]] = []
        while True:
            try:
                buf = os.read(self.fd, 1 << 16)
            except BlockingIOError:
                return out
            off = 0
            while off + _EVENT.size <= len(buf):
                wd, mask, _cookie, ln = _EVENT.unpack_from(buf, off)
                off += _EVENT.size
                name = buf[off:off + ln].split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
                off += ln
                if mask & IN_Q_OVERFLOW:
                    out.append((None, mask, ""))
                    continue
                d = self._by_wd.get(wd)
                if d is None:
                    continue
                if mask & IN_IGNORED:
                    self._by_wd.pop(wd, None)
                    self._by_dir.pop(d, None)
                out.append((d, mask, name))


# ── Host ─────────────────────────────────────────────────────────────────────
class Host:
    def __init__(self) -> None:
        global DEBUG
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)   # config watch must never have to retry
        self.cfg = load_config()
        DEBUG = self.cfg.debug
        self.cfg_sig = stat_sig(CONFIG_PATH)
        self.palette = Palette(self.cfg.colors_file)
        self.sites = SiteRules(self.cfg.sites_dir)
        self.fallback = Fallback(lambda: self.cfg.sites_dir)
        self.wire = Wire()
        self.ino = Inotify()

        self.sel = selectors.DefaultSelector()
        self.sel.register(0, selectors.EVENT_READ, "stdin")
        self.sel.register(self.ino.fd, selectors.EVENT_READ, "inotify")
        r, w = os.pipe()
        os.set_blocking(r, False)
        os.set_blocking(w, False)
        self.sel.register(r, selectors.EVENT_READ, "signal")
        self._sig_r = r
        signal.set_wakeup_fd(w, warn_on_full_buffer=False)
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(s, self._on_stop)
        signal.signal(signal.SIGUSR1, self._on_usr1)

        self.stop = False
        self.query_live = False
        self.sent_sites_rev: str | None = None
        self.dirty = {"palette": False, "sites": False, "config": False}
        self.force = False
        now = time.monotonic()
        self.t_update: float | None = None
        self.t_hard: float | None = None
        self.t_settle: float | None = None
        self.t_keepalive = now + KEEPALIVE_S
        self.t_poll = now + POLL_S
        self.t_watch = 0.0
        self.watch_ok = False

        self.sync_watches()
        self.palette.refresh()          # pre-warm: the first FETCH_NOW answers from memory
        self.sites.refresh()

    def _on_stop(self, *_: object) -> None:
        self.stop = True

    def _on_usr1(self, *_: object) -> None:
        self.query_live = True

    # transport ------------------------------------------------------------
    def send(self, msg: dict) -> bool:
        ok = self.wire.send(msg)
        if ok:
            self.t_keepalive = time.monotonic() + KEEPALIVE_S
        return ok

    # watches --------------------------------------------------------------
    def sync_watches(self) -> None:
        self.watch_ok = self.ino.sync({self.palette.path.parent, self.sites.dir, CONFIG_PATH.parent})
        if not self.watch_ok:
            self.t_watch = time.monotonic() + WATCH_RETRY_S

    def schedule_update(self) -> None:
        t = time.monotonic()
        if self.t_hard is None:
            self.t_hard = t + MAX_WAIT_S
        self.t_update = min(t + QUIET_S, self.t_hard)

    def on_inotify(self) -> None:
        for d, mask, name in self.ino.drain():
            if mask & IN_Q_OVERFLOW:
                self.dirty.update(palette=True, sites=True, config=True)
                self.schedule_update()
                continue
            if mask & (IN_IGNORED | IN_DELETE_SELF | IN_MOVE_SELF):
                self.watch_ok = False
                self.t_watch = time.monotonic() + 1.0
            if d == self.palette.path.parent and name == self.palette.path.name:
                self.dirty["palette"] = True
                self.schedule_update()
            if d == self.sites.dir and (not name or name.endswith(".css")):
                self.dirty["sites"] = True
                self.schedule_update()
            if d == CONFIG_PATH.parent and name == CONFIG_PATH.name:
                self.dirty["config"] = True
                self.schedule_update()

    # timers ---------------------------------------------------------------
    def next_timeout(self) -> float:
        deadlines = [self.t_poll]
        if self.t_update is not None:
            deadlines.append(self.t_update)
        if self.t_settle is not None:
            deadlines.append(self.t_settle)
        if self.cfg.keep_alive:
            deadlines.append(self.t_keepalive)
        if not self.watch_ok:
            deadlines.append(self.t_watch)
        return max(0.0, min(deadlines) - time.monotonic())

    def run_timers(self) -> None:
        t = time.monotonic()
        if self.t_update is not None and t >= self.t_update:
            self.t_update = self.t_hard = None
            self.tick()
        if self.t_settle is not None and t >= self.t_settle:
            self.t_settle = None
            self.send({"type": "QUERY_LIVE_THEME"})
        if self.query_live:
            self.query_live = False
            self.send({"type": "QUERY_LIVE_THEME"})
        if self.cfg.keep_alive and t >= self.t_keepalive:
            self.send({"type": "PING", "at": int(time.time() * 1000)})
            self.t_keepalive = t + KEEPALIVE_S           # even if the write failed, do not spin
        if t >= self.t_poll:
            self.t_poll = t + POLL_S
            self.dirty.update(palette=True, sites=True, config=True)
            self.tick()
        if not self.watch_ok and t >= self.t_watch:
            self.sync_watches()

    # state ----------------------------------------------------------------
    def tick(self) -> None:
        t0 = time.perf_counter()
        changed = self.force
        self.force = False
        if self.dirty["config"]:
            self.dirty["config"] = False
            changed |= self.reload_config()
        if self.dirty["palette"]:
            self.dirty["palette"] = False
            changed |= self.palette.refresh()
        if self.dirty["sites"]:
            self.dirty["sites"] = False
            changed |= self.sites.refresh()
        if changed:
            self.send_update()
        log(f"tick {'sent' if changed else 'no-op'} in {(time.perf_counter() - t0) * 1000:.2f} ms")

    def reload_config(self) -> bool:
        global DEBUG
        sig = stat_sig(CONFIG_PATH)
        if sig == self.cfg_sig:
            return False
        self.cfg_sig = sig
        fresh = load_config()
        changed = fresh.to_json() != self.cfg.to_json()
        self.cfg = fresh
        DEBUG = fresh.debug
        self.retarget()
        return changed

    def retarget(self) -> None:
        if self.palette.path != self.cfg.colors_file:
            self.palette = Palette(self.cfg.colors_file)
            self.dirty["palette"] = True
        if self.sites.dir != self.cfg.sites_dir:
            self.sites = SiteRules(self.cfg.sites_dir)
            self.dirty["sites"] = True
        self.sync_watches()

    def send_update(self) -> None:
        cfg = self.cfg
        status: list[str] = []
        if self.palette.sig is None:
            status.append(f"Colors file not found: {cfg.colors_path}")
        elif not self.palette.colors:
            status.append(f"Colors empty or unreadable: {cfg.colors_path}")
        if not self.sites.dir.is_dir():
            status.append(f"Websites dir not found: {cfg.websites_dir}")
        include_sites = self.sites.rev != self.sent_sites_rev
        data: dict[str, object] = {
            "colors": self.palette.colors,
            "colorsRev": self.palette.rev,
            "websitesRev": self.sites.rev,
            "disabledSites": cfg.disabled_sites,
            "webThemeEnabled": cfg.web_theme_enabled,
            "forceUnthemedWebsites": cfg.force_unthemed_websites,
            "status": status or ["OK"],
            "ok": bool(self.palette.colors),
            "timestamp": int(time.time() * 1000),
        }
        if include_sites:
            data["websites"] = self.sites.sites
        if self.send({"type": "MATUGEN_UPDATE", "data": data}):
            if include_sites:
                self.sent_sites_rev = self.sites.rev
            self.t_settle = time.monotonic() + SETTLE_S
            log(f"MATUGEN_UPDATE colours={len(self.palette.colors)} rev={self.palette.rev} sites={'sent' if include_sites else 'known'}")
        else:
            self.sent_sites_rev = None
            self.force = True
            self.t_update = time.monotonic() + 1.0
            self.t_hard = None

    def note_known(self, known: object) -> None:
        rev = known.get("websitesRev") if isinstance(known, dict) else None
        self.sent_sites_rev = rev if isinstance(rev, str) else None

    def write_live_theme(self, theme: object) -> None:
        try:
            atomic_write(LIVE_THEME_PATH, json.dumps({"theme": theme, "timestamp": int(time.time() * 1000)}, indent=2) + "\n")
        except OSError as e:
            err(f"live theme cache write failed: {e}")

    # protocol -------------------------------------------------------------
    def handle(self, m: dict) -> None:
        global DEBUG
        match m.get("type"):
            case "HELLO":
                self.note_known(m.get("known"))
                self.send({"type": "HELLO_ACK", "wire": WIRE, "pid": os.getpid(), "peerWire": m.get("wire")})
            case "FETCH_NOW":
                self.note_known(m.get("known"))
                self.force = True
                self.dirty.update(palette=True, sites=True, config=True)
                self.t_update = time.monotonic()
                self.t_hard = None
            case "SET_CONFIG":
                cfg = m.get("config")
                if isinstance(cfg, dict) and self.cfg.apply(cfg):
                    save_config(self.cfg)
                    self.cfg_sig = stat_sig(CONFIG_PATH)
                    DEBUG = self.cfg.debug
                    self.retarget()
                    self.force = True
                    self.t_update = time.monotonic()
                    self.t_hard = None
            case "GET_DOMAIN_FIX":
                domain = str(m.get("domain") or "").strip().lower()[:253]
                fix = self.fallback.domain_fix(domain) if domain else {"css": "", "isDarkSite": False, "hints": []}
                self.send({"type": "DOMAIN_FIX_RESPONSE", "rid": m.get("rid"), "domain": domain, **fix})
            case "LIVE_THEME_RESPONSE":
                self.write_live_theme(m.get("theme"))
            case "PING":
                self.send({"type": "PONG", "at": m.get("at")})
            case "PONG":
                pass
            case other:
                self.send({"type": "HOST_RESPONSE", "ok": False, "error": "unsupported", "of": other, "rid": m.get("rid")})

    def run(self) -> int:
        while not self.stop:
            for key, _ in self.sel.select(self.next_timeout()):
                match key.data:
                    case "stdin":
                        frames = self.wire.read()
                        if frames is None:
                            log("stdin closed by Firefox; exiting")
                            return 0
                        for frame in frames:
                            self.handle(frame)
                    case "inotify":
                        self.on_inotify()
                    case "signal":
                        try:
                            os.read(self._sig_r, 4096)
                        except BlockingIOError:
                            pass
            self.run_timers()
        return 0


def main() -> int:
    os.set_blocking(0, False)
    try:
        return Host().run()
    except KeyboardInterrupt:
        return 0
    except Exception as e:                        # last-resort diagnostics land in the Browser Console
        err(f"fatal: {e!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
