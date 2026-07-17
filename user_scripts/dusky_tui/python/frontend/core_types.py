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

_LOWER_KNOWN_COLORS = {k.lower() for k in KNOWN_COLORS}

try:
    import webcolors
    _CSS_NAMED = frozenset(name.lower() for name in webcolors.names("css4"))
except Exception:
    _CSS_NAMED = _LOWER_KNOWN_COLORS  # degraded fallback

def is_theme_variable(val: str) -> bool:
    """Validates if a string is a custom theme variable rather than a standard color/hex."""
    val = str(val).strip()
    if not val: return False
    
    val_lower = val.lower()

    # --- NATIVE VARIABLE DETECTION SAFEGUARD ---
    # If the string contains explicit structural variable syntax, it is guaranteed
    # to be a theme variable. This prevents values like `rgba($color, 0.5)` or 
    # `rgb(var(--bg))` from incorrectly getting marked as standard functional colors.
    if re.search(r"(\$|@|var\(|\{\{)", val_lower): 
        return True
    
    # Fast fail for obvious explicit standard color name declarations
    if val_lower in _CSS_NAMED: return False
    
    # Filter out standard Hex structures (with or without #)
    if re.match(r"^#?([a-fA-F0-9]{3}|[a-fA-F0-9]{4}|[a-fA-F0-9]{6}|[a-fA-F0-9]{8})$", val): return False
    
    # Filter out 0x prefixed hex
    if re.match(r"^0x[a-fA-F0-9]{6,8}$", val_lower): return False
    
    # Filter out functional color blocks. Requires opening parenthesis to prevent misidentifying 
    # variables literally named like "rgb-primary".
    if re.match(r"^(rgba?|hsla?|oklch)\s*\(", val_lower): return False
    
    # Everything remaining (Template {{tags}}, Bash $vars, CSS var(--xyz), lexical aliases) is a theme variable
    return True

# Preserving your exact Python 3.12+ type alias syntax from the original ui.py
type ConfigType = Literal["bool", "int", "float", "string", "cycle", "action", "menu", "picker", "color", "preset"]

@dataclass(kw_only=True)
class ConfigItem:
    label: str
    key: str
    scope: str = "DEFAULT"
    type_: ConfigType
    default: Any
    options: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    min_val: float | None = None
    max_val: float | None = None
    step: float | None = None
    value: Any = None
    exists_in_target: bool = False
    
    # Architectural Enhancements
    group: str | None = None
    extended_help: str | None = None
    initial_value: Any = None 
    _initial_loaded: bool = False
    
    # Batch Operations 
    preset_payload: dict[str, Any] | None = None
    
    # Nested Hierarchies (Drop-downs)
    is_parent: bool = False
    parent_ref: str | None = None  # Use the uid of the parent item to nest under
    expanded: bool = False

    # Validation, Prompts, & Multi-Engine Targeting Overrides
    warning_msg: str | None = None
    popup_message: str | None = None
    confirm_message: str | None = None  # Requires explicit dialog confirmation before mutation
    target_file_override: str | None = None
    engine_type_override: str | None = None

    @property
    def uid(self) -> str:
        """Returns a globally unique identifier based on scope and key."""
        if self.scope and self.scope != "DEFAULT":
            return f"{self.scope}.{self.key}"
        return self.key

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = copy.deepcopy(self.default)

    def serialize(self, val: Any) -> str:
        """Centralized serializer to safely prepare values for the backend engines."""
        if val is None: return "nil"
        
        # 1. Type-Aware Coercion (Fixes CLI string injection & robust boolean handling)
        if self.type_ == "bool":
            if isinstance(val, str):
                return "true" if val.strip().lower() in ("true", "1", "yes", "on", "t", "y") else "false"
            return "true" if val else "false"

        val_str = str(val)
        
        # 2. Apply the __VAR__ wrapper for theme variables exclusively at serialization
        if self.type_ == "color" and is_theme_variable(val_str):
            return f"__VAR__{val_str}"
            
        return val_str

    def deserialize(self, raw_val: Any) -> Any:
        """Centralized deserializer to parse raw backend engine strings into clean UI memory state."""
        if self.type_ == "bool":
            if isinstance(raw_val, bool): return raw_val
            return str(raw_val).lower() in ("true", "1", "yes", "on")
        elif self.type_ in ("int", "float"):
            try:
                return float(raw_val) if self.type_ == "float" else int(float(raw_val))
            except (ValueError, TypeError): 
                return self.default
        elif self.type_ in ("string", "picker", "cycle", "color"):
            if isinstance(raw_val, str):
                if raw_val.startswith("__VAR__"):
                    return raw_val[7:]
                if raw_val.startswith('"') and raw_val.endswith('"'):
                    return raw_val[1:-1]
            return raw_val
        return raw_val

class BaseEngine(ABC):
    """
    Abstract Base Class enforcing the strict mutator contract for the IoC architecture.
    
    Engine Contract Expectations:
    - Magic Strings: Return "AUTH_REQUIRED" in the error message if sudo is needed.
    - Null Values: The UI serializes None as the literal string "nil".
    - Optional Methods: Engines can optionally implement `load_state_for_units(user_units, sys_units)`.
    - Batch Writes: `write_batch` should ideally be overridden to be atomic. The base 
      implementation is NOT atomic and falls back to iterative `write_value` calls, 
      which is only safe for idempotent operations.
    """
    
    @property
    @abstractmethod
    def target_path(self) -> str:
        """
        Returns the primary target file path (e.g., ~/.config/hypr/hyprland.conf).
        Used by the UI to render the FileLink component and manage edit events.
        """
        pass

    @abstractmethod
    def load_state(self) -> dict[str, Any]:
        """
        Loads and returns a flattened dictionary of the current parsed state.
        Keys should follow the 'scope/key' or 'key' convention to map to ConfigItems.
        """
        pass

    @abstractmethod
    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        """
        Commits a value change to the configuration backend.
        
        Args:
            target_key: The configuration key to mutate.
            target_scope: The structural scope/category of the key.
            new_value: The stringified new value to inject.
            item_type: The explicitly declared type to guarantee formatting correctly.
            
        Returns:
            tuple[bool, str, str]: (Success boolean, Status/Error message, Debug/Telemetry output)
        """
        pass

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        """
        Commits multiple value changes to the configuration backend.
        Base implementation loops through write_value. Engine subclasses should override 
        this for atomic AST batch writes to prevent I/O bottlenecks.
        
        Args:
            changes: A list of tuples containing (target_key, target_scope, new_value, item_type)
            
        Returns:
            tuple[bool, str, str]: (Success boolean, Status/Error message, Debug/Telemetry output)
        """
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
