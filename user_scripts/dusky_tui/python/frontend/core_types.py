#!/usr/bin/env python3
import math
import re
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Literal
from abc import ABC, abstractmethod

# =============================================================================
# CORE UTILITIES & CONSTANTS
# =============================================================================
# Fast syntax checks avoid loading color parsing for hex and explicit variables.
_RE_THEME_VAR = re.compile(r"(\$|@|var\(|\{\{)")
_RE_HEX = re.compile(r"^#?(?:[a-f0-9]{3}|[a-f0-9]{4}|[a-f0-9]{6}|[a-f0-9]{8})$")
_RE_HEX_LOWER = re.compile(r"^0x(?:[a-f0-9]{6}|[a-f0-9]{8})$")
_RE_CSS_FUNC = re.compile(r"^(?:rgba?|hsla?|oklch)\s*\(")
_RE_VAR_IDENT = re.compile(r"^(?:--[a-z0-9_-]+|[a-z0-9_][a-z0-9_-]*(?:\.[a-z0-9_-]+)*)$")
_RE_INT = re.compile(r"^[+-]?\d+$")
_RE_INT_BASE = re.compile(r"^[+-]?0(?:[xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$")
_RE_FLOAT = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_RE_DEG = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)deg$", re.IGNORECASE)
_RE_GRADIENT_SPLIT = re.compile(r"\s+(?![^()]*\))")

