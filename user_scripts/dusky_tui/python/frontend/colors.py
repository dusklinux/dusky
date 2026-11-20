"""Cached literal colors and format-preserving hue adjustment.

This module is loaded on first color use. Hex, RGB, HSL, and OKLCH use only
the Python standard library; Textual's CSS parser is lazily loaded exclusively
for named CSS colors.
"""
import re
lazy import math
lazy import colorsys
from dataclasses import dataclass
from functools import lru_cache
lazy from textual.color import Color, ColorParseError


_NUMBER = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"


@lru_cache(maxsize=1)
def _component_pattern() -> re.Pattern:
    return re.compile(_NUMBER + r"(%)?")


@lru_cache(maxsize=1)
def _hue_component_pattern() -> re.Pattern:
    return re.compile(_NUMBER + r"(deg)?")


@lru_cache(maxsize=1)
def _hsl_hue_replace_pattern() -> re.Pattern:
    return re.compile(rf"(hsla?\(\s*){_NUMBER}(deg)?", re.IGNORECASE)


@lru_cache(maxsize=1)
def _oklch_pattern() -> re.Pattern:
    return re.compile(
        rf"oklch\(\s*{_NUMBER}(%)?\s+{_NUMBER}\s+{_NUMBER}(?:deg)?"
        rf"\s*(?:/\s*{_NUMBER}(%)?\s*)?\)"
    )


@dataclass(frozen=True, slots=True)
class LiteralColor:
    rgb: tuple[int, int, int]
    format: str
    alpha: str = ""
    space_separated: bool = False

    @property
    def hex(self) -> str:
        r, g, b = self.rgb
        return f"#{r:02x}{g:02x}{b:02x}"


