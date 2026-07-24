# 🦊 MatugenFox & Dusky Sites — Architecture & Variable Maintenance Guide

> **Notice for AI Assistants & Maintainers**: This document explains how Matugen CSS variables, WebExtension theme rules, and native Firefox profile stylesheets are configured and how to update them in the future.

---

## 🏛️ System Architecture

```
                          ┌──────────────────────────────────────────────┐
                          │   Matugen Wallpaper Palette Generator        │
                          │   (~/.config/matugen/generated/dusky_sites.css)│
                          └──────────────────────┬───────────────────────┘
                                                 │
                                                 ▼
┌───────────────────────────────┐   ┌───────────────────────────────────────────┐
│     Dusky TUI / CLI Tools     │   │     Linux C-Library Inotify Watcher       │
│  - tui_dusky_sites.py         ├──►│  (dusky_sites_host.py Native Host Daemon)  │
│  - templates/dusky_sites.py   │   └─────────────────────┬─────────────────────┘
└───────────────────────────────┘                         │
                                                          │ Native Messaging (stdio)
                                                          ▼
                                            ┌───────────────────────────┐
                                            │ Firefox WebExtension      │
                                            │ (extension/background.js) │
                                            └─────────────┬─────────────┘
                                                          │
                             ┌────────────────────────────┴───────────────────────────┐
                             ▼                                                        ▼
           ┌───────────────────────────────────┐                    ┌──────────────────────────────────┐
           │ browser.theme.update()            │                    │ content.js                       │
           │ (Native Chrome, Menus & Sidebars) │                    │ (Webpage CSS Variable Injection) │
           └───────────────────────────────────┘                    └──────────────────────────────────┘
```

---

## 📂 File Map & Key Paths

| Component | Path | Description |
| :--- | :--- | :--- |
| **Main Config** | `~/.config/dusky/settings/matugenfox/config.json` | Active extension configuration file |
| **Matugen Template** | `~/.config/matugen/templates/dusky_sites.css` | Matugen template input file |
| **Matugen Generated** | `~/.config/matugen/generated/dusky_sites.css` | Raw generated CSS color palette variables |
| **Website Templates** | `~/.config/dusky_sites/*.css` | Per-domain CSS files for webpage color injection |
| **Native Host Daemon** | `~/.config/firefox_extentions/matugenfox/dusky_sites_host.py` | Event-driven inotify file watcher host daemon |
| **Setup Script (Root)** | `~/.config/firefox_extentions/matugenfox/setup.py` | Profile installer & manifest provisioner |
| **Setup Script (User)** | `~/user_scripts/firefox/theme_matugen/dusky_sites_setup.py` | User setup & desktop sync script |
| **WebExtension Dir** | `~/.config/firefox_extentions/matugenfox/extension/` | WebExtension files (`background.js`, `manifest.json`) |
| **Audit Script** | `~/user_scripts/firefox/theme_matugen/templates/audit_variables.py` | Automated variable verification script |
| **Guide & README** | `~/user_scripts/firefox/theme_matugen/templates/README.md` | This documentation file |

---

## 🎨 How Theme Variables Flow into Firefox

The WebExtension maps colors in two stages inside `extension/background.js`:

### 1. `paletteTemplate` (Matugen Variable ➔ Abstract Role)
```javascript
paletteTemplate: {
    background: '--background',
    backgroundLight: '--surface',
    backgroundExtra: '--surface_container',
    accentPrimary: '--primary',
    accentSecondary: '--secondary',
    text: '--on_background',
    textFocus: '--on_surface',
}
```

### 2. `browserTemplate` (Abstract Role ➔ Firefox LWT Element)
```javascript
browserTemplate: {
    frame: 'background',
    frame_inactive: 'background',
    tab_text: 'textFocus',
    tab_background_text: 'text',
    tab_selected: 'backgroundLight',
    tab_line: 'accentPrimary',
    tab_loading: 'accentPrimary',
    toolbar: 'backgroundLight',
    toolbar_text: 'textFocus',
    toolbar_field: 'backgroundExtra',
    toolbar_field_text: 'textFocus',
    toolbar_field_border: 'backgroundExtra',
    toolbar_field_focus: 'backgroundLight',
    toolbar_field_text_focus: 'textFocus',
    toolbar_field_border_focus: 'accentPrimary',
    toolbar_field_highlight: 'accentPrimary',
    toolbar_field_highlight_text: 'background',
    icons: 'text',
    icons_attention: 'accentPrimary',
    sidebar: 'backgroundLight',
    sidebar_text: 'textFocus',
    sidebar_border: 'backgroundExtra',
    sidebar_highlight: 'accentPrimary',
    sidebar_highlight_text: 'background',
    popup: 'backgroundLight',
    popup_text: 'textFocus',
    popup_border: 'backgroundExtra',
    popup_highlight: 'accentPrimary',
    popup_highlight_text: 'background',
    ntp_background: 'background',
    ntp_card_background: 'backgroundLight',
    ntp_text: 'text',
    bookmark_text: 'textFocus',
    toolbar_top_separator: 'backgroundExtra',
    toolbar_bottom_separator: 'backgroundExtra',
    button_background_hover: 'backgroundExtra',
    button_background_active: 'backgroundExtra',
}
```

---

## 🛠️ Step-by-Step: How to Add or Update Variables in the Future

### Scenario A: You want to map a new Matugen variable to Firefox UI

1. **Open `extension/background.js`**:
   - Add your new role to `paletteTemplate` (e.g. `myRole: '--surface_container_high'`).
   - Assign `myRole` to target elements in `browserTemplate` (e.g. `popup: 'myRole'`).

2. **Update `dusky_menu.css` in Setup Scripts**:
   - If the element requires custom CSS overrides (like popups, context menus, or scrollbars), open both `dusky_sites_setup.py` and `setup.py`.
   - Add your CSS rules into `menu_css_content`.

3. **Re-Run the Setup Script**:
   ```bash
   python3 ~/user_scripts/firefox/theme_matugen/dusky_sites_setup.py
   ```
   *This automatically updates `dusky_menu.css` across all browser profiles in `~/.mozilla/firefox/` and `~/.zen/`.*

---

## 🧪 Automated Audit & Verification

To verify that all variables, paths, and profile stylesheets are correct and unbroken, run the automated verification script:

```bash
python3 ~/user_scripts/firefox/theme_matugen/templates/audit_variables.py
```

### Checking Live in Firefox:
1. Open Firefox ➔ go to `about:debugging#/runtime/this-firefox`.
2. Click **"Load Temporary Add-on..."** and select `extension/manifest.json`.
3. Press **`Ctrl + Alt + Shift + I`** to open Firefox Browser Toolbox to inspect live computed `--lwt-*` CSS variables on `:root`.
