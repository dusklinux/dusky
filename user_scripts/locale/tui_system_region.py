#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: SYSTEM REGION & LOCALE MANAGER SCHEMA
===============================================================================
Target: /etc/locale.gen
Engine: locale_gen
Replaces: ~/user_scripts/locale/locale_tui.sh (Bash)
"""

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "locale_gen"
TARGET_FILE = "/etc/locale.gen"
APP_TITLE = "System Region & Locale Manager"
REQUIRE_ROOT = True

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Time & Date",
    "Popular Locales",
    "English Variants",
    "European Locales",
    "Asian & Other",
    "System Actions"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: TIME & DATE / SYSTEMD REGION
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="NTP Time Synchronization",
            key="ntp_sync",
            scope="DEFAULT",
            type_="action",
            default="timedatectl set-ntp true",
            group="Time & Date",
            extended_help="Enable automatic network time synchronization via systemd-timesyncd (`timedatectl set-ntp true`).",
        ),
        ConfigItem(
            label="RTC in Local Timezone",
            key="rtc_local",
            scope="DEFAULT",
            type_="action",
            default="timedatectl set-local-rtc true",
            group="Time & Date",
            extended_help="Configure real-time hardware clock to maintain local time vs UTC (`timedatectl set-local-rtc true`).",
        ),
        ConfigItem(
            label="Set System Timezone",
            key="action_set_timezone",
            scope="DEFAULT",
            type_="action",
            default="tz=$(timedatectl list-timezones | fzf --prompt='Select System Timezone > ') && [ -n \"$tz\" ] && timedatectl set-timezone \"$tz\"",
            group="Region & Input",
            extended_help="Interactively select and apply system timezone via `timedatectl set-timezone`.",
        ),
        ConfigItem(
            label="Set System Locale (LANG)",
            key="action_set_lang",
            scope="DEFAULT",
            type_="action",
            default="loc=$(grep -v '^#' /etc/locale.gen | awk '{print $1}' | fzf --prompt='Select System LANG Locale > ') && [ -n \"$loc\" ] && localectl set-locale LANG=\"$loc\"",
            group="Region & Input",
            extended_help="Set the primary system `LANG` variable via `localectl set-locale LANG=...`.",
        ),
        ConfigItem(
            label="Set TTY Keyboard Layout",
            key="action_set_keymap",
            scope="DEFAULT",
            type_="action",
            default="km=$(localectl list-keymaps | fzf --prompt='Select TTY Keymap > ') && [ -n \"$km\" ] && localectl set-keymap \"$km\"",
            group="Region & Input",
            extended_help="Set virtual console (vconsole) TTY keyboard layout via `localectl set-keymap`.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: POPULAR LOCALES
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="American English (en_US.UTF-8)",
            key="en_US.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Popular",
            extended_help="Standard American English UTF-8 locale.",
        ),
        ConfigItem(
            label="British English (en_GB.UTF-8)",
            key="en_GB.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="British English UTF-8 locale.",
        ),
        ConfigItem(
            label="German / Deutschland (de_DE.UTF-8)",
            key="de_DE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Standard German UTF-8 locale.",
        ),
        ConfigItem(
            label="French / France (fr_FR.UTF-8)",
            key="fr_FR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Standard French UTF-8 locale.",
        ),
        ConfigItem(
            label="Spanish / España (es_ES.UTF-8)",
            key="es_ES.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Standard Spanish UTF-8 locale.",
        ),
        ConfigItem(
            label="Japanese / 日本語 (ja_JP.UTF-8)",
            key="ja_JP.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Standard Japanese UTF-8 locale.",
        ),
        ConfigItem(
            label="Simplified Chinese / 简体中文 (zh_CN.UTF-8)",
            key="zh_CN.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Simplified Chinese UTF-8 locale.",
        ),
        ConfigItem(
            label="Traditional Chinese / 繁體中文 (zh_TW.UTF-8)",
            key="zh_TW.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Traditional Chinese UTF-8 locale.",
        ),
        ConfigItem(
            label="Korean / 한국어 (ko_KR.UTF-8)",
            key="ko_KR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Popular",
            extended_help="Standard Korean UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: ENGLISH VARIANTS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Australian English (en_AU.UTF-8)",
            key="en_AU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="Australian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Canadian English (en_CA.UTF-8)",
            key="en_CA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="Canadian English UTF-8 locale.",
        ),
        ConfigItem(
            label="Irish English (en_IE.UTF-8)",
            key="en_IE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="Irish English UTF-8 locale.",
        ),
        ConfigItem(
            label="New Zealand English (en_NZ.UTF-8)",
            key="en_NZ.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="New Zealand English UTF-8 locale.",
        ),
        ConfigItem(
            label="Singapore English (en_SG.UTF-8)",
            key="en_SG.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="Singapore English UTF-8 locale.",
        ),
        ConfigItem(
            label="Indian English (en_IN.UTF-8)",
            key="en_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="English",
            extended_help="Indian English UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: EUROPEAN LOCALES
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Italian / Italia (it_IT.UTF-8)",
            key="it_IT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Italian UTF-8 locale.",
        ),
        ConfigItem(
            label="Portuguese / Portugal (pt_PT.UTF-8)",
            key="pt_PT.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Portuguese UTF-8 locale.",
        ),
        ConfigItem(
            label="Brazilian Portuguese (pt_BR.UTF-8)",
            key="pt_BR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Brazilian Portuguese UTF-8 locale.",
        ),
        ConfigItem(
            label="Dutch / Nederland (nl_NL.UTF-8)",
            key="nl_NL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Dutch UTF-8 locale.",
        ),
        ConfigItem(
            label="Polish / Polska (pl_PL.UTF-8)",
            key="pl_PL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Polish UTF-8 locale.",
        ),
        ConfigItem(
            label="Russian / Россия (ru_RU.UTF-8)",
            key="ru_RU.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Russian UTF-8 locale.",
        ),
        ConfigItem(
            label="Swedish / Sverige (sv_SE.UTF-8)",
            key="sv_SE.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Swedish UTF-8 locale.",
        ),
        ConfigItem(
            label="Norwegian / Norge (nb_NO.UTF-8)",
            key="nb_NO.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Norwegian Bokmål UTF-8 locale.",
        ),
        ConfigItem(
            label="Danish / Danmark (da_DK.UTF-8)",
            key="da_DK.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Danish UTF-8 locale.",
        ),
        ConfigItem(
            label="Finnish / Suomi (fi_FI.UTF-8)",
            key="fi_FI.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="European",
            extended_help="Finnish UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: ASIAN & OTHER LOCALES
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Hindi / हिन्दी (hi_IN.UTF-8)",
            key="hi_IN UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Hindi UTF-8 locale.",
        ),
        ConfigItem(
            label="Arabic / Saudi Arabia (ar_SA.UTF-8)",
            key="ar_SA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Arabic UTF-8 locale.",
        ),
        ConfigItem(
            label="Hebrew / Israel (he_IL.UTF-8)",
            key="he_IL.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Hebrew UTF-8 locale.",
        ),
        ConfigItem(
            label="Turkish / Türkiye (tr_TR.UTF-8)",
            key="tr_TR.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Turkish UTF-8 locale.",
        ),
        ConfigItem(
            label="Ukrainian / Україна (uk_UA.UTF-8)",
            key="uk_UA.UTF-8 UTF-8",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Asian & Middle East",
            extended_help="Ukrainian UTF-8 locale.",
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: SYSTEM ACTIONS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Compile Locales (locale-gen)",
            key="action_locale_gen",
            scope="DEFAULT",
            type_="action",
            default="locale-gen",
            group="Actions",
            extended_help="Executes `locale-gen` as root to compile all enabled locales in `/etc/locale.gen` into `/usr/lib/locale/locale-archive`.",
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
