#!/usr/bin/env python3
import re
import copy
from dataclasses import dataclass, field
from typing import Any, Literal
from abc import ABC, abstractmethod

# =============================================================================
# CORE UTILITIES & CONSTANTS
# =============================================================================
KNOWN_COLORS = {
    "Red": (255, 0, 0), "Green": (0, 128, 0), "Lime": (0, 255, 0),
    "Blue": (0, 0, 255), "Yellow": (255, 255, 0), "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255), "White": (255, 255, 255), "Black": (0, 0, 0),
    "Gray": (128, 128, 128), "Silver": (192, 192, 192), "Maroon": (128, 0, 0),
    "Olive": (128, 128, 0), "Purple": (128, 0, 128), "Teal": (0, 128, 128),
    "Navy": (0, 0, 128), "Orange": (255, 165, 0), "Pink": (255, 192, 203),
    "Brown": (165, 42, 42), "Indigo": (75, 0, 130), "Violet": (238, 130, 238),
    "Gold": (255, 215, 0), "Coral": (255, 127, 80), "Salmon": (250, 128, 114),
    "Khaki": (240, 230, 140), "Plum": (221, 160, 221), "Turquoise": (64, 224, 208),
    "Crimson": (220, 20, 60), "Azure": (240, 255, 255), "Beige": (245, 245, 220),
    "Chocolate": (210, 105, 30), "Tomato": (255, 99, 71), "Lavender": (230, 230, 250)
}

KNOWN_COLORS_LOWER = {k.lower(): v for k, v in KNOWN_COLORS.items()}
_LOWER_KNOWN_COLORS = frozenset(k.lower() for k in KNOWN_COLORS)

# Lazy-loaded CSS cache to eliminate import-time module freezing
_css_named_cache: frozenset[str] | None = None

def _get_css_named() -> frozenset[str]:
    global _css_named_cache
    if _css_named_cache is None:
        try:
            import webcolors
            _css_named_cache = frozenset(name.lower() for name in webcolors.names("css4"))
        except (ImportError, Exception):
            _css_named_cache = _LOWER_KNOWN_COLORS
    return _css_named_cache

# Pre-compiled, strictly lower-cased regexes for zero-overhead validation loops
_RE_THEME_VAR = re.compile(r"(\$|@|var\(|\{\{)")
_RE_HEX = re.compile(r"^#?(?:[a-f0-9]{3}|[a-f0-9]{4}|[a-f0-9]{6}|[a-f0-9]{8})$")
_RE_HEX_LOWER = re.compile(r"^0x[a-f0-9]{6,8}$")
_RE_CSS_FUNC = re.compile(r"^(?:rgba?|hsla?|oklch)\s*\(")

def is_theme_variable(val: str) -> bool:
    """Validates if a string is a custom theme variable in O(1) checks."""
    val = str(val).strip()
    if not val:
        return False
    
    val_lower = val.lower()
    
    if _RE_THEME_VAR.search(val_lower):
        return True
    if val_lower in _get_css_named():
        return False
    if _RE_HEX.match(val_lower) or _RE_HEX_LOWER.match(val_lower):
        return False
    if _RE_CSS_FUNC.match(val_lower):
        return False
        
    return True

def is_trigger_item(item: Any) -> bool:
    """Checks if a ConfigItem acts as an action trigger via pattern matching."""
    if getattr(item, "type_", None) != "bool":
        return False
    
    match getattr(item, "options", None):
        case [opt0, *_] if isinstance(opt0, str):
            opt0_lower = opt0.lower()
            return (opt0_lower in {"trigger", "copy"} or 
                    opt0_lower.startswith(("trigger:", "copy:")))
        case _:
            return False

# PEP 695 Strict Type Alias
type ConfigType = Literal["bool", "int", "float", "string", "cycle", "action", "menu", "picker", "color", "preset"]

def clone_value(v: Any) -> Any:
    """Structural clone; avoids deepcopy overhead on scalars and standard structures."""
    match v:
        case None | bool() | int() | float() | str():
            return v
        case list():
            return [clone_value(x) for x in v]
        case dict():
            return {k: clone_value(x) for k, x in v.items()}
        case tuple():
            return tuple(clone_value(x) for x in v)
        case _:
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
    
    is_parent: bool = False
    parent_ref: str | None = None
    expanded: bool = False

    warning_msg: str | None = None
    popup_message: str | None = None
    confirm_message: str | None = None
    target_file_override: str | None = None
    engine_type_override: str | None = None
    force_interactive: bool | None = None

    _ratio_cache: float | None = field(default=None, repr=False, compare=False)

    @property
    def uid(self) -> str:
        return f"{self.scope}.{self.key}" if self.scope and self.scope != "DEFAULT" else self.key

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = clone_value(self.default)

    def serialize(self, val: Any) -> str:
        match val:
            case None:
                return "nil"
            case _ if self.type_ == "bool":
                if isinstance(val, str):
                    return "true" if val.strip().lower() in {"true", "1", "yes", "on", "t", "y"} else "false"
                return "true" if val else "false"
            case str(val_str) if self.type_ == "color" and is_theme_variable(val_str):
                return f"__VAR__{val_str}"
            case _:
                return str(val)

    def deserialize(self, raw_val: Any) -> Any:
        match self.type_:
            case "bool":
                if isinstance(raw_val, bool): 
                    return raw_val
                return str(raw_val).lower() in {"true", "1", "yes", "on"}
            case "int" | "float":
                try:
                    return float(raw_val) if self.type_ == "float" else int(float(raw_val))
                except (ValueError, TypeError): 
                    return self.default
            case "string" | "picker" | "cycle" | "color" if isinstance(raw_val, str):
                if raw_val.startswith("__VAR__"):
                    return raw_val[7:]
                if raw_val.startswith('"') and raw_val.endswith('"'):
                    return raw_val[1:-1]
        return raw_val

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
    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
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
            last_debug = debug
            
        if success_count == len(changes):
            return True, f"Successfully batched {success_count} writes.", last_debug
            
        return False, f"Batch wrote {success_count}/{len(changes)}. Failed keys: {', '.join(failed_keys)}", last_debug