_BOOL_TRUE = frozenset({"true", "1", "yes", "on", "t", "y"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off", "f", "n"})
_SCALAR_TYPES = frozenset((bool, int, float, str, bytes))
_COLOR_PREFIXES = ("#", "0x", "rgb(", "rgba(", "hsl(", "hsla(", "oklch(")


def _load_parse_literal_color() -> Any:
    fn = globals().get("parse_literal_color")
    if fn is None:
        from python.frontend.colors import parse_literal_color as fn

        globals()["parse_literal_color"] = fn
    return fn


def __getattr__(name: str) -> Any:
    if name == "parse_literal_color":
        return _load_parse_literal_color()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@lru_cache(maxsize=2048)
def is_theme_variable(val: str) -> bool:
    """Distinguish literal colors from theme variables without misclassifying typos."""
    val = str(val).strip()
    if not val:
        return False
    if val.startswith("__VAR__"):
        return bool(val[7:].strip())

    val_lower = val.lower()

    if _RE_THEME_VAR.search(val_lower):
        return True
    if (
        val_lower.startswith("#")
        or _RE_HEX.fullmatch(val_lower)
        or _RE_HEX_LOWER.fullmatch(val_lower)
        or _RE_CSS_FUNC.match(val_lower)
    ):
        return False
    if not _RE_VAR_IDENT.fullmatch(val_lower):
        return False
    return _load_parse_literal_color()(val, True) is None


def _is_valid_hypr_gradient(text: str) -> bool:
    """Validate Hyprland multi-stop gradients: '<color> <color> [... <angle>deg]'."""
    tokens = _RE_GRADIENT_SPLIT.split(text)
    if len(tokens) < 2:
        return False
    if _RE_DEG.fullmatch(tokens[-1]):
        tokens = tokens[:-1]
    if len(tokens) < 2:
        return False
    parse_color = _load_parse_literal_color()
    return all(
        parse_color(tok, True) is not None or is_theme_variable(tok)
        for tok in tokens
    )


@lru_cache(maxsize=2048)
def color_preview_hex(value: str, color_field: bool = False) -> str | None:
    """Return preview #rrggbb hex for literal colors while keeping plain rows lazy."""
    stripped = value.lstrip()
    if not color_field and not stripped[:6].lower().startswith(_COLOR_PREFIXES):
        return None
    if color_field and is_theme_variable(stripped):
        return None
    color = _load_parse_literal_color()(stripped, color_field)
    return color.hex if color is not None else None


def is_trigger_item(item: Any) -> bool:
    """Checks if a ConfigItem acts as an action trigger via pattern matching."""
    if getattr(item, "type_", None) != "bool":
        return False

    match getattr(item, "options", None):
        case [opt0, *_] if isinstance(opt0, str):
            opt0_lower = opt0.lower()
            return (
                opt0_lower in {"trigger", "copy"}
                or opt0_lower.startswith(("trigger:", "copy:"))
            )
        case _:
            return False


# PEP 695 Strict Type Alias
type ConfigType = Literal[
    "bool", "int", "float", "string", "cycle",
    "action", "menu", "picker", "color", "preset",
]


def clone_value(v: Any) -> Any:
    """Clone acyclic config values; preserve custom types through deepcopy."""
    if v is None:
        return v
    value_type = type(v)
    if value_type in _SCALAR_TYPES:
        return v
    if value_type is list:
        return [clone_value(x) for x in v]
    if value_type is dict:
        return {k: clone_value(x) for k, x in v.items()}
    if value_type is tuple:
        return tuple(clone_value(x) for x in v)
    import copy

    return copy.deepcopy(v)


@dataclass(kw_only=True, slots=True)
class ConfigItem:
    label: str
    key: str
    type_: ConfigType
    default: Any
    scope: str = "DEFAULT"
    options: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    min_val: float | None = None
    max_val: float | None = None
    step: float | None = None
    value: Any = None
    exists_in_target: bool = False

    group: str | None = None
    extended_help: str | None = None
    initial_value: Any = None
    _initial_loaded: bool = False

    preset_payload: dict[str, Any] | None = None
    # Partial presets change only payload keys; ordinary snapshots reset omissions.
    preset_partial: bool = False

    is_parent: bool = False
    parent_ref: str | None = None
    expanded: bool = False

    warning_msg: str | None = None
    popup_message: str | None = None
    confirm_message: str | None = None
    target_file_override: str | None = None
    engine_type_override: str | None = None
    force_interactive: bool | None = None
    read_only: bool = False

    @property
    def uid(self) -> str:
        return f"{self.scope}.{self.key}" if self.scope and self.scope != "DEFAULT" else self.key

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = clone_value(self.default)

    def to_str(self, val: Any) -> str:
        """Return a clean human-readable display string for UI and diff rendering."""
        match val:
            case None:
                return "nil"
            case bool() as b:
                return "true" if b else "false"
            case str(val_str):
                if self.type_ == "color" and val_str.startswith("__VAR__"):
                    return val_str[7:]
                return val_str
            case _:
                return str(val)

    def serialize(self, val: Any) -> str:
        match val:
            case None:
                return "nil"
            case _ if self.type_ == "bool":
                if isinstance(val, str):
                    return "true" if val.strip().lower() in _BOOL_TRUE else "false"
                return "true" if val else "false"
            case _ if self.type_ == "float" and not isinstance(val, bool):
                try:
                    num = float(val)
                except (ValueError, TypeError):
                    return str(val)
                if math.isfinite(num):
                    if num == 0.0:
                        num = 0.0
                    return str(num)
                return str(val)
            case _ if self.type_ == "int" and not isinstance(val, bool):
                if isinstance(val, int):
                    return str(val)
                if isinstance(val, float) and math.isfinite(val) and val.is_integer():
                    return str(int(val))
                if isinstance(val, str):
                    text = val.strip()
                    if _RE_INT.fullmatch(text):
                        return str(int(text, 10))
                return str(val)
            case str(val_str) if self.type_ == "color":
                if val_str.startswith("__VAR__"):
                    return val_str
                if is_theme_variable(val_str):
                    return f"__VAR__{val_str}"
                return val_str
            case _:
                return str(val)

    def values_equal(self, a: Any, b: Any) -> bool:
        """Canonical semantic equality across serialized representations."""
        return self.serialize(a) == self.serialize(b)

    def deserialize(self, raw_val: Any) -> Any:
        match self.type_:
            case "bool":
                if isinstance(raw_val, bool):
                    return raw_val
                return str(raw_val).strip().lower() in _BOOL_TRUE
            case "int" | "float":
                try:
                    if self.type_ == "int":
                        if isinstance(raw_val, str):
                            text = raw_val.strip()
                            if _RE_INT.fullmatch(text):
                                return int(text, 10)
                            val = float(text)
                            return int(val) if math.isfinite(val) else clone_value(self.default)
                        return int(raw_val)
                    value = float(raw_val)
                    return value if math.isfinite(value) else clone_value(self.default)
                except (ValueError, TypeError, OverflowError):
                    return clone_value(self.default)
            case "string" | "picker" | "cycle" | "color":
                if isinstance(raw_val, str):
                    if raw_val.startswith("__VAR__"):
                        return raw_val[7:]
                    if len(raw_val) >= 2 and raw_val.startswith('"') and raw_val.endswith('"'):
                        return raw_val[1:-1]
                elif isinstance(raw_val, int) and not isinstance(raw_val, bool):
                    return str(raw_val)
                elif isinstance(raw_val, float) and math.isfinite(raw_val):
                    return str(int(raw_val)) if raw_val.is_integer() else str(raw_val)
        return raw_val

    def parse_user_input(self, raw: Any) -> Any:
        """Strictly parse and validate user, preset, or CLI input."""
        match self.type_:
            case "bool":
                if isinstance(raw, bool):
                    return raw
                text = str(raw).strip().lower()
                if text in _BOOL_TRUE:
                    return True
                if text in _BOOL_FALSE:
                    return False
                raise ValueError(f"Expected boolean value, got {raw!r}.")

            case "int":
                if isinstance(raw, bool):
                    raise ValueError("Value must be a finite integer.")
                if isinstance(raw, int):
                    parsed = raw
                elif isinstance(raw, float):
                    if not math.isfinite(raw) or not raw.is_integer():
                        raise ValueError("Value must be a finite integer.")
                    parsed = int(raw)
                else:
                    text = str(raw).strip()
                    if _RE_INT.fullmatch(text):
                        parsed = int(text, 10)
                    elif _RE_INT_BASE.fullmatch(text):
                        parsed = int(text, 0)
                    elif _RE_FLOAT.fullmatch(text):
                        as_float = float(text)
                        if not math.isfinite(as_float) or not as_float.is_integer():
                            raise ValueError("Value must be a finite integer.")
                        parsed = int(as_float)
                    else:
                        raise ValueError("Value must be a finite integer.")
                if self.min_val is not None and parsed < self.min_val:
                    raise ValueError(f"Value must be at least {self.min_val}.")
                if self.max_val is not None and parsed > self.max_val:
                    raise ValueError(f"Value must be at most {self.max_val}.")
                return parsed

            case "float":
                if isinstance(raw, bool):
                    raise ValueError("Value must be a finite float.")
                if isinstance(raw, (int, float)):
                    parsed_f = float(raw)
                else:
                    text = str(raw).strip()
                    if not _RE_FLOAT.fullmatch(text):
                        raise ValueError("Value must be a finite float.")
                    parsed_f = float(text)
                if not math.isfinite(parsed_f):
                    raise ValueError("Value must be a finite float.")
                if self.min_val is not None and parsed_f < self.min_val:
                    raise ValueError(f"Value must be at least {self.min_val}.")
                if self.max_val is not None and parsed_f > self.max_val:
                    raise ValueError(f"Value must be at most {self.max_val}.")
                return parsed_f

            case "color":
                text = str(raw).strip()
                if not text:
                    return ""
                if text.startswith("__VAR__"):
                    text = text[7:].strip()
                    if not text:
                        raise ValueError("Empty theme variable name.")
                if (
                    _load_parse_literal_color()(text, True) is not None
                    or is_theme_variable(text)
                    or ((" " in text or "\t" in text) and _is_valid_hypr_gradient(text))
                ):
                    return text
                raise ValueError(f"Invalid color or theme variable: {text!r}.")

            case "cycle":
                text = str(raw)
                if self.options and text not in self.options:
                    raise ValueError(f"Value {text!r} is not in allowed options.")
                return text

            case "string" | "picker":
                return str(raw)

            case _:
                return raw


class BaseEngine(ABC):
    """
    Abstract Base Class enforcing the strict mutator contract for the IoC architecture.
    """

    @property
    @abstractmethod
    def target_path(self) -> str:
        pass

    @abstractmethod
    def load_state(self) -> dict[str, Any]:
        pass

    @abstractmethod
    def write_value(
        self,
        target_key: str,
        target_scope: str,
        new_value: str,
        item_type: str = "string",
    ) -> tuple[bool, str, str]:
        pass

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        success_count = 0
        failed_keys: list[str] = []
        last_debug = ""

        for key, scope, val, itype in changes:
            ok, msg, debug = self.write_value(key, scope, val, item_type=itype)
            if ok:
                success_count += 1
            else:
                failed_keys.append(key)
                if "AUTH_REQUIRED" in msg:
                    return False, msg, debug
            last_debug = debug

        if success_count == len(changes):
            return True, f"Successfully batched {success_count} writes.", last_debug

        return (
            False,
            f"Batch wrote {success_count}/{len(changes)}. Failed keys: {', '.join(failed_keys)}",
            last_debug,
        )
