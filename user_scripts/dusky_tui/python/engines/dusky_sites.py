#!/usr/bin/env python3
"""
🦊 Dusky Sites Engine for Dusky TUI (Arch Linux / Python 3.12+)
================================================================
Engine for managing MatugenFox per-site theming configuration and 
templates in ~/.config/dusky_sites/ and ~/.config/dusky/settings/dusky_sites/config.json.
"""

import os
import json
import tempfile
import threading
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

class DuskySitesEngine(BaseEngine):
    """
    Engine for managing MatugenFox configuration and site-specific CSS themes.
    """

    def __init__(self, config_path: str = "~/.config/dusky/settings/dusky_sites/config.json"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.sites_dir = Path("~/.config/dusky_sites").expanduser().resolve()
        self.sites_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.cache: dict[str, Any] = {}

    @property
    def target_path(self) -> str:
        return str(self.config_path)

    def _read_config_json(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {
                "colorsPath": "~/.config/matugen/generated/dusky_sites.css",
                "websitesDir": "~/.config/dusky_sites",
                "webThemeEnabled": False,
                "disabledSites": []
            }
        try:
            content = self.config_path.read_text(encoding="utf-8")
            if content.strip():
                return json.loads(content)
        except Exception:
            pass
        return {}

    def _write_config_json(self, data: dict[str, Any]) -> bool:
        try:
            parent_dir = self.config_path.parent
            parent_dir.mkdir(parents=True, exist_ok=True)

            tmp_file = tempfile.NamedTemporaryFile("w", dir=parent_dir, delete=False, encoding="utf-8")
            tmp_path = Path(tmp_file.name)

            json.dump(data, tmp_file, indent=4, ensure_ascii=False)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_file.close()

            os.replace(tmp_path, self.config_path)
            return True
        except Exception as e:
            if "tmp_path" in locals() and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    def get_site_files(self) -> list[Path]:
        if not self.sites_dir.exists():
            return []
        return sorted(self.sites_dir.glob("*.css"))

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            data = self._read_config_json()
            web_enabled = bool(data.get("webThemeEnabled", False))
            eco_mode = bool(data.get("ecoMode", True))
            browser_enabled = bool(data.get("browserThemeEnabled", True))
            chrome_enabled = bool(data.get("userChromeEnabled", True))
            content_enabled = bool(data.get("userContentEnabled", True))
            disabled_list = [str(s).strip().lower() for s in data.get("disabledSites", []) if s]
            disabled_set = set(disabled_list)

            self.cache = {
                "webThemeEnabled": web_enabled,
                "DEFAULT/webThemeEnabled": web_enabled,
                "ecoMode": eco_mode,
                "DEFAULT/ecoMode": eco_mode,
                "browserThemeEnabled": browser_enabled,
                "DEFAULT/browserThemeEnabled": browser_enabled,
                "userChromeEnabled": chrome_enabled,
                "DEFAULT/userChromeEnabled": chrome_enabled,
                "userContentEnabled": content_enabled,
                "DEFAULT/userContentEnabled": content_enabled,
                "colorsPath": data.get("colorsPath", "~/.config/matugen/generated/dusky_sites.css"),
                "websitesDir": data.get("websitesDir", "~/.config/dusky_sites"),
            }

            site_files = self.get_site_files()
            for css_file in site_files:
                domain = css_file.stem.lower()
                try:
                    content = css_file.read_text(encoding="utf-8")
                    match = re.search(r'@-moz-document\s+domain\("([^"]+)"\)', content)
                    if match:
                        domain = match.group(1).lower()
                except Exception:
                    pass

                is_enabled = domain not in disabled_set and css_file.stem.lower() not in disabled_set
                key_name = f"site_{css_file.stem.lower()}"
                self.cache[key_name] = is_enabled
                self.cache[f"DEFAULT/{key_name}"] = is_enabled
                self.cache[f"domain_{domain}"] = is_enabled

            return self.cache

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No changes pending.", ""

        with self._lock:
            data = self._read_config_json()
            disabled_list = [str(s).strip().lower() for s in data.get("disabledSites", []) if s]
            disabled_set = set(disabled_list)

            status_messages = []

            for key, scope, val, itype in changes:
                str_val = str(val).lower()
                bool_val = str_val in ("true", "1", "yes", "on", "t", "y")

                if key == "webThemeEnabled":
                    data["webThemeEnabled"] = bool_val
                    status_messages.append(f"Web theming set to {bool_val}")

                elif key == "ecoMode":
                    data["ecoMode"] = bool_val
                    status_messages.append(f"Eco mode set to {bool_val}")

                elif key == "browserThemeEnabled":
                    data["browserThemeEnabled"] = bool_val
                    status_messages.append(f"Browser UI theme set to {bool_val}")

                elif key == "userChromeEnabled":
                    data["userChromeEnabled"] = bool_val
                    status_messages.append(f"userChrome.css integration set to {bool_val}")

                elif key == "userContentEnabled":
                    data["userContentEnabled"] = bool_val
                    status_messages.append(f"userContent.css integration set to {bool_val}")

                elif key.startswith("site_") or key.startswith("domain_"):
                    raw_name = key.replace("site_", "").replace("domain_", "").lower()
                    matched_files = []
                    for f in self.get_site_files():
                        f_stem = f.stem.lower()
                        if f_stem == raw_name or f_stem.replace(".", "_") == raw_name or f_stem.replace("-", "_") == raw_name:
                            matched_files.append(f)

                    domains_to_toggle = {raw_name}
                    for f in matched_files:
                        domains_to_toggle.add(f.stem.lower())
                        try:
                            content = f.read_text(encoding="utf-8")
                            for m in re.finditer(r'@-moz-document\s+domain\("([^"]+)"\)', content):
                                domains_to_toggle.add(m.group(1).lower())
                        except Exception:
                            pass

                    disp_name = matched_files[0].stem if matched_files else raw_name

                    if bool_val:
                        for d in domains_to_toggle:
                            disabled_set.discard(d)
                        status_messages.append(f"Enabled theme for {disp_name}")
                    else:
                        for d in domains_to_toggle:
                            disabled_set.add(d)
                        status_messages.append(f"Disabled theme for {disp_name}")

                elif key == "action_add_site":
                    new_domain = str(val).strip().lower()
                    if new_domain:
                        new_file = self.sites_dir / f"{new_domain}.css"
                        if not new_file.exists():
                            template_content = f"""@-moz-document domain("{new_domain}") {{
    :root {{
        --bgColor-default: var(--surface) !important;
        --fgColor-default: var(--on_surface) !important;
        --borderColor-default: var(--outline_variant) !important;
    }}
}}
"""
                            new_file.write_text(template_content, encoding="utf-8")
                            disabled_set.discard(new_domain)
                            status_messages.append(f"Created template for {new_domain}")

                elif key == "action_delete_site":
                    del_domain = str(val).strip().lower()
                    if del_domain:
                        del_file = self.sites_dir / f"{del_domain}.css"
                        if del_file.exists():
                            del_file.unlink()
                            disabled_set.discard(del_domain)
                            status_messages.append(f"Deleted template for {del_domain}")

            data["disabledSites"] = sorted(list(disabled_set))
            ok = self._write_config_json(data)

            if ok:
                msg = "; ".join(status_messages) if status_messages else "Configuration updated."
                return True, msg, ""
            return False, "Failed to write configuration file.", ""
