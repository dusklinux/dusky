# Row definitions for the Dusky Control Center
from typing import Any
from pathlib import Path
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Pango

import lib.utility as utility

def _build_prefix_icon(icon: dict[str, Any]) -> Gtk.Image:
        """Create a prefix icon with background styling."""
        if isinstance(icon, dict):
            icon_type = icon.get("type", "system")
            if icon_type == "file":
                file_path_template = str(icon.get("path", "")).strip()
                if file_path_template:
                    file_path = file_path_template.replace("~", str(Path.home()))
                    icon = Gtk.Image.new_from_file(file_path)
                    icon.add_css_class("action-row-prefix-icon")
                    return icon
            # Fallback to default icon name if type is unknown
            icon = str(icon.get("name", "utilities-terminal-symbolic"))

        prefix_icon = Gtk.Image.new_from_icon_name(icon)
        prefix_icon.add_css_class("action-row-prefix-icon")
        return prefix_icon

class BaseActionRow(Adw.ActionRow):
    """Base class for custom rows."""

    def __init__(self, properties: dict[str, Any], on_action: dict[str, Any] = {}, context=None) -> None:
        super().__init__()
        self.add_css_class("action-row")

        self.properties = properties
        self.stack = context["stack"] if context else None
        self.content_title_label = context["content_title_label"] if context else None
        self.config = context["config"] if context else None
        self.sidebar = context["sidebar"] if context else None
        self.toast_overlay = context["toast_overlay"] if context else None
        self.on_action = on_action

        title = str(properties.get("title", "Unnamed"))
        subtitle = str(properties.get("description", ""))
        icon = properties.get("icon", "utilities-terminal-symbolic")

        self.set_title(GLib.markup_escape_text(title))
        if subtitle:
            self.set_subtitle(GLib.markup_escape_text(subtitle))

        # Prefix icon with background
        self.add_prefix(_build_prefix_icon(icon))

class ButtonRow(BaseActionRow):
    """A button row for executing commands."""

    def __init__(self, properties: dict[str, Any], on_press: dict[str, Any] = {}, context=None) -> None:
        super().__init__(properties, on_press, context)

        # Run button
        run_btn = Gtk.Button(label="Run")
        run_btn.add_css_class("run-btn")
        run_btn.add_css_class("suggested-action")
        run_btn.set_valign(Gtk.Align.CENTER)
        run_btn.connect("clicked", self._on_button_clicked)

        self.add_suffix(run_btn)
        self.set_activatable_widget(run_btn)

    def _on_button_clicked(self, button: Gtk.Button) -> None:
        """Handle button click."""
        action_type = self.on_action.get("type")
        if action_type == "exec":
            command = str(self.on_action.get("command", "")).strip()
            title = "Command"
            use_terminal = bool(self.on_action.get("terminal", False))
            if not command:
                utility.toast(self.toast_overlay, "⚠ No command specified", timeout=3)
                return

            success = utility.execute_command(command, title, use_terminal)

            if success:
                utility.toast(self.toast_overlay, f"▶ Launched: {title}")
            else:
                utility.toast(self.toast_overlay, f"✖ Failed to launch: {title}", timeout=4)
        elif action_type == "redirect":
            page_id = self.on_action.get("page")
            if page_id and self.stack:
                # Find the page index by id
                pages = self.config.get("pages", [])
                for idx, page in enumerate(pages):
                    if page.get("id") == page_id:
                        self.sidebar.select_row(self.sidebar.get_row_at_index(idx))
                        page_name = str(page.get("title", ""))
                        if self.content_title_label:
                            self.content_title_label.set_label(page_name)

class ToggleRow(BaseActionRow):
    """A toggle row for enabling/disabling settings."""

    def __init__(self, properties: dict[str, Any], on_toggle: dict[str, Any] = {}, context=None) -> None:
        super().__init__(properties, on_toggle, context)
        self.save_as_int = bool(properties.get("save_as_int", False))
        self.key_inverse = bool(properties.get("key_inverse", False))

        # Toggle switch
        toggle_switch = Gtk.Switch()
        toggle_switch.set_valign(Gtk.Align.CENTER)
        if "key" in properties:
            # Load from key if specified
            key = str(properties.get("key", "")).strip()
            system_value = utility.load_setting(key, False, self.key_inverse)
            print(f"[DEBUG] Loaded setting for key '{key}': {system_value}")
            if isinstance(system_value, bool):
                toggle_switch.set_active(system_value)
        toggle_switch.connect("state-set", self._on_toggle_changed)

        self.add_suffix(toggle_switch)
        self.set_activatable_widget(toggle_switch)

    def _on_toggle_changed(self, switch: Gtk.Switch, state: bool) -> None:
        """Handle toggle switch change."""
        print(f"[DEBUG] Toggle changed to: {state}")
        action = self.on_action.get("enabled" if state else "disabled", {})
        action_type = action.get("type")
        if action_type == "exec":
            command = str(action.get("command", "")).strip()
            title = "Toggle Command"
            use_terminal = bool(action.get("terminal", False))

            if command:
                success = utility.execute_command(command, title, use_terminal)
                if not success:
                    utility.toast(self.toast_overlay, f"✖ Failed to execute toggle command", timeout=4)
                
        if "key" in self.properties:
            # Save the new state to settings
            utility.save_setting(self.properties.get("key", ""), (state ^ self.key_inverse), self.save_as_int)