def _oklch_rgb(lightness: float, chroma: float, hue: float) -> tuple[int, int, int]:
    """OKLCH to clipped sRGB using the CSS Color 4 conversion matrices."""
    angle = math.radians(hue % 360)
    a, b = chroma * math.cos(angle), chroma * math.sin(angle)
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3

    def gamma(component: float) -> int:
        component = max(0.0, min(1.0, component))
        component = (
            12.92 * component
            if component <= 0.0031308
            else 1.055 * component ** (1 / 2.4) - 0.055
        )
        return round(component * 255)

    return (
        gamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        gamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        gamma(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    )


def _split_functional_parts(
    fmt: str,
    body: str,
) -> tuple[list[str], str | None, bool] | None:
    """Split comma-separated or CSS Color 4 space-separated functional arguments."""
    if "," in body:
        if "/" in body:
            return None
        parts = [part.strip() for part in body.split(",")]
        expected = 4 if fmt in ("rgba", "hsla") else 3
        if len(parts) != expected:
            return None
        if len(parts) == 4:
            return parts[:3], parts[3], False
        return parts, None, False

    if "/" in body:
        left, sep, right = body.partition("/")
        if "/" in right:
            return None
        channels = left.split()
        alpha_tokens = right.split()
        if len(channels) != 3 or len(alpha_tokens) != 1:
            return None
        return channels, alpha_tokens[0], True

    channels = body.split()
    if len(channels) != 3:
        return None
    return channels, None, True


def _parse_alpha_token(token: str) -> str | None:
    if _component_pattern().fullmatch(token) is None:
        return None
    return token


def _parse_rgb_hsl_functional(fmt: str, body: str) -> LiteralColor | None:
    body_stripped = body.strip()
    if not body_stripped:
        return None

    # Hyprland hex functional syntax: rgb(rrggbb) / rgba(rrggbbaa)
    if fmt in ("rgb", "rgba") and not body_stripped.strip("0123456789abcdef"):
        if len(body_stripped) != (8 if fmt == "rgba" else 6):
            return None
        alpha = body_stripped[6:]
        rgb = (
            int(body_stripped[:2], 16),
            int(body_stripped[2:4], 16),
            int(body_stripped[4:6], 16),
        )
        return LiteralColor(rgb, "hypr_" + fmt, alpha)

    split = _split_functional_parts(fmt, body_stripped)
    if split is None:
        return None
    channels, alpha_tok, space_separated = split

    alpha = ""
    if alpha_tok is not None:
        parsed_alpha = _parse_alpha_token(alpha_tok)
        if parsed_alpha is None:
            return None
        alpha = parsed_alpha

    comp_re = _component_pattern()

    if fmt.startswith("hsl"):
        hue_match = _hue_component_pattern().fullmatch(channels[0])
        if hue_match is None:
            return None
        hue = float(hue_match.group(1))
        if not math.isfinite(hue):
            return None

        sat_match = comp_re.fullmatch(channels[1])
        light_match = comp_re.fullmatch(channels[2])
        if sat_match is None or light_match is None:
            return None
        if sat_match.group(2) is None or light_match.group(2) is None:
            return None

        sat = max(0.0, min(100.0, float(sat_match.group(1))))
        light = max(0.0, min(100.0, float(light_match.group(1))))

        hue_norm = (hue % 360.0) / 360.0
        r, g, b = colorsys.hls_to_rgb(hue_norm, light / 100.0, sat / 100.0)
        return LiteralColor(
            (round(r * 255), round(g * 255), round(b * 255)),
            fmt,
            alpha,
            space_separated=space_separated,
        )

    rgb_vals: list[int] = []
    for part in channels:
        match = comp_re.fullmatch(part)
        if match is None:
            return None
        number = float(match.group(1))
        if match.group(2) is not None:
            number = number * 255.0 / 100.0
        rgb_vals.append(round(max(0.0, min(255.0, number))))

    return LiteralColor(
        (rgb_vals[0], rgb_vals[1], rgb_vals[2]),
        fmt,
        alpha,
        space_separated=space_separated,
    )


@lru_cache(maxsize=2048)
def parse_literal_color(value: str, allow_named: bool = False) -> LiteralColor | None:
    """Return a literal color or None; never turn unresolved variables gray.

    Bare hex and names require an explicit color-field context. Ordinary
    settings such as 'fade' and identifiers such as 'deadbeef' remain text.
    """
    value = value.strip()
    if not allow_named and not value[:6].lower().startswith(
        ("#", "0x", "rgb(", "rgba(", "hsl(", "hsla(", "oklch(")
    ):
        return None
    value = value.lower()
    alpha = ""

    if value.startswith("#"):
        digits, fmt = value[1:], "hex"
    elif value.startswith("0x"):
        digits, fmt = value[2:], "0xhex"
        if len(digits) not in (6, 8):
            return None
        if len(digits) == 8:
            alpha, digits = digits[:2], digits[2:]
    elif value.startswith("oklch("):
        match = _oklch_pattern().fullmatch(value)
        if match is None:
            return None
        numbers = [float(match.group(i)) for i in (1, 3, 4)]
        if match.group(5) is not None:
            numbers.append(float(match.group(5)))
        if not all(map(math.isfinite, numbers)):
            return None
        lightness, chroma, hue = numbers[:3]
        if match.group(2):
            lightness /= 100
        if match.group(5) is not None:
            alpha = match.group(5) + ("%" if match.group(6) else "")
        return LiteralColor(
            _oklch_rgb(
                max(0.0, min(1.0, lightness)),
                max(0.0, min(1.0, chroma)),
                hue,
            ),
            "oklch",
            alpha,
            space_separated=True,
        )
    elif allow_named and len(value) in (3, 4, 6, 8) and not value.strip("0123456789abcdef"):
        digits, fmt = value, "bare_hex"
    else:
        functional = value.startswith(("rgb(", "rgba(", "hsl(", "hsla("))
        if functional:
            if not value.endswith(")"):
                return None
            fmt = value.partition("(")[0]
            body = value[value.index("(") + 1 : -1]
            return _parse_rgb_hsl_functional(fmt, body)

        if not allow_named or not value.isalpha():
            return None
        try:
            color = Color.parse(value).clamped
        except (ColorParseError, ValueError, OverflowError):
            return None
        return LiteralColor(color.rgb, "named", "")

    if len(digits) not in (3, 4, 6, 8) or (alpha + digits).strip("0123456789abcdef"):
        return None
    if len(digits) in (3, 4):
        digits = "".join(digit * 2 for digit in digits)
    if len(digits) == 8:
        digits, alpha = digits[:6], digits[6:]
    rgb = (int(digits[:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    return LiteralColor(rgb, fmt, alpha)


def adjust_color_hue(value: str, degrees: float) -> str | None:
    """Rotate hue from the actual value, preserving syntax, units, and opacity.

    HSL and OKLCH retain their original saturation/chroma and lightness.
    RGB/hex use HSL conversion. Achromatic RGB values stay unchanged.
    """
    color = parse_literal_color(value, True)
    if color is None or not math.isfinite(degrees):
        return None
    degrees %= 360
    if degrees == 0:
        return value
    original = value.strip()
    fmt = color.format

    if fmt in ("hsl", "hsla"):
        if "," in original:
            parts = [part.strip() for part in original[original.index("(") + 1 : -1].split(",")]
            had_deg = parts[0].lower().endswith("deg")
            raw_hue = parts[0][:-3] if had_deg else parts[0]
            suffix = parts[0][-3:] if had_deg else ""
            hue = (float(raw_hue) % 360 + degrees) % 360
            parts[0] = f"{hue:.12f}".rstrip("0").rstrip(".") + suffix
            return f"{fmt}({', '.join(parts)})"
        match = _hsl_hue_replace_pattern().match(original)
        if match is None:
            return None
        hue = (float(match.group(2)) % 360 + degrees) % 360
        hue_str = f"{hue:.12f}".rstrip("0").rstrip(".")
        return original[: match.start(2)] + hue_str + original[match.end(2) :]

    if fmt == "oklch":
        match = _oklch_pattern().fullmatch(original.lower())
        if match is None:
            return None
        hue = (float(match.group(4)) % 360 + degrees) % 360
        hue_str = f"{hue:.12f}".rstrip("0").rstrip(".")
        return original[: match.start(4)] + hue_str + original[match.end(4) :]

    r, g, b = color.rgb
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation == 0:
        return value
    r, g, b = (
        round(component * 255)
        for component in colorsys.hls_to_rgb((hue + degrees / 360) % 1, lightness, saturation)
    )
    digits = f"{r:02x}{g:02x}{b:02x}"
    if fmt == "0xhex":
        return "0x" + color.alpha + digits
    if fmt.startswith("hypr_"):
        return f"{fmt[5:]}({digits}{color.alpha})"
    if fmt in ("rgb", "rgba"):
        if color.space_separated:
            alpha = f" / {color.alpha}" if color.alpha else ""
            return f"{fmt}({r} {g} {b}{alpha})"
        alpha = f", {color.alpha}" if color.alpha else ""
        return f"{fmt}({r}, {g}, {b}{alpha})"
    return ("" if fmt == "bare_hex" else "#") + digits + color.alpha
