/* ═══════════════════════════════════════════════════════════════════════════
   Dusky Sites — user configuration overrides (loaded before background.js)

   Uncomment and edit. Every key is optional; unknown keys are ignored.

   Extension-owned keys (configurable only here):
     ecoMode              true    palette changes go to the active tab of each window; other tabs
                                  self-serve when activated. false = also trickle to background tabs
                                  once a burst has settled (their restyle is deferred until visible anyway).
     browserThemeEnabled  true    drive browser.theme (chrome colours)
     fastPaint            true    pre-paint from the per-host paint cache at document_start
     contentColorScheme   'auto'  'auto' | 'light' | 'dark' | 'system' → theme.properties.content_color_scheme
     watchdogMinutes      0.5     alarm cadence that revives the native link after an event-page suspension
     debug                false   console logging + per-burst counters
     disabledSites        []      additional hostnames never themed (merged with the host's list, never pushed)
     paletteTemplate / browserTemplate   role → CSS variable / theme key → role (see BUILTIN in background.js)

   Host-owned keys (source of truth: ~/.config/dusky/settings/dusky_sites/config.json). A value given
   here is pushed with SET_CONFIG on every connect and persisted there — set it only if you want
   defaults.js to win over config.json:
     colorsPath, websitesDir, webThemeEnabled, forceUnthemedWebsites
   ═══════════════════════════════════════════════════════════════════════════ */

'use strict';

// const USER_CONFIG = {
//     debug: false,
//     ecoMode: true,
//     contentColorScheme: 'auto'
// };
