#!/usr/bin/env python3
"""Lazily loaded modal screens for DuskyTUI."""
from typing import Any

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Input, Label, Markdown, OptionList
from textual.widgets.option_list import Option

from rich.text import Text

from python.frontend.core_types import color_preview_hex, is_trigger_item

_ICON_ARROW = "\uf061"  # nf-fa-arrow-right


def _append_value_preview(
    text: Text,
    value: str,
    style: str = "",
    *,
    prefix: str = "",
    color_field: bool = False,
    swatch_style: str | None = None,
) -> None:
    """Render a color swatch or unchanged text without mutating stored values."""
    color = color_preview_hex(value.strip(), color_field)
    if color is None:
        text.append(prefix + value.rstrip(), style=style)
    else:
        if prefix:
            text.append(prefix, style=style)
        text.append("⬤", style=swatch_style or color)


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "dismiss_false", "Cancel", priority=True),
        Binding("left,h,up,k", "nav_prev", "Previous Option", priority=True),
        Binding("right,l,down,j", "nav_next", "Next Option", priority=True),
        Binding("tab", "nav_next", "Next Option", priority=True),
        Binding("shift+tab", "nav_prev", "Previous Option", priority=True),
        Binding("enter,space", "select_current", "Confirm", priority=True),
        Binding("y", "dismiss_true", "Confirm", priority=True, show=False),
        Binding("n", "dismiss_false", "Cancel", priority=True, show=False),
    ]

    selected_index: reactive[int] = reactive(1)

    def __init__(
        self,
        message: str,
        title: str = "CONFIRM",
        level: str = "warning",
        default_confirm: bool = True,
    ) -> None:
        super().__init__()
        self.message = message
        self.title_text = title
        self.level = level
        self.selected_index = 1 if default_confirm else 0

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="modal-title")
            yield Markdown(self.message, id="confirm-message")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel (n) ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Confirm (y) ", classes="modal-close-btn", id="btn-confirm")

    def on_mount(self) -> None:
        self._update_btn_styles()

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        self._update_btn_styles()

    def _update_btn_styles(self) -> None:
        for idx, b_id in enumerate(("#btn-cancel", "#btn-confirm")):
            try:
                lbl = self.query_one(b_id, Label)
                lbl.set_class(idx == self.selected_index, "-focused")
                lbl.set_class(idx != self.selected_index, "-unfocused")
            except Exception:
                pass

    def action_nav_prev(self) -> None:
        self.selected_index = (self.selected_index - 1) % 2

    def action_nav_next(self) -> None:
        self.selected_index = (self.selected_index + 1) % 2

    def action_select_current(self) -> None:
        self.dismiss(self.selected_index == 1)

    def action_dismiss_false(self) -> None:
        self.dismiss(False)

    def action_dismiss_true(self) -> None:
        self.dismiss(True)

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(False)

    @on(events.Click, "#btn-confirm")
    def on_confirm_click(self) -> None:
        self.dismiss(True)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(False)


