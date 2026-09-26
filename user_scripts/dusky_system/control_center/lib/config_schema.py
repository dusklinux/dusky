"""Pure, current-format validation for Dusky's TOML configuration."""

from __future__ import annotations

import math
import re
import tomllib
from pathlib import Path
from typing import Any


SECTIONS = {"section", "grid_section"}
ITEMS = {
    "button", "toggle", "label", "slider", "spin", "selection", "entry",
    "secret", "multi_text", "keybind", "color", "path", "navigation",
    "warning_banner", "toggle_card", "grid_card", "expander",
    "directory_generator", "file_generator", "async_selector", "flag_group",
    "service", "service_card",
}
GRID_ITEMS = {"toggle_card", "grid_card", "service_card", "service"}
GENERATORS = {"directory_generator", "file_generator"}
ROW_ITEMS = ITEMS - {"toggle_card", "grid_card", "service_card"}
COMMAND_PROPS = {"state_command", "value_command", "options_command"}
NUMBER_PROPS = {"min", "max", "step", "default"}
BOOL_PROPS = {"debounce", "recursive", "key_inverse", "save_as_int", "compact", "center_title", "show_label"}
PLACEHOLDERS = {"name", "filename", "path", "name_pretty", "relpath", "subdir"}
UNIT_NAME = re.compile(r"[A-Za-z0-9@._-]+\Z")


def _error(where: str, message: str) -> None:
    raise ValueError(f"{where}: {message}")


def _action(value: Any, where: str, page_ids: set[str]) -> None:
    if not isinstance(value, dict):
        _error(where, "action must be a table")
    kind = value.get("type")
    if kind == "redirect":
        target = value.get("page")
        if not isinstance(target, str) or target not in page_ids:
            _error(f"{where}.page", f"unknown page {target!r}")
    elif kind in {"exec", "argv"}:
        command = value.get("command")
        argv = value.get("argv")
        if (command is None) == (argv is None):
            _error(where, "specify exactly one of command or argv")
        if command is not None and (not isinstance(command, str) or not command.strip()):
            _error(f"{where}.command", "must be a nonempty string")
        if argv is not None and (not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv)):
            _error(f"{where}.argv", "must be a nonempty list of nonempty strings")
        if "mode" in value and value["mode"] not in {"launch", "apply"}:
            _error(f"{where}.mode", "must be launch or apply")
        if "timeout" in value and (type(value["timeout"]) is not int or value["timeout"] < 1):
            _error(f"{where}.timeout", "must be a positive integer")
        for flag in ("terminal", "requires_root"):
            if flag in value and type(value[flag]) is not bool:
                _error(f"{where}.{flag}", "must be boolean")
    else:
        _error(f"{where}.type", f"unsupported action type {kind!r}")


