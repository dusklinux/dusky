#!/usr/bin/env python3
# DUSKY_INTERACTIVE=true
"""Collect account details for the ISO's existing .arch_credentials consumers."""

import argparse
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile


def username_error(value: str) -> str:
    if not value:
        return "Enter a username."
    if value == "root":
        return "Choose a username other than root."
    if len(value) > 32 or re.fullmatch(r"[a-z_][a-z0-9_-]*", value) is None:
        return "Use 1-32 characters: a-z, 0-9, _ or -; start with a-z or _."
    return ""


def password_error(value: str) -> str:
    if not value:
        return "Enter a password."
    # chpasswd and the partitioner's credential reader use line-based input.
    if any(character in value for character in "\n\r\x00"):
        return "Passwords must be a single line without NUL characters."
    return ""


def stage_credentials(user: str, password: str, root_password: str, encrypt: bool) -> None:
    values = {
        "TARGET_USER": user,
        "USER_PASS": password,
        "ROOT_PASS": root_password,
        "ENCRYPT_ROOT": str(int(encrypt)),
        "AUTO_MODE": "1",
    }
    for error in (username_error(user), password_error(password), password_error(root_password)):
        if error:
            raise ValueError(error)
    destination = Path.cwd() / ".arch_credentials"
    # mkstemp creates mode 0600; replace only after the complete file is closed.
    fd, name = tempfile.mkstemp(prefix=".arch_credentials.", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"export {key}={shlex.quote(value)}\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def create_wizard(preset_encrypt: bool | None):
    # Unattended use needs only Python's standard library.
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Input, RadioButton, RadioSet, Static

    class CredentialWizard(App[tuple[str, str, bool] | None]):
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = [
            Binding("ctrl+c", "cancel", "Cancel", priority=True),
            Binding("escape", "back", "Back", priority=True),
        ]
        CSS = """
        Screen { background: $background; color: $foreground; }
        #masthead { height: auto; padding: 1 2; color: $accent; text-style: bold; }
        #viewport { height: 1fr; }
        #center { height: auto; align-horizontal: center; padding: 0 2; }
        #card { width: 62; max-width: 100%; height: auto; margin-top: 2;
                padding: 1 2; border: double $accent; }
        #heading { height: auto; text-style: bold; margin-bottom: 1; }
        #progress { height: auto; color: $accent; margin-bottom: 1; }
        #label { height: auto; text-style: bold; }
        Input { height: 3; border: solid $foreground; background: $background; color: $foreground; }
        Input:focus { border: solid $accent; background-tint: transparent; }
        Input > .input--cursor { color: ansi_black; background: $accent; text-style: none; }
        #hint { height: auto; color: $foreground; text-opacity: 70%; margin-top: 1; }
        #error { height: auto; min-height: 1; color: $error; }
        #actions { height: 3; align-horizontal: center; }
        Button { width: auto; min-width: 20; border: solid $foreground; background: $background; color: $foreground; text-style: none; }
        Button:focus { border: solid $accent; background: $accent; color: ansi_black; text-style: none; }
        #continue { margin-left: 2; }
        #defaults { height: auto; margin-top: 1; color: $foreground; text-opacity: 70%; }
        #review { height: auto; margin-bottom: 1; }
        RadioSet { height: auto; border: none; padding: 0; background: $background; }
        RadioSet:focus { border: none; background-tint: transparent; }
        RadioSet > RadioButton { background: $background; color: $foreground; }
        RadioSet > RadioButton.-on > .toggle--button { color: $accent; text-style: bold; }
        RadioSet:focus > RadioButton.-selected { background: $accent; color: ansi_black; text-style: none; }
        RadioSet:focus > RadioButton.-selected > .toggle--label { background: $accent; color: ansi_black; text-style: none; }
        RadioSet:focus > RadioButton.-selected > .toggle--button { background: $accent; color: ansi_black; text-style: none; }
        #controls { height: auto; padding: 0 1; background: $panel; }
        .small #card { margin-top: 0; padding: 0 1; }
        """

        def __init__(self):
            super().__init__(ansi_color=True)
            self.theme = "ansi-dark"
            self.step = 0
            self.answers = ["", "", ""]
            self.encrypt = bool(preset_encrypt)
            self.review_step = 3 if preset_encrypt is not None else 4

        def compose(self) -> ComposeResult:
            yield Static("DUSKY / SETUP", id="masthead")
            with VerticalScroll(id="viewport"):
                with Horizontal(id="center"):
                    with Vertical(id="card"):
                        yield Static("Create your account", id="heading")
                        yield Static("", id="progress")
                        yield Static("", id="label")
                        yield Input(id="answer", select_on_focus=False)
                        yield RadioSet(
                            RadioButton("No / plain Btrfs", value=True),
                            RadioButton("Yes / LUKS2 encryption"),
                            id="encryption",
                        )
                        yield Static("", id="review", markup=False)
                        yield Static("", id="hint", markup=False)
                        yield Static("", id="error", markup=False)
                        with Horizontal(id="actions"):
                            yield Button("Back", id="back")
                            yield Button("Continue", id="continue")
                        yield Static("", id="defaults")
            yield Static("Enter: continue   Tab: move   Esc: back   Ctrl+C: cancel", id="controls")

        def on_mount(self) -> None:
            self.set_class(self.size.height < 25, "small")
            self.render_step()

        def on_resize(self, event: events.Resize) -> None:
            self.set_class(event.size.height < 25, "small")

        def render_step(self) -> None:
            answer = self.query_one("#answer", Input)
            review = self.step == self.review_step
            encryption = self.step == 3 and not review
            answer.display = self.step < 3
            self.query_one("#encryption").display = encryption
            self.query_one("#review").display = review
            self.query_one("#label").display = not review
            self.query_one("#error", Static).update("")
            back_button = self.query_one("#back", Button)
            continue_button = self.query_one("#continue", Button)
            back_button.display = self.step > 0
            continue_button.styles.margin = (0, 0, 0, 2 if self.step > 0 else 0)
            continue_button.label = "Confirm" if review else "Continue"
            encryption_text = "on / LUKS2" if self.encrypt else "off / plain Btrfs"
            self.query_one("#defaults", Static).update(f"Encryption: {encryption_text}")
            if self.step < 3:
                labels = ("Username", "Password", "Confirm password")
                hints = (
                    "Lowercase letters, numbers, _ or -. Maximum 32 characters.",
                    "One password for your account and root."
                    + (" Also used to unlock your drive." if self.encrypt else ""),
                    "Enter the same password again.",
                )
                self.query_one("#progress", Static).update(
                    f"Step {self.step + 1} of {self.review_step} / {labels[self.step]}"
                )
                self.query_one("#label", Static).update(labels[self.step])
                self.query_one("#hint", Static).update(hints[self.step])
                answer.password = self.step != 0
                answer.placeholder = "e.g. alex" if self.step == 0 else ""
                answer.value = self.answers[self.step]
                answer.focus()
                answer.cursor_position = len(answer.value)
            elif encryption:
                self.query_one("#progress", Static).update("Step 4 of 4 / Encryption")
                self.query_one("#label", Static).update("Encrypt your system?")
                self.query_one("#hint", Static).update(
                    "If enabled, your account password will also unlock the drive.\n"
                    "Up/Down: choose   Space: select   Tab: move to Continue"
                )
                self.query_one("#encryption", RadioSet).focus()
            else:
                self.query_one("#progress", Static).update("Ready / Review your details")
                self.query_one("#review", Static).update(
                    f"Username:    {self.answers[0]}\n"
                    f"Password:    confirmed / same for account and root\n"
                    f"Encryption:  {encryption_text}"
                )
                self.query_one("#hint", Static).update("Use Back to edit, or continue with these details.")
                self.query_one("#continue", Button).focus()

        def advance(self) -> None:
            if self.step < 3:
                answer = self.query_one("#answer", Input)
                value = answer.value
                if self.step == 0:
                    error = username_error(value)
                elif self.step == 1:
                    error = password_error(value)
                else:
                    error = "Passwords do not match." if value != self.answers[1] else ""
                if error:
                    self.query_one("#error", Static).update(error)
                    answer.focus()
                    return
                if self.step == 1 and value != self.answers[1]:
                    self.answers[2] = ""
                self.answers[self.step] = value
            elif self.step == self.review_step:
                result = (self.answers[0], self.answers[1], self.encrypt)
                self.answers = ["", "", ""]
                self.query_one("#answer", Input).value = ""
                self.exit(result)
                return
            else:
                self.encrypt = self.query_one("#encryption", RadioSet).pressed_index == 1
            self.step += 1
            self.render_step()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            self.advance()

        def on_input_changed(self, event: Input.Changed) -> None:
            self.query_one("#error", Static).update("")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "back":
                self.action_back()
            elif event.button.id == "continue":
                self.advance()

        def action_back(self) -> None:
            if self.step == 0:
                return
            if self.step < 3:
                value = self.query_one("#answer", Input).value
                if self.step == 1 and value != self.answers[1]:
                    self.answers[2] = ""
                self.answers[self.step] = value
            self.step -= 1
            self.render_step()

        def action_cancel(self) -> None:
            self.answers = ["", "", ""]
            self.query_one("#answer", Input).value = ""
            self.exit(None)

    return CredentialWizard()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--encrypt", dest="encrypt", action="store_const", const=True, default=None)
    parser.add_argument("--no-encrypt", "--no_encrypt", dest="encrypt", action="store_const", const=False)
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("[ERROR] This script must be run as root.", file=sys.stderr)
        return 1
    user = os.environ.get("TARGET_USER", "")
    password = os.environ.get("USER_PASS", "")
    try:
        if user and password:
            root_password = os.environ.get("ROOT_PASS") or password
            encrypt = bool(args.encrypt)
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print("[ERROR] Use an interactive TTY, or set TARGET_USER and USER_PASS.", file=sys.stderr)
                return 1
            result = create_wizard(args.encrypt).run()
            if result is None:
                print("[INFO] Account setup cancelled; credentials were not changed.")
                return 130
            user, password, encrypt = result
            root_password = password
        stage_credentials(user, password, root_password, encrypt)
    except (OSError, ValueError, ImportError) as error:
        print(f"[ERROR] Account setup failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print("[OK] Account details staged. Continuing installation...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