class AlertDialog(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss", priority=True),
        Binding("enter,space", "dismiss_modal", "Dismiss", priority=True),
    ]

    def __init__(
        self,
        message: str,
        title: str = "NOTICE",
        level: str = "warning",
        btn_text: str = " OK ",
    ) -> None:
        super().__init__()
        self.message = message
        self.title_text = title
        self.level = level
        self.btn_text = btn_text

    def compose(self) -> ComposeResult:
        with Vertical(id="alert-dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="modal-title")
            yield Markdown(self.message, id="alert-message")

            with Horizontal(classes="modal-btn-container"):
                yield Label(f" {self.btn_text} ", classes="modal-close-btn -focused")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class PasswordScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("tab", "cycle_focus(1)", "Next Control", priority=True),
        Binding("shift+tab", "cycle_focus(-1)", "Previous Control", priority=True),
    ]

    # 0 = Input, 1 = Cancel button, 2 = Authenticate button
    _focus_slot: reactive[int] = reactive(0)

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("SUDO AUTHENTICATION REQUIRED", id="modal-title", classes="-warning")
            yield Markdown(
                "Enter your sudo password to execute system-level actions. "
                "The session will be kept alive automatically.",
                id="alert-message",
            )
            yield Input(placeholder="Password...", password=True, id="password-input")
            yield Label("", id="password-error")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn -unfocused", id="btn-cancel")
                yield Label(" Authenticate ", classes="modal-close-btn -unfocused", id="btn-authenticate")

    def on_mount(self) -> None:
        self.query_one("#password-error", Label).display = False
        self._focus_slot = 0
        self._sync_focus_slot()

    def watch__focus_slot(self, old_val: int, new_val: int) -> None:
        self._sync_focus_slot()

    def _sync_focus_slot(self) -> None:
        try:
            inp = self.query_one("#password-input", Input)
            btn_cancel = self.query_one("#btn-cancel", Label)
            btn_auth = self.query_one("#btn-authenticate", Label)
        except Exception:
            return

        if self._focus_slot == 0:
            inp.focus()
        else:
            self.set_focus(None)

        btn_cancel.set_class(self._focus_slot == 1, "-focused")
        btn_cancel.set_class(self._focus_slot != 1, "-unfocused")
        btn_auth.set_class(self._focus_slot == 2, "-focused")
        btn_auth.set_class(self._focus_slot != 2, "-unfocused")

    @on(events.DescendantFocus)
    def handle_descendant_focus(self, event: events.DescendantFocus) -> None:
        if getattr(event.widget, "id", None) == "password-input" and self._focus_slot != 0:
            self._focus_slot = 0

    def _show_error(self, message: str) -> None:
        err = self.query_one("#password-error", Label)
        err.update(Text(message, style=f"bold {self.app.theme_colors['error']}"))
        err.display = True

    def _clear_error(self) -> None:
        err = self.query_one("#password-error", Label)
        if err.display:
            err.update("")
            err.display = False

    def _submit_password(self) -> None:
        inp = self.query_one("#password-input", Input)
        if not inp.value:
            self._show_error("Password cannot be empty.")
            self._focus_slot = 0
            inp.focus()
            return
        self.dismiss(inp.value)

    def action_cycle_focus(self, delta: int) -> None:
        self._focus_slot = (self._focus_slot + delta) % 3

    def on_key(self, event: events.Key) -> None:
        if self._focus_slot == 0:
            return

        if event.key in ("left", "h"):
            event.stop()
            self._focus_slot = 1 if self._focus_slot == 2 else 2
        elif event.key in ("right", "l"):
            event.stop()
            self._focus_slot = 2 if self._focus_slot == 1 else 1
        elif event.key in ("enter", "space"):
            event.stop()
            if self._focus_slot == 1:
                self.dismiss(None)
            else:
                self._submit_password()
        elif (
            event.is_printable
            and event.character
            and not event.key.startswith(("ctrl+", "alt+"))
        ):
            event.stop()
            inp = self.query_one("#password-input", Input)
            self._focus_slot = 0
            inp.insert_text_at_cursor(event.character)

    @on(Input.Changed, "#password-input")
    def handle_input_changed(self, event: Input.Changed) -> None:
        self._clear_error()

    @on(Input.Submitted, "#password-input")
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit_password()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-authenticate")
    def on_authenticate_click(self) -> None:
        self._submit_password()

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class UnsavedChangesDialog(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "dismiss_cancel", "Cancel", priority=True),
        Binding("left,h,up,k", "nav_prev", "Previous Option", priority=True),
        Binding("right,l,down,j", "nav_next", "Next Option", priority=True),
        Binding("tab", "nav_next", "Next Option", priority=True),
        Binding("shift+tab", "nav_prev", "Previous Option", priority=True),
        Binding("enter,space", "select_current", "Select", priority=True),
        Binding("c", "dismiss_cancel", "Cancel", priority=True, show=False),
        Binding("d", "dismiss_discard", "Discard", priority=True, show=False),
        Binding("s", "dismiss_save", "Save", priority=True, show=False),
    ]

    selected_index: reactive[int] = reactive(2)

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        with Vertical(id="unsaved-dialog", classes="-warning"):
            yield Label("UNSAVED CHANGES", id="modal-title")
            yield Markdown(
                f"You have **{self.count}** unsaved batch changes.\n"
                "Do you want to save them before quitting?",
                id="confirm-message",
            )

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel (c) ", classes="modal-cancel-btn", id="btn-cancel")
                yield Label(" Discard (d) ", classes="modal-cancel-btn", id="btn-discard")
                yield Label(" Save (s) ", classes="modal-close-btn", id="btn-save")

    def on_mount(self) -> None:
        self._update_btn_styles()

    def watch_selected_index(self, old_val: int, new_val: int) -> None:
        self._update_btn_styles()

    def _update_btn_styles(self) -> None:
        for idx, b_id in enumerate(("#btn-cancel", "#btn-discard", "#btn-save")):
            try:
                lbl = self.query_one(b_id, Label)
                lbl.set_class(idx == self.selected_index, "-focused")
                lbl.set_class(idx != self.selected_index, "-unfocused")
            except Exception:
                pass

    def action_nav_prev(self) -> None:
        self.selected_index = (self.selected_index - 1) % 3

    def action_nav_next(self) -> None:
        self.selected_index = (self.selected_index + 1) % 3

    def action_select_current(self) -> None:
        match self.selected_index:
            case 0:
                self.dismiss("cancel")
            case 1:
                self.dismiss("discard")
            case 2:
                self.dismiss("save")

    def action_dismiss_cancel(self) -> None:
        self.dismiss("cancel")

    def action_dismiss_discard(self) -> None:
        self.dismiss("discard")

    def action_dismiss_save(self) -> None:
        self.dismiss("save")

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss("cancel")

    @on(events.Click, "#btn-discard")
    def on_discard_click(self) -> None:
        self.dismiss("discard")

    @on(events.Click, "#btn-save")
    def on_save_click(self) -> None:
        self.dismiss("save")

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss("cancel")


class HybridInputScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("tab", "cycle_focus(1)", "Next Control", priority=True),
        Binding("shift+tab", "cycle_focus(-1)", "Previous Control", priority=True),
        Binding("up", "nav_vertical(-1)", "Previous Option", priority=True, show=False),
        Binding("down", "nav_vertical(1)", "Next Option", priority=True, show=False),
    ]

    def __init__(self, prompt: str, default: str, options: list[Any] | None = None) -> None:
        super().__init__()
        self.prompt_text = prompt
        self.default_text = default
        self.options = options or []
        self._slots = (
            ("input", "list", "cancel", "confirm")
            if self.options
            else ("input", "cancel", "confirm")
        )
        self._slot_idx = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label(self.prompt_text, id="modal-title")
            yield Input(value=self.default_text, id="modal-input")

            if self.options:
                yield Label(" Pre-configured Options (↑/↓ to browse, Enter to select):", id="modal-hint")
                yield OptionList(id="hybrid-option-list")
            else:
                yield Label("Press Enter to save • Tab to navigate • Esc to cancel", id="modal-hint")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-cancel-btn -unfocused", id="btn-cancel")
                yield Label(" Ok ", classes="modal-close-btn -unfocused", id="btn-confirm")

    def on_mount(self) -> None:
        if self.options:
            ol = self.query_one("#hybrid-option-list", OptionList)
            opts = []
            for opt in self.options:
                txt = Text()
                _append_value_preview(txt, str(opt))
                opts.append(Option(txt))
            ol.add_options(opts)

            for idx, opt in enumerate(self.options):
                if str(opt) == self.default_text:
                    ol.highlighted = idx
                    ol.scroll_to_highlight()
                    break

        self._set_slot(0)

    def _set_slot(self, idx: int) -> None:
        self._slot_idx = idx % len(self._slots)
        slot = self._slots[self._slot_idx]
        inp = self.query_one("#modal-input", Input)
        btn_cancel = self.query_one("#btn-cancel", Label)
        btn_confirm = self.query_one("#btn-confirm", Label)

        if slot == "input":
            inp.focus()
        elif slot == "list":
            self.query_one("#hybrid-option-list", OptionList).focus()
        else:
            self.set_focus(None)

        btn_cancel.set_class(slot == "cancel", "-focused")
        btn_cancel.set_class(slot != "cancel", "-unfocused")
        btn_confirm.set_class(slot == "confirm", "-focused")
        btn_confirm.set_class(slot != "confirm", "-unfocused")

    @on(events.DescendantFocus)
    def handle_descendant_focus(self, event: events.DescendantFocus) -> None:
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "modal-input" and self._slots[self._slot_idx] != "input":
            self._set_slot(0)
        elif (
            widget_id == "hybrid-option-list"
            and "list" in self._slots
            and self._slots[self._slot_idx] != "list"
        ):
            self._set_slot(self._slots.index("list"))

    def action_cycle_focus(self, delta: int) -> None:
        self._set_slot(self._slot_idx + delta)

    def _sync_input_from_highlight(self, ol: OptionList, inp: Input) -> None:
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self.options):
            val = str(self.options[idx])
            if inp.value != val:
                inp.value = val
                inp.cursor_position = len(val)

    def action_nav_vertical(self, delta: int) -> None:
        if not self.options:
            return
        slot = self._slots[self._slot_idx]
        if slot == "input":
            self._set_slot(self._slots.index("list"))
            self._move_option(delta)
        elif slot == "list":
            ol = self.query_one("#hybrid-option-list", OptionList)
            if delta < 0 and ol.highlighted == 0:
                self._set_slot(0)
            else:
                self._move_option(delta)
        elif delta < 0:
            self._set_slot(self._slots.index("list"))

    def _move_option(self, delta: int) -> None:
        if not self.options:
            return
        ol = self.query_one("#hybrid-option-list", OptionList)
        inp = self.query_one("#modal-input", Input)
        count = ol.option_count
        if count <= 0:
            return

        current = ol.highlighted
        if current is None or not (0 <= current < count):
            start = -1 if delta > 0 else 0
        else:
            start = current

        for step in range(1, count + 1):
            candidate = (start + step * delta) % count
            if not getattr(ol.get_option_at_index(candidate), "disabled", False):
                ol.highlighted = candidate
                ol.scroll_to_highlight()
                self._sync_input_from_highlight(ol, inp)
                return

    @on(OptionList.OptionHighlighted, "#hybrid-option-list")
    def handle_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if (
            event.option_list.has_focus
            and self.options
            and event.option_index is not None
            and 0 <= event.option_index < len(self.options)
        ):
            self._sync_input_from_highlight(
                event.option_list,
                self.query_one("#modal-input", Input),
            )

    def on_key(self, event: events.Key) -> None:
        slot = self._slots[self._slot_idx]
        inp = self.query_one("#modal-input", Input)

        if slot == "input":
            return

        if slot == "list":
            ol = self.query_one("#hybrid-option-list", OptionList)
            if event.key == "j":
                event.stop()
                self._move_option(1)
            elif event.key == "k":
                event.stop()
                self._move_option(-1)
            elif event.key in ("page_up", "ctrl+u"):
                event.stop()
                ol.action_page_up()
                self._sync_input_from_highlight(ol, inp)
            elif event.key in ("page_down", "ctrl+d"):
                event.stop()
                ol.action_page_down()
                self._sync_input_from_highlight(ol, inp)
            elif event.key == "backspace":
                event.stop()
                self._set_slot(0)
            elif event.key in ("enter", "space"):
                event.stop()
                if ol.highlighted is not None and 0 <= ol.highlighted < len(self.options):
                    self.dismiss(str(self.options[ol.highlighted]))
                else:
                    self.dismiss(inp.value)
            elif (
                event.is_printable
                and event.character
                and not event.key.startswith(("ctrl+", "alt+"))
            ):
                event.stop()
                self._set_slot(0)
                inp.insert_text_at_cursor(event.character)
            return

        # Button slots ("cancel" / "confirm")
        if event.key in ("left", "h", "right", "l"):
            event.stop()
            other = "confirm" if slot == "cancel" else "cancel"
            self._set_slot(self._slots.index(other))
        elif event.key in ("enter", "space"):
            event.stop()
            if slot == "cancel":
                self.dismiss(None)
            else:
                self.dismiss(inp.value)
        elif (
            event.is_printable
            and event.character
            and not event.key.startswith(("ctrl+", "alt+"))
        ):
            event.stop()
            self._set_slot(0)
            inp.insert_text_at_cursor(event.character)

    @on(Input.Submitted, "#modal-input")
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    @on(OptionList.OptionSelected, "#hybrid-option-list")
    def handle_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self.options and event.option_index is not None and 0 <= event.option_index < len(self.options):
            self.dismiss(str(self.options[event.option_index]))
        else:
            self.dismiss(str(event.option.prompt))

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-cancel")
    def on_cancel_click(self) -> None:
        self.dismiss(None)

    @on(events.Click, "#btn-confirm")
    def on_confirm_click(self) -> None:
        inp = self.query_one("#modal-input", Input)
        self.dismiss(inp.value)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class PickerScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("up,k", "cursor_up", "Up", priority=True),
        Binding("down,j", "cursor_down", "Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
    ]

    def __init__(
        self,
        title: str,
        options: list[str],
        hints: list[str],
        current: str | None = None,
    ) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options
        self.hints = hints
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(f"PICKER: {self.picker_title}", id="picker-title")
            yield OptionList(id="picker-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn -focused")

    def on_mount(self) -> None:
        ol = self.query_one("#picker-list", OptionList)
        options_to_add = []
        muted = self.app.theme_colors["muted"]

        for i, opt in enumerate(self.options):
            hint = self.hints[i] if i < len(self.hints) else ""
            txt = Text()
            _append_value_preview(txt, str(opt), "bold", prefix=" ")
            txt.append(" ")
            if hint:
                txt.append(" - ")
                txt.append(hint, style=f"italic {muted}")
            options_to_add.append(Option(txt))

        ol.add_options(options_to_add)
        ol.focus()

        matched_idx: int | None = None
        if self.current is not None:
            for idx, opt in enumerate(self.options):
                if str(opt) == str(self.current):
                    matched_idx = idx
                    break

        if matched_idx is not None:
            ol.highlighted = matched_idx
            ol.scroll_to_highlight()
        elif options_to_add:
            ol.highlighted = 0

    @on(OptionList.OptionSelected, "#picker-list")
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        idx = event.option_index
        if idx is not None and 0 <= idx < len(self.options):
            self.dismiss(self.options[idx])

    def _move_highlight(self, delta: int) -> None:
        ol = self.query_one("#picker-list", OptionList)
        count = ol.option_count
        if count <= 0:
            return

        current = ol.highlighted
        if current is None or not (0 <= current < count):
            start = -1 if delta > 0 else 0
        else:
            start = current

        for step in range(1, count + 1):
            candidate = (start + step * delta) % count
            if not getattr(ol.get_option_at_index(candidate), "disabled", False):
                ol.highlighted = candidate
                ol.scroll_to_highlight()
                return

    def action_cursor_up(self) -> None:
        self._move_highlight(-1)

    def action_cursor_down(self) -> None:
        self._move_highlight(1)

    def action_page_up(self) -> None:
        self.query_one("#picker-list", OptionList).action_page_up()

    def action_page_down(self) -> None:
        self.query_one("#picker-list", OptionList).action_page_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class SearchScreen(ModalScreen[tuple[int, int] | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("FUZZY FIND (Ctrl+F)", id="modal-title")
            yield Input(placeholder="Type to filter configurations...", id="search-input")
            yield OptionList(id="search-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn -focused")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()
        self._search_cache: list[tuple[int, int, Any, str, str, str]] = []
        self._search_timer: Timer | None = None
        self._last_query: str | None = None
        self.results: list[tuple[int, int]] = []

        for tab_idx, tab_items in self.app.schema.items():
            tab_name = self.app.tabs.get(tab_idx, f"Tab {tab_idx}")
            for item_idx, item in enumerate(tab_items):
                if not self.app._item_is_searchable(tab_idx, item):
                    continue
                label_norm = item.label.casefold()
                haystack_compact = "".join(
                    f"{tab_name} {item.label} {item.key} {item.type_}".casefold().split()
                )
                self._search_cache.append(
                    (tab_idx, item_idx, item, tab_name, label_norm, haystack_compact)
                )

        self._populate_list("")

    def on_unmount(self) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None

    @on(Input.Changed, "#search-input")
    def handle_input(self, event: Input.Changed) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(
            0.05,
            lambda query=event.value: self._populate_list(query),
        )

    def _populate_list(self, query: str) -> None:
        query_key = query.casefold().strip()
        if query_key == self._last_query:
            return
        self._last_query = query_key
        ol = self.query_one("#search-list", OptionList)
        ol.clear_options()
        self.results = []

        query_lower = query_key
        query_no_space = "".join(query_key.split())
        scored_results = []

        for tab_idx, item_idx, item, tab_name, label_norm, haystack_compact in self._search_cache:
            if not query_no_space:
                scored_results.append((100, tab_idx, item_idx, item, tab_name))
                continue

            score = 0
            if query_lower == label_norm:
                score += 100
            elif label_norm.startswith(query_lower):
                score += 50
            elif query_lower in label_norm:
                score += 20

            q_idx = 0
            s_idx = 0
            match_positions = []
            while q_idx < len(query_no_space) and s_idx < len(haystack_compact):
                if query_no_space[q_idx] == haystack_compact[s_idx]:
                    match_positions.append(s_idx)
                    q_idx += 1
                s_idx += 1

            if q_idx == len(query_no_space):
                if len(match_positions) > 1:
                    spread = (match_positions[-1] - match_positions[0]) - (len(match_positions) - 1)
                    score += max(0, 15 - spread)
                else:
                    score += 15
                score += 5

            if score > 0:
                scored_results.append((score, tab_idx, item_idx, item, tab_name))

        scored_results.sort(key=lambda x: (-x[0], x[4], x[3].label))

        accent = self.app.theme_colors["accent"]
        muted = self.app.theme_colors["muted"]
        options_to_add = []
        for _score, tab_idx, item_idx, item, tab_name in scored_results:
            txt = Text()
            txt.append(f"[{tab_name}] ", style=accent)
            txt.append(item.label, style="bold")
            if item.hints:
                txt.append(f" - {item.hints[0]}", style=f"italic {muted}")
            options_to_add.append(Option(txt, id=f"search_{tab_idx}_{item_idx}"))
            self.results.append((tab_idx, item_idx))

        ol.add_options(options_to_add)
        ol.highlighted = 0 if self.results else None

    @on(OptionList.OptionSelected, "#search-list")
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index is not None and event.option_index < len(self.results):
            self.dismiss(self.results[event.option_index])

    @on(Input.Submitted, "#search-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._search_timer is not None:
            self._search_timer.stop()
            self._search_timer = None
        self._populate_list(event.value)

        ol = self.query_one("#search-list", OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.results):
            self.dismiss(self.results[ol.highlighted])
        elif self.results:
            self.dismiss(self.results[0])

    def action_cursor_down(self) -> None:
        self.query_one("#search-list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#search-list", OptionList).action_cursor_up()

    def action_page_up(self) -> None:
        self.query_one("#search-list", OptionList).action_page_up()

    def action_page_down(self) -> None:
        self.query_one("#search-list", OptionList).action_page_down()

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class DiffScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,space,q", "dismiss_modal", "Dismiss", priority=True),
        Binding("up,k", "scroll_up", "Scroll Up", priority=True),
        Binding("down,j", "scroll_down", "Scroll Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
        Binding("g", "scroll_home", "Top", priority=True),
        Binding("G", "scroll_end", "Bottom", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Label("MODIFICATIONS (From Launch)", id="modal-title")
            yield OptionList(id="diff-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn -focused")

    def on_mount(self) -> None:
        ol = self.query_one("#diff-list", OptionList)
        seen_keys: set[tuple[str, tuple[str, str]]] = set()
        options_to_add = []
        colors = self.app.theme_colors

        for tab_idx, tab_items in self.app.schema.items():
            tab_name = self.app.tabs.get(tab_idx, f"Tab {tab_idx}")
            for item in tab_items:
                if (
                    not item._initial_loaded
                    or item.type_ in ("action", "preset", "menu")
                    or item.read_only
                    or is_trigger_item(item)
                ):
                    continue
                if item.values_equal(item.value, item.initial_value):
                    continue

                identity = (item.uid, self.app._get_item_engine_info(item))
                if identity in seen_keys:
                    continue
                seen_keys.add(identity)

                txt = Text()
                txt.append(f"[{tab_name}] ", style=colors["accent"])
                txt.append(f"{item.label}: ", style="bold")
                _append_value_preview(
                    txt,
                    item.to_str(item.initial_value) or "Unset",
                    f"strike {colors['error']}",
                    color_field=item.type_ == "color",
                )
                txt.append(" ")
                txt.append(f"{_ICON_ARROW} ", style=colors["muted"])
                _append_value_preview(
                    txt,
                    item.to_str(item.value) or "Unset",
                    f"bold {colors['success']}",
                    color_field=item.type_ == "color",
                )
                options_to_add.append(Option(txt, disabled=True))

        if options_to_add:
            ol.add_options(options_to_add)
        else:
            ol.add_option(Option("No changes detected from initial load state.", disabled=True))

    def action_scroll_up(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#diff-list", OptionList).scroll_end(animate=False)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)


class ShortcutsInfoScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,enter,space,q,f1", "dismiss_modal", "Dismiss", priority=True),
        Binding("up,k", "scroll_up", "Scroll Up", priority=True),
        Binding("down,j", "scroll_down", "Scroll Down", priority=True),
        Binding("page_up,ctrl+u", "page_up", "Page Up", priority=True),
        Binding("page_down,ctrl+d", "page_down", "Page Down", priority=True),
        Binding("g", "scroll_home", "Top", priority=True),
        Binding("G", "scroll_end", "Bottom", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SHORTCUTS", id="modal-title")
            yield OptionList(id="shortcuts-list")

            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn -focused")

    def on_mount(self) -> None:
        ol = self.query_one("#shortcuts-list", OptionList)
        colors = self.app.theme_colors

        bindings_info = [
            ("q, ctrl+c", "Quit the application"),
            ("f1", "Show this shortcuts page"),
            ("f5", "Refresh current TUI state"),
            ("?", "Toggle item documentation panel"),
            ("ctrl+f", "Fuzzy search all options"),
            ("/", "Inline search in current tab"),
            ("n / N", "Next / previous search match (wraps)"),
            ("enter", "Accept search / Next match until Esc"),
            ("escape", "End search navigation / Close modals"),
            ("tab", "Switch to Next Tab"),
            ("shift+tab", "Switch to Previous Tab"),
            ("alt+1..9", "Jump directly to tab N"),
            ("d", "Show pending or modified items (Diff)"),
            ("u", "Undo last change (or batch change)"),
            ("ctrl+r", "Redo last undone change"),
            ("ctrl+t", "Toggle between Auto and Batch save modes"),
            ("ctrl+s", "Commit all pending changes (Batch mode)"),
            ("ctrl+p", "Save current state as a user preset"),
            ("D", "Delete highlighted user preset"),
            ("enter, space", "Trigger action / Toggle boolean / Open Picker / Expand Folder"),
            ("e", "Expand / Collapse nested option menus"),
            ("j, down", "Move cursor down"),
            ("k, up", "Move cursor up"),
            ("h, left, backspace", "Adjust value down / Cycle previous option"),
            ("l, right", "Adjust value up / Cycle next option"),
            ("g", "Scroll to top of list"),
            ("G", "Scroll to bottom of list"),
            ("ctrl+u, page_up", "Page up"),
            ("ctrl+d, page_down", "Page down"),
            ("r", "Reset highlighted item to default"),
            ("R", "Reset entire page to defaults"),
        ]

        options_to_add = []
        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=f"{colors['accent']} bold")
            txt.append(f" {_ICON_ARROW} ", style=colors["muted"])
            txt.append(desc, style=colors["fg"])
            options_to_add.append(Option(txt, disabled=True))
        ol.add_options(options_to_add)

    def action_scroll_up(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_up(animate=False)

    def action_scroll_down(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_down(animate=False)

    def action_page_up(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_page_down(animate=False)

    def action_scroll_home(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        self.query_one("#shortcuts-list", OptionList).scroll_end(animate=False)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)