def _validate_templates(value: Any, where: str) -> None:
    if isinstance(value, str):
        for name in re.findall(r"\{([^{}]+)\}", value):
            if name not in PLACEHOLDERS and name != "value":
                _error(where, f"unknown placeholder {{{name}}}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _validate_templates(child, f"{where}[{i}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            _validate_templates(child, f"{where}.{key}")


def _node(value: Any, where: str, context: str, page_ids: set[str]) -> None:
    if not isinstance(value, dict):
        _error(where, "must be a table")
    kind = value.get("type")
    allowed = (
        SECTIONS | (ROW_ITEMS - GENERATORS) if context == "layout" else
        GRID_ITEMS | GENERATORS if context == "grid" else
        ROW_ITEMS - GENERATORS if context == "expander" else
        ROW_ITEMS
    )
    if kind not in allowed:
        _error(f"{where}.type", f"unsupported {context} type {kind!r}")
    props = value.get("properties", {})
    if not isinstance(props, dict):
        _error(f"{where}.properties", "must be a table")
    for key, val in props.items():
        path = f"{where}.properties.{key}"
        if key in COMMAND_PROPS | {"key", "path", "service", "scope", "title", "message", "options_command"}:
            if not isinstance(val, str):
                _error(path, "must be a string")
        elif key in NUMBER_PROPS:
            if type(val) not in {int, float} or not math.isfinite(val):
                _error(path, "must be a finite number")
        elif key in BOOL_PROPS:
            if type(val) is not bool:
                _error(path, "must be boolean")
        elif key in {"options_map", "button_text_map", "style_map"}:
            if not isinstance(val, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in val.items()):
                _error(path, "must be a string-to-string table")
    if "interval" in props and (type(props["interval"]) is not int or props["interval"] < 1):
        _error(f"{where}.properties.interval", "must be a positive integer")
    if "persistence" in props and props["persistence"] not in {"app", "action"}:
        _error(f"{where}.properties.persistence", "must be app or action")
    if "key" in props and kind in {"toggle", "toggle_card", "selection", "entry", "spin", "slider"} and "persistence" not in props:
        _error(f"{where}.properties.persistence", "required when key is configured")
    if "options" in props and (not isinstance(props["options"], list) or any(not isinstance(x, str) for x in props["options"])):
        _error(f"{where}.properties.options", "must be a list of strings")
    if "value" in value:
        source = value["value"]
        if isinstance(source, dict):
            source_type = source.get("type")
            if source_type not in {"exec", "file", "system", "static"}:
                _error(f"{where}.value.type", f"unsupported value type {source_type!r}")
            source_field = {"exec": "command", "file": "path", "system": "key", "static": "text"}[source_type]
            if not isinstance(source.get(source_field), str):
                _error(f"{where}.value.{source_field}", "must be a string")
        elif not isinstance(source, str):
            _error(f"{where}.value", "must be a string or table")
    if kind in {"slider", "spin"}:
        low, high, step = (props.get("min", 0), props.get("max", 100), props.get("step", 1))
        if low >= high or step <= 0:
            _error(f"{where}.properties", "numeric range requires min < max and step > 0")
        default = props.get("default", low)
        if not low <= default <= high:
            _error(f"{where}.properties.default", "must be within min and max")
    if kind in {"service", "service_card"}:
        if not isinstance(props.get("service"), str) or not UNIT_NAME.fullmatch(props["service"]) or props["service"].startswith("-"):
            _error(f"{where}.properties.service", "must be a nonempty unit name")
        if props.get("scope", "user") not in {"user", "system"}:
            _error(f"{where}.properties.scope", "must be user or system")
    if kind in GENERATORS:
        if not isinstance(props.get("path"), str) or not props["path"]:
            _error(f"{where}.properties.path", "must be a nonempty string")
        template = value.get("item_template")
        if not isinstance(template, dict):
            _error(f"{where}.item_template", "must be a table")
        _validate_templates(template, f"{where}.item_template")
        _node(template, f"{where}.item_template", "item", page_ids)
    for field in ("on_press", "on_action", "on_change", "on_toggle"):
        action = value.get(field)
        if action is None:
            continue
        path = f"{where}.{field}"
        if field == "on_toggle" and kind in {"toggle", "toggle_card"} and (not isinstance(action, dict) or set(action) != {"enabled", "disabled"}):
            _error(path, "toggle requires enabled and disabled actions")
        if isinstance(action, dict) and "type" not in action:
            for label, child in action.items():
                _action(child, f"{path}.{label}", page_ids)
        else:
            _action(action, path, page_ids)
    buttons = props.get("buttons", [])
    if not isinstance(buttons, list):
        _error(f"{where}.properties.buttons", "must be a list")
    for index, button in enumerate(buttons):
        if not isinstance(button, dict):
            _error(f"{where}.properties.buttons[{index}]", "must be a table")
        if "on_press" in button:
            _action(button["on_press"], f"{where}.properties.buttons[{index}].on_press", page_ids)
    for field, child_context in (("layout", "layout"), ("items", "grid" if kind == "grid_section" else "expander" if kind == "expander" else "item")):
        if field in value:
            children = value[field]
            if not isinstance(children, list):
                _error(f"{where}.{field}", "must be a list")
            for index, child in enumerate(children):
                _node(child, f"{where}.{field}[{index}]", child_context, page_ids)


def validate_config(config: Any) -> None:
    if not isinstance(config, dict) or not isinstance(config.get("pages"), list):
        _error("pages", "must be a list")
    page_ids: set[str] = set()
    for index, page in enumerate(config["pages"]):
        where = f"pages[{index}]"
        if not isinstance(page, dict):
            _error(where, "must be a table")
        page_id = page.get("id")
        if not isinstance(page_id, str) or not page_id.strip():
            _error(f"{where}.id", "must be a nonempty string")
        if page_id in page_ids:
            _error(f"{where}.id", f"duplicate page ID {page_id!r}")
        page_ids.add(page_id)
        if not isinstance(page.get("title"), str) or not page["title"].strip():
            _error(f"{where}.title", "must be a nonempty string")
    for index, page in enumerate(config["pages"]):
        layout = page.get("layout")
        if not isinstance(layout, list):
            _error(f"pages[{index}].layout", "must be a list")
        for section_index, section in enumerate(layout):
            _node(section, f"pages[{index}].layout[{section_index}]", "layout", page_ids)


def validate_file(path: Path) -> None:
    with path.open("rb") as stream:
        validate_config(tomllib.load(stream))
