#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: STARSHIP PROMPT ENGINE
===============================================================================
Engine Type: "starship"
Target: ~/.config/starship.toml

Switches the active Starship prompt by atomically writing whole-file TOML
content to the target config. Prompt sources:

  - "dusky" (alias) -> Official bundled default theme shipped with the
                        dotfiles (user_scripts/starship/presets/dusky.toml).
  - "preset:<name>"  -> Bundled Starship preset snapshots shipped with the
                        dotfiles (user_scripts/starship/presets/).
  - "custom:<name>"  -> User-made prompts stored in
                        ~/.config/dusky/settings/starship/prompts/ (untracked,
                        therefore persistent through updates).

State is saved to ~/.config/dusky/settings/starship/.dusky_starship_state.json
following the same pattern as the Waybar engine.
===============================================================================
"""

lazy import hashlib
lazy import json
import os
import re
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.frontend.core_types import BaseEngine

_SAFE_NAME_RE = re.compile(r"[^\w\- ]+")


def _content_hash(content: str) -> str:
    return hashlib.blake2b(content.encode("utf-8"), digest_size=16).hexdigest()


class StarshipEngine(BaseEngine):
    def __init__(self, config_path: str = "~/.config/starship.toml"):
        self.config_path = Path(config_path).expanduser().resolve()
        if self.config_path.is_dir():
            self.config_path = self.config_path / "starship.toml"

        self.state_dir = Path("~/.config/dusky/settings/starship").expanduser().resolve()
        self.state_file = self.state_dir / ".dusky_starship_state.json"
        self.custom_dir = self.state_dir / "prompts"

        # Bundled presets live next to the schema (same work tree as this engine).
        self.presets_dir = Path(__file__).resolve().parent.parent.parent.parent / "starship" / "presets"
        if not self.presets_dir.is_dir():
            self.presets_dir = Path("~/user_scripts/starship/presets").expanduser().resolve()

        self.cache: dict[str, Any] = {}

    # =========================================================================
    # PATH HELPERS
    # =========================================================================
    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _bundled_presets(self) -> dict[str, Path]:
        if not self.presets_dir.is_dir():
            return {}
        return {p.stem: p for p in self.presets_dir.glob("*.toml")}

    def _custom_presets(self) -> dict[str, Path]:
        if not self.custom_dir.is_dir():
            return {}
        return {p.stem: p for p in self.custom_dir.glob("*.toml")}

    # =========================================================================
    # CONTENT RESOLUTION
    # =========================================================================
    def _resolve_prompt(self, name: str) -> str | None:
        name = str(name).strip()
        if name.startswith("preset:"):
            path = self.presets_dir / f"{name[7:]}.toml"
        elif name.startswith("custom:"):
            path = self.custom_dir / f"{name[7:]}.toml"
        else:
            path = self.presets_dir / f"{name}.toml"
        try:
            return path.read_text(encoding="utf-8") if path.is_file() else None
        except OSError:
            return None

    def _normalize_name(self, name: str) -> str:
        """Qualifies a bare prompt name into its canonical 'preset:'/'custom:' form."""
        name = str(name).strip()
        if name.startswith(("preset:", "custom:")):
            return name
        if (self.presets_dir / f"{name}.toml").is_file():
            return f"preset:{name}"
        if (self.custom_dir / f"{name}.toml").is_file():
            return f"custom:{name}"
        return name

    def _match_hash(self, content_hash: str) -> str | None:
        """Recovers the prompt name whose file content matches a given hash."""
        candidates: list[tuple[str, str | None]] = []
        for stem, path in self._bundled_presets().items():
            try:
                candidates.append((f"preset:{stem}", path.read_text(encoding="utf-8")))
            except OSError:
                pass
        for stem, path in self._custom_presets().items():
            try:
                candidates.append((f"custom:{stem}", path.read_text(encoding="utf-8")))
            except OSError:
                pass
        for name, content in candidates:
            if content is not None and _content_hash(content) == content_hash:
                return name
        return None

    # =========================================================================
    # STATE
    # =========================================================================
    def load_state(self) -> dict[str, Any]:
        state_data: dict[str, Any] = {}
        if self.state_file.exists():
            try:
                state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state_data = {}

        active = "preset:dusky"
        current_hash: str | None = None
        try:
            if self.config_path.is_file():
                current_hash = _content_hash(self.config_path.read_text(encoding="utf-8"))
        except OSError:
            pass

        saved = state_data.get("active_prompt")
        if saved:
            saved = self._normalize_name(saved)
        if saved and self._resolve_prompt(saved) is not None:
            active = saved
            if current_hash is not None:
                saved_content = self._resolve_prompt(saved)
                if saved_content is None or _content_hash(saved_content) != current_hash:
                    recovered = self._match_hash(current_hash)
                    if recovered:
                        active = recovered
        elif current_hash is not None:
            recovered = self._match_hash(current_hash)
            if recovered:
                active = recovered

        custom_name = str(state_data.get("custom_prompt_name") or "my-custom-prompt")

        self.cache = {
            "active_prompt": active,
            "DEFAULT/active_prompt": active,
            "custom_prompt_name": custom_name,
            "DEFAULT/custom_prompt_name": custom_name,
            "action_save_custom": False,
            "DEFAULT/action_save_custom": False,
        }
        return self.cache

    def _save_state(self, active: str, extra: dict[str, Any] | None = None) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {"active_prompt": active}
            if extra:
                data.update(extra)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    # =========================================================================
    # ATOMIC WRITES
    # =========================================================================
    @staticmethod
    def _atomic_write(path: Path, content: str) -> bool:
        tmp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            if path.exists():
                try:
                    st = path.stat()
                    os.chown(tmp_path, st.st_uid, st.st_gid)
                    tmp_path.chmod(st.st_mode)
                except OSError:
                    pass
            os.replace(tmp_path, path)
            return True
        except OSError:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return False

    def _write_config(self, content: str) -> tuple[bool, str]:
        # If the config is a symlink (e.g. managed by matugen theme generation),
        # os.replace() atomically swaps the symlink itself with a regular file,
        # leaving matugen's generated file untouched elsewhere.
        was_symlink = self.config_path.is_symlink()
        if not self._atomic_write(self.config_path, content):
            return False, f"Failed to write {self.config_path.name}."
        note = "Replaced matugen-managed symlink with a static config. " if was_symlink else ""
        return True, note

    # =========================================================================
    # MUTATORS
    # =========================================================================
    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        self.load_state()

        active = str(self.cache.get("active_prompt", "dusky"))
        custom_name = str(self.cache.get("custom_prompt_name") or "my-custom-prompt")
        status_msg = ""

        for key, scope, val, itype in changes:
            str_val = str(val).lower()

            match key:
                case "active_prompt":
                    new_name = str(val).strip()
                    if not new_name or new_name in ("nil", "None"):
                        continue
                    content = self._resolve_prompt(new_name)
                    if content is None:
                        return False, f"Prompt '{new_name}' not found.", ""
                    ok, note = self._write_config(content)
                    if not ok:
                        return False, note, ""
                    active = self._normalize_name(new_name)
                    status_msg = f"Applied prompt: {new_name}"
                    if note:
                        status_msg = f"{note.strip()} {status_msg}"

                case "custom_prompt_name":
                    custom_name = str(val).strip()
                    self.cache["custom_prompt_name"] = custom_name
                    self.cache["DEFAULT/custom_prompt_name"] = custom_name
                    self._save_state(active, {"custom_prompt_name": custom_name})

                case "action_save_custom" if str_val == "true":
                    name = _SAFE_NAME_RE.sub("_", custom_name).strip(" _")
                    if not name:
                        return False, "Invalid custom prompt name.", ""
                    if not self.config_path.is_file():
                        return False, f"Config file not found: {self.config_path}", ""
                    try:
                        content = self.config_path.read_text(encoding="utf-8")
                    except OSError as e:
                        return False, f"Failed to read config: {e}", ""
                    dest = self.custom_dir / f"{name}.toml"
                    if not self._atomic_write(dest, content):
                        return False, f"Failed to save custom prompt: {name}", ""
                    # The saved file now matches the active config; keep the
                    # state file truthful by switching to it.
                    active = f"custom:{name}"
                    status_msg = f"Saved current prompt as custom: {name} (now active)"

        self.cache["active_prompt"] = active
        self.cache["DEFAULT/active_prompt"] = active
        self._save_state(active, {"custom_prompt_name": custom_name})

        if not status_msg:
            status_msg = "No changes applied."
        return True, status_msg, ""