class LabelRow(BaseActionRow):
    """A simple label row."""

    def __init__(self, properties: dict[str, Any], value: dict[str, Any], context=None) -> None:
        super().__init__(properties, {}, context)
        self.value = value

        # Value label
        value_text = self._get_value_text(value)
        value_label = Gtk.Label(label=value_text)
        value_label.set_valign(Gtk.Align.CENTER)
        value_label.set_halign(Gtk.Align.END)
        value_label.set_hexpand(True)
        value_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.add_suffix(value_label)

    def _get_value_text(self, value: dict[str, Any]) -> str:
        """Get the text for a label value."""
        if isinstance(value, str):
            return value
        value_type = value.get("type")
        if value_type == "exec":
            command = str(value.get("command", "")).strip()
            if command:
                try:
                    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=5)
                    return result.stdout.strip() or "N/A"
                except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                    return "Error"
        elif value_type == "static":
            return str(value.get("text", "N/A"))
        elif value_type == "file":
            file_path_template = str(value.get("path", "")).strip()
            if file_path_template:
                file_path = file_path_template.replace("~", str(Path.home()))
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        return f.read().strip() or "N/A"
                except (FileNotFoundError, IOError):
                    return "N/A"
        elif value_type == "system":
            key = str(value.get("key", "")).strip()
            if key:
                system_value = utility.get_system_value(key)
                return system_value if system_value is not None else "N/A"
        
        return "N/A"
    
class SliderRow(BaseActionRow):
    """A slider row for adjusting numeric settings."""

    def __init__(self, properties: dict[str, Any], on_change: dict[str, Any] = {}, context=None) -> None:
        super().__init__(properties, on_change, context)

        # Slider
        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=Gtk.Adjustment(
            value=float(properties.get("default", 0)),
            lower=float(properties.get("min", 0)),
            upper=float(properties.get("max", 100)),
            step_increment=float(properties.get("step", 1)),
            page_increment=float(properties.get("step", 1)) * 10,
            page_size=0,
        ))
        self.slider.set_valign(Gtk.Align.CENTER)
        self.slider.set_hexpand(True)
        self.slider.set_draw_value(False)
        self.slider.connect("value-changed", self._on_slider_changed)
        
        self.slider_changing = False
        self.min_value = float(properties.get("min", 0))
        self.max_value = float(properties.get("max", 100))
        self.step_value = float(properties.get("step", 1))
        self.last_snapped_value = None

        self.add_suffix(self.slider)

    def _on_slider_changed(self, slider: Gtk.Scale) -> None:
        """Handle slider value change."""
        if self.slider_changing:
            return

        current_value = slider.get_value()
        snapped_value = round(current_value / self.step_value) * self.step_value
        snapped_value = max(self.min_value, min(snapped_value, self.max_value))

        if snapped_value % 1 == 0:
            snapped_value = int(snapped_value)

        # Avoid redundant execution
        if self.last_snapped_value is not None and abs(snapped_value - self.last_snapped_value) < 1e-6:
            return

        self.last_snapped_value = snapped_value

        if abs(snapped_value - current_value) > 1e-6:
            self.slider_changing = True
            self.slider.set_value(snapped_value)
            self.slider_changing = False

        # Execute command with snapped value
        action_type = self.on_action.get("type", "")
        if action_type == "exec":
            command_template = str(self.on_action.get("command", "")).strip()
            title = "Slider Command"
            use_terminal = bool(self.on_action.get("terminal", False))

            if command_template:
                command = command_template.replace("{value}", str(int(snapped_value)))
                success = utility.execute_command(command, title, use_terminal)
                if not success:
                    self._toast(f"✖ Failed to execute slider command", timeout=4)