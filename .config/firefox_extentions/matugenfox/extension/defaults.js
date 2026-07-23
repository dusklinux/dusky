/* ═══════════════════════════════════════════
   MatugenFox Shared Defaults
   ═══════════════════════════════════════════ */

'use strict';

const DEFAULT_CONFIG = {
    colorsPath: '~/.config/matugen/generated/firefox_websites.css',
    websitesDir: '~/.config/dusky_sites',
    ecoMode: true,
    browserThemeEnabled: true,
    webThemeEnabled: false,
    duckduckgoEnabled: false,
    userChromeEnabled: false,
    userContentEnabled: false,
    fontSize: 13,
    paletteTemplate: {
        background: '--background',
        backgroundLight: '--surface',
        backgroundExtra: '--surface_container',
        accentPrimary: '--primary',
        accentSecondary: '--secondary',
        text: '--on_background',
        textFocus: '--on_surface',
    },
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
        ntp_text: 'text',
        button_background_hover: 'backgroundExtra',
        button_background_active: 'backgroundExtra',
    }
};

function mergeConfig(updates) {
    const m = { ...DEFAULT_CONFIG, ...(updates || {}) };
    if (updates && updates.paletteTemplate) m.paletteTemplate = { ...DEFAULT_CONFIG.paletteTemplate, ...updates.paletteTemplate };
    if (updates && updates.browserTemplate) m.browserTemplate = { ...DEFAULT_CONFIG.browserTemplate, ...updates.browserTemplate };
    return m;
}
