"""Cached literal colors and format-preserving hue adjustment.

This module is loaded on first color use. Hex needs only the standard library;
Textual's CSS parser and colorsys are explicitly lazy.
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
        component = (12.92 * component if component <= 0.0031308
                     else 1.055 * component ** (1 / 2.4) - 0.055)
        return round(component * 255)

    return (
        gamma(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        gamma(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        gamma(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    )


@lru_cache(maxsize=2048)
def parse_literal_color(value: str, allow_named: bool = False) -> LiteralColor | None:
    """Return a literal color or None; never turn unresolved variables gray.

    Bare hex and names require an explicit color-field context. Ordinary
    settings such as 'fade' and identifiers such as 'deadbeef' remain text.
    """
    value = value.strip()
    if not allow_named and not value[:6].lower().startswith(("#", "0x", "rgb(", "rgba(", "hsl(", "hsla(", "oklch(")):
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
        return LiteralColor(_oklch_rgb(max(0.0, min(1.0, lightness)),
                                       max(0.0, min(1.0, chroma)), hue), "oklch", alpha)
    elif allow_named and len(value) in (3, 4, 6, 8) and not value.strip("0123456789abcdef"):
        digits, fmt = value, "bare_hex"
    else:
        functional = value.startswith(("rgb(", "rgba(", "hsl(", "hsla("))
        if not functional and (not allow_named or not value.isalpha()):
            return None
        fmt = value.partition("(")[0] if functional else "named"
        css_value = value
        if functional:
            if not value.endswith(")"):
                return None
            body = value[value.index("(") + 1:-1]
            if fmt in ("rgb", "rgba") and not body.strip("0123456789abcdef"):
                if len(body) != (8 if fmt == "rgba" else 6):
                    return None
                alpha = body[6:]
                rgb = (int(body[:2], 16), int(body[2:4], 16), int(body[4:6], 16))
                return LiteralColor(rgb, "hypr_" + fmt, alpha)
            parts = [part.strip() for part in body.split(",")]
            if len(parts) != (4 if fmt in ("rgba", "hsla") else 3):
                return None
            components = []
            for index, part in enumerate(parts):
                match = _component_pattern().fullmatch(part)
                if match is None:
                    return None
                number = float(match.group(1))
                percent = match.group(2) is not None
                if index == 3:
                    number = max(0.0, min(1.0, number / 100 if percent else number))
                    alpha = part
                elif fmt.startswith("hsl"):
                    if percent != (index != 0):
                        return None
                    if index == 0:
                        if not math.isfinite(number):
                            return None
                        number %= 360
                    else:
                        number = max(0.0, min(100.0, number))
                else:
                    number = max(0.0, min(255.0, number * 255 / 100 if percent else number))
                components.append(f"{number:.12f}" + ("%" if fmt.startswith("hsl") and index in (1, 2) else ""))
            css_value = f"{fmt}({','.join(components)})"
        try:
            color = Color.parse(css_value).clamped
        except (ColorParseError, ValueError, OverflowError):
            return None
        return LiteralColor(color.rgb, fmt, alpha)

    if len(digits) not in (3, 4, 6, 8) or (alpha + digits).strip("0123456789abcdef"):
        return None
    if len(digits) in (3, 4):
        digits = "".join(digit * 2 for digit in digits)
    if len(digits) == 8:
        digits, alpha = digits[:6], digits[6:]
    rgb = (int(digits[:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    return LiteralColor(rgb, fmt, alpha)


def adjust_color_hue(value: str, degrees: float) -> str | None:
    """Rotate hue from the actual value, preserving syntax and opacity.

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
        parts = [part.strip() for part in original[original.index("(") + 1:-1].split(",")]
        hue = (float(parts[0]) % 360 + degrees) % 360
        parts[0] = f"{hue:.12f}".rstrip("0").rstrip(".")
        return f"{fmt}({', '.join(parts)})"
    if fmt == "oklch":
        match = _oklch_pattern().fullmatch(original.lower())
        hue = (float(match.group(4)) % 360 + degrees) % 360
        hue = f"{hue:.12f}".rstrip("0").rstrip(".")
        return original[:match.start(4)] + hue + original[match.end(4):]

    r, g, b = color.rgb
    hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if saturation == 0:
        return value
    r, g, b = (round(component * 255) for component in
               colorsys.hls_to_rgb((hue + degrees / 360) % 1, lightness, saturation))
    digits = f"{r:02x}{g:02x}{b:02x}"
    if fmt == "0xhex":
        return "0x" + color.alpha + digits
    if fmt.startswith("hypr_"):
        return f"{fmt[5:]}({digits}{color.alpha})"
    if fmt in ("rgb", "rgba"):
        alpha = f", {color.alpha}" if fmt == "rgba" else ""
        return f"{fmt}({r}, {g}, {b}{alpha})"
    return ("" if fmt == "bare_hex" else "#") + digits + color.alpha
