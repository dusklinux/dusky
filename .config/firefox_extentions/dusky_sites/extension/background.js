/* =============================================================================
 * Dusky Sites — Background Engine v6.2.2
 * Firefox 158+ · MV3 event page · wire v3 (delta) native host · no UI
 *
 * HOT PATH — one matugen tick = one ~4 KB MATUGEN_UPDATE frame
 *   adopt()            O(colours)  the rule map is hashed only when the host actually sends it
 *   queueTheme()       adaptive    ≤ 1 theme.update() per max(250 ms, 4 × measured parent cost)
 *   broadcastActive()  O(windows)  tabs.query({active:true}) + per-tab signature dedupe, palette-only frames
 *   persistWarm()      4 KB        storage.session only; disk (seed / paint cache) after a 1.2 s settle
 *
 * INVARIANTS
 *   I1 every listener is registered synchronously at top level (event-page wake-ups).
 *   I2 runtime state rehydrates from storage.session, then from the storage.local seed after a restart.
 *   I3 exactly one native port; its closures are gated on a generation counter.
 *   I4 theme.update() runs only when the payload hash changed AND the limiter admits it; the hash
 *      survives event-page respawns so a respawn never repaints the chrome.
 *   I5 per-tab latest-wins slot + per-tab signature: a tab is never sent what it already has.
 *   I6 rollbacks are state transitions, never per-revision traffic.
 * ===========================================================================*/

'use strict';

(() => {
    /* ── 0. Tunables ─────────────────────────────────────────────────────── */
    const APP = 'Dusky Sites';
    const NATIVE_APP = 'dusky_sites';
    const WIRE = 3;
    const SCHEMA = 8;

    const T = Object.freeze({
        RECONNECT_BASE_MS: 1500,
        RECONNECT_MAX_MS: 120000,
        HANDSHAKE_MS: 5000,
        RPC_MS: 6000,
        IDLE_PING_MS: 120000,
        PING_GRACE_MS: 10000,

        // browser.theme.update() is global when no windowId is supplied.
        // Promise RTT is only a congestion signal, never treated as a direct
        // measurement of Gecko's subsequent chrome restyle/reflow work.
        THEME_MIN_GAP_MS: 250,
        THEME_MAX_GAP_MS: 1500,
        THEME_COST_X: 4,
        THEME_EWMA_ALPHA: 0.25,

        TAB_SLOT_MS: 16,
        SETTLE_MS: 1200,
        TRICKLE_BATCH: 24,
        TRICKLE_MS: 50,
        CHUNK_TTL_MS: 15000,
        REASSERT_MAX: 3,
        REASSERT_WINDOW_MS: 60000
    });

    const LIM = Object.freeze({
        OUTBOX: 16,
        RULES_CACHE: 256,
        DOMAIN_CACHE: 256,
        DOMAIN_TTL_MS: 600000,
        DOMAIN_NEG_TTL_MS: 60000,
        PAINT_ENTRIES: 64,
        PAINT_ENTRY_BYTES: 49152,
        CHUNK_BYTES: 8388608,
        VAR_COUNT: 640,
        SITE_CSS_BYTES: 524288,
        DISABLED_SITES: 512,
        TRACKED_TABS: 4096
    });

    const ALARM = 'dusky.watchdog';

    /* ── 1. Logging + counters ───────────────────────────────────────────── */
    let DEBUG = false;
    const TAG = `[${APP}]`;
    const log = (...a) => { if (DEBUG) console.log(TAG, ...a); };
    const warn = (...a) => { if (DEBUG) console.warn(TAG, ...a); };
    const fail = (...a) => console.error(TAG, ...a);
    const NO_RECEIVER = /Receiving end does not exist|Could not establish connection|message port closed/i;
    const swallow = (e) => { if (e && !NO_RECEIVER.test(String(e?.message ?? e))) warn(e); };

    const stats = {
        hostFrames: 0, revs: 0, ruleRevs: 0,
        themeUpdates: 0, themeSkipped: 0, themeLastCostMs: 0,
        tabSends: 0, tabSkips: 0, paletteOnly: 0, rollbacks: 0,
        contentPulls: 0, paintWrites: 0, settles: 0
    };

    /* ── 2. Primitives ───────────────────────────────────────────────────── */
    function hash32(s) {
        let h = 0x811c9dc5;
        for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
        return h.toString(36);
    }

    class Lru {
        #max; #m = new Map();
        constructor(max) { this.#max = max; }
        get(k) {
            if (!this.#m.has(k)) return undefined;
            const v = this.#m.get(k); this.#m.delete(k); this.#m.set(k, v);
            return v;
        }
        set(k, v) {
            this.#m.delete(k); this.#m.set(k, v);
            if (this.#m.size > this.#max) this.#m.delete(this.#m.keys().next().value);
            return this;
        }
        delete(k) { return this.#m.delete(k); }
        clear() { this.#m.clear(); }
        get size() { return this.#m.size; }
    }

    const clamp = (n, lo, hi) => (n < lo ? lo : n > hi ? hi : n);
    const now = () => Date.now();

    /* ── 3. Colour engine (pure arithmetic; chrome theme only) ───────────── */
    const NAMED = { transparent: [0, 0, 0, 0], black: [0, 0, 0, 1], white: [255, 255, 255, 1] };
    const to255 = (n) => clamp(Math.round(n), 0, 255);
    const num = (t, scale) => {
        if (typeof t !== 'string') return NaN;
        const v = parseFloat(t);
        if (!Number.isFinite(v)) return NaN;
        return t.endsWith('%') ? (v / 100) * scale : v;
    };
    const hueDeg = (t) => {
        const v = parseFloat(t);
        if (!Number.isFinite(v)) return NaN;
        if (t.endsWith('turn')) return v * 360;
        if (t.endsWith('grad')) return v * 0.9;
        if (t.endsWith('rad')) return v * 180 / Math.PI;
        return v;
    };
    function hslToRgb(h, s, l) {
        h = (((h % 360) + 360) % 360) / 360; s = clamp(s, 0, 1); l = clamp(l, 0, 1);
        if (s === 0) { const g = to255(l * 255); return [g, g, g]; }
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
        const ch = (t) => {
            if (t < 0) t += 1; if (t > 1) t -= 1;
            if (t < 1 / 6) return p + (q - p) * 6 * t;
            if (t < 1 / 2) return q;
            if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
            return p;
        };
        return [to255(ch(h + 1 / 3) * 255), to255(ch(h) * 255), to255(ch(h - 1 / 3) * 255)];
    }
    /** @returns {{r:number,g:number,b:number,a:number}|null} */
    function parseColor(raw) {
        if (typeof raw !== 'string') return null;
        const s = raw.trim().toLowerCase();
        if (!s || s.length > 64) return null;
        if (s.charCodeAt(0) === 35) {
            const hex = s.slice(1);
            if (!/^[0-9a-f]+$/.test(hex)) return null;
            const x = (i) => parseInt(hex[i] + hex[i], 16), y = (i) => parseInt(hex.slice(i, i + 2), 16);
            switch (hex.length) {
                case 3: return { r: x(0), g: x(1), b: x(2), a: 1 };
                case 4: return { r: x(0), g: x(1), b: x(2), a: x(3) / 255 };
                case 6: return { r: y(0), g: y(2), b: y(4), a: 1 };
                case 8: return { r: y(0), g: y(2), b: y(4), a: y(6) / 255 };
                default: return null;
            }
        }
        const fn = s.match(/^(rgba?|hsla?)\s*\(([^()]*)\)$/);
        if (fn) {
            const p = fn[2].replaceAll('/', ' ').split(/[\s,]+/).filter(Boolean);
            if (p.length < 3) return null;
            const a = p.length > 3 ? clamp(num(p[3], 1), 0, 1) : 1;
            if (!Number.isFinite(a)) return null;
            if (fn[1].startsWith('rgb')) {
                const r = num(p[0], 255), g = num(p[1], 255), b = num(p[2], 255);
                if (![r, g, b].every(Number.isFinite)) return null;
                return { r: to255(r), g: to255(g), b: to255(b), a };
            }
            const h = hueDeg(p[0]), sa = num(p[1], 1), li = num(p[2], 1);
            if (![h, sa, li].every(Number.isFinite)) return null;
            const [r, g, b] = hslToRgb(h, sa, li);
            return { r, g, b, a };
        }
        const n = Object.hasOwn(NAMED, s) ? NAMED[s] : null;
        return n ? { r: n[0], g: n[1], b: n[2], a: n[3] } : null;   // lab()/oklch()/color-mix(): not for theme.update
    }
    const hex2 = (n) => n.toString(16).padStart(2, '0');
    const toHex = (c) => '#' + hex2(c.r) + hex2(c.g) + hex2(c.b);
    /** Composite over an opaque backdrop: translucent structural surfaces show compositor seams. */
    function flatten(c, bg) {
        if (c.a >= 0.999) return c;
        return { r: to255(c.r * c.a + bg.r * (1 - c.a)), g: to255(c.g * c.a + bg.g * (1 - c.a)), b: to255(c.b * c.a + bg.b * (1 - c.a)), a: 1 };
    }
    const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    /** CIE L* — Material You's "is this tone light" boundary is L* = 50. */
    function lstar(c) {
        const y = 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
        return y <= 216 / 24389 ? y * 24389 / 27 : Math.cbrt(y) * 116 - 16;
    }

    /* ── 4. Configuration ────────────────────────────────────────────────── */
    const BUILTIN = Object.freeze({
        ecoMode: true,
        browserThemeEnabled: true,
        webThemeEnabled: false,          // webpage theming requires explicit opt-in
        forceUnthemedWebsites: false,    // idem
        fastPaint: true,
        contentColorScheme: 'auto',      // auto | light | dark | system
        watchdogMinutes: 0.5,
        debug: false,
        disabledSites: [],
        paletteTemplate: {
            background: '--background', backgroundLight: '--surface', backgroundExtra: '--surface_container',
            accentPrimary: '--primary', accentSecondary: '--secondary', text: '--on_background', textFocus: '--on_surface'
        },
        browserTemplate: {
            frame: 'background', frame_inactive: 'background',
            tab_text: 'textFocus', tab_background_text: 'text', tab_selected: 'backgroundLight',
            tab_line: 'accentPrimary', tab_loading: 'accentPrimary',
            toolbar: 'backgroundLight', toolbar_text: 'textFocus',
            toolbar_field: 'backgroundExtra', toolbar_field_text: 'textFocus', toolbar_field_border: 'backgroundExtra',
            toolbar_field_focus: 'backgroundLight', toolbar_field_text_focus: 'textFocus', toolbar_field_border_focus: 'accentPrimary',
            toolbar_field_highlight: 'accentPrimary', toolbar_field_highlight_text: 'background',
            icons: 'text', icons_attention: 'accentPrimary',
            sidebar: 'backgroundLight', sidebar_text: 'textFocus', sidebar_border: 'backgroundExtra',
            sidebar_highlight: 'accentPrimary', sidebar_highlight_text: 'background',
            popup: 'backgroundLight', popup_text: 'textFocus', popup_border: 'backgroundExtra',
            popup_highlight: 'accentPrimary', popup_highlight_text: 'background',
            ntp_background: 'background', ntp_card_background: 'backgroundLight', ntp_text: 'text',
            bookmark_text: 'textFocus', toolbar_top_separator: 'backgroundExtra', toolbar_bottom_separator: 'backgroundExtra',
            button_background_hover: 'backgroundExtra', button_background_active: 'backgroundExtra'
        }
    });

    /** Colour keys Gecko 158 honours (tab_background_separator / toolbar_field_separator died in 89). */
    const VALID_THEME_KEYS = new Set([
        'bookmark_text', 'button_background_active', 'button_background_hover', 'frame', 'frame_inactive',
        'icons', 'icons_attention', 'ntp_background', 'ntp_card_background', 'ntp_text',
        'popup', 'popup_border', 'popup_highlight', 'popup_highlight_text', 'popup_text',
        'sidebar', 'sidebar_border', 'sidebar_highlight', 'sidebar_highlight_text', 'sidebar_text',
        'tab_background_text', 'tab_line', 'tab_loading', 'tab_selected', 'tab_text',
        'toolbar', 'toolbar_bottom_separator', 'toolbar_field', 'toolbar_field_border', 'toolbar_field_border_focus',
        'toolbar_field_focus', 'toolbar_field_highlight', 'toolbar_field_highlight_text', 'toolbar_field_text',
        'toolbar_field_text_focus', 'toolbar_text', 'toolbar_top_separator', 'toolbar_vertical_separator'
    ]);
    const OPAQUE_THEME_KEYS = new Set(['frame', 'frame_inactive', 'toolbar', 'toolbar_field', 'toolbar_field_focus',
        'popup', 'sidebar', 'ntp_background', 'ntp_card_background', 'tab_selected']);

    const BOOL_KEYS = ['ecoMode', 'browserThemeEnabled', 'webThemeEnabled', 'forceUnthemedWebsites', 'fastPaint', 'debug'];
    const HOST_KEYS = ['colorsPath', 'websitesDir', 'webThemeEnabled', 'forceUnthemedWebsites'];
    const SCHEMES = new Set(['auto', 'light', 'dark', 'system']);
    const SAFE_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const CSS_VAR_KEY = /^--[A-Za-z0-9_-]{1,64}$/;
    const SAFE_VALUE = /^[\w#(),.%\/\s+-]{1,96}$/;     // colour tokens only
    const BAD_VALUE = /url\s*\(|expression\s*\(/i;

    function cleanSites(list) {
        const out = [];
        for (const s of list) {
            if (typeof s !== 'string') continue;
            const v = s.trim().toLowerCase();
            if (v && v.length <= 253 && !out.includes(v)) out.push(v);
            if (out.length >= LIM.DISABLED_SITES) break;
        }
        return out;
    }
    function sanitizeTemplate(src, ok) {
        const out = {};
        if (!src || typeof src !== 'object') return out;
        for (const k of Object.keys(src)) {
            if (k === '__proto__' || k === 'constructor' || k === 'prototype') continue;
            const v = src[k];
            if (typeof v === 'string' && ok(k, v)) out[k] = v;
        }
        return out;
    }
    /** Typed, allow-listed merge. Unknown keys are dropped. */
    function mergeConfig(base, up) {
        const out = { ...base };
        if (!up || typeof up !== 'object') return out;
        for (const k of BOOL_KEYS) if (typeof up[k] === 'boolean') out[k] = up[k];
        if (SCHEMES.has(up.contentColorScheme)) out.contentColorScheme = up.contentColorScheme;
        if (up.watchdogMinutes !== undefined) { const n = Number(up.watchdogMinutes); if (Number.isFinite(n)) out.watchdogMinutes = clamp(n, 0.25, 10); }
        if (Array.isArray(up.disabledSites)) out.disabledSites = cleanSites(up.disabledSites);
        if (up.paletteTemplate) out.paletteTemplate = { ...base.paletteTemplate, ...sanitizeTemplate(up.paletteTemplate, (k, v) => SAFE_KEY.test(k) && CSS_VAR_KEY.test(v)) };
        if (up.browserTemplate) out.browserTemplate = { ...base.browserTemplate, ...sanitizeTemplate(up.browserTemplate, (k, v) => VALID_THEME_KEYS.has(k) && SAFE_KEY.test(v)) };
        return out;
    }
    /** defaults.js may declare `const USER_CONFIG` — a global lexical binding, NOT a globalThis property. */
    function userConfig() {
        try { return typeof USER_CONFIG === 'object' && USER_CONFIG ? USER_CONFIG : null; } catch { return null; }
    }
    /** Host-owned keys the user pinned in defaults.js — the only thing ever pushed with SET_CONFIG. */
    function hostOverrides() {
        const u = userConfig();
        if (!u) return null;
        const o = {};
        for (const k of HOST_KEYS) {
            if (!(k in u)) continue;
            const v = u[k];
            if (k === 'colorsPath' || k === 'websitesDir') { if (typeof v === 'string' && v && v.length < 4096 && !v.includes('\u0000')) o[k] = v.trim(); }
            else if (typeof v === 'boolean') o[k] = v;
        }
        return Object.keys(o).length ? o : null;
    }

    /* ── 5. State + rehydration (I2) ─────────────────────────────────────── */
    const S = {
        cfg: mergeConfig(BUILTIN, userConfig()),
        enabled: true,
        theme: null,      // frozen {colors, colorsRev, websites, websitesRev, disabled, disabledRev, status, at}
        palette: null,    // frozen {vars, hash} — derived once per colour revision, shared by every tab
        port: null, ready: false, gen: 0, attempt: 0, nextAt: 0, rxAt: 0, connectedAt: 0, lastError: null,
        themeHash: null, themeCost: 0, themeAt: 0, themeBusy: false, themeDirty: false, themeEpoch: 0, scheme: 'dark',
        wantTrickle: false,
        tm: { retry: 0, ping: 0, hs: 0, theme: 0, settle: 0, trickle: 0 }
    };
    DEBUG = !!S.cfg.debug;
    const clearT = (k) => { if (S.tm[k]) { clearTimeout(S.tm[k]); S.tm[k] = 0; } };

    const rulesCache = new Lru(LIM.RULES_CACHE);    // host -> rules entry | null
    const domainCache = new Lru(LIM.DOMAIN_CACHE);  // host -> {css,isDarkSite,hints,neg,at}
    const domainInflight = new Map();               // host -> Promise
    const slots = new Map();                        // tabId -> queued delivery
    const lastSig = new Map();                      // tabId -> acknowledged 'p/r[/s]' | 'x' | '?'
    const tabEpoch = new Map();                     // tabId -> newest delivery/pull epoch
    let tabEpochSeq = 0;
    const paintDirty = new Map();                   // host -> paint entry | null (tombstone)
    let paintIndex = [];                            // [[host, rulesHash], …] MRU order, mirrors paint:<host> keys on disk
    let paintWork = Promise.resolve();               // serialize cache clear/publish operations
    let paletteWritten = null;                      // palette hash last written to paint:palette
    let seedHash = null, seedSitesRev = null;

    let bootP = null;
    const ready = () => (bootP ??= boot().catch((e) => fail('boot failed', e)));

    async function boot() {
        let [local, sess] = await Promise.all([
            browser.storage.local.get([
                'schema',
                'enabled',
                'seed',
                'seedSites',
                'paintIndex',
                'paint:palette'
            ]).catch(() => ({})),

            browser.storage.session.get([
                'colors',
                'sites',
                'meta'
            ]).catch(() => ({}))
        ]);

        if (local.schema !== SCHEMA) {
            await migrate();

            // The snapshots above predate migrate(), so invalidate their
            // revision-bearing records in memory too.
            local = {
                ...local,
                schema: SCHEMA,
                seed: undefined,
                seedSites: undefined,
                paintIndex: [],
                'paint:palette': undefined
            };

            sess = {
                ...sess,
                colors: undefined,
                sites: undefined
            };
        }

        if (typeof local.enabled === 'boolean') {
            S.enabled = local.enabled;
        }

        paintIndex = Array.isArray(local.paintIndex)
            ? local.paintIndex.filter(
                (e) =>
                    Array.isArray(e) &&
                    typeof e[0] === 'string' &&
                    typeof e[1] === 'string'
            )
            : [];

        paletteWritten = typeof local['paint:palette']?.hash === 'string' ? local['paint:palette'].hash : null;
        const meta = sess.meta;

        if (meta && typeof meta === 'object') {
            S.themeHash =
                typeof meta.themeHash === 'string'
                    ? meta.themeHash
                    : null;

            S.scheme =
                meta.scheme === 'light'
                    ? 'light'
                    : 'dark';

            if (Array.isArray(meta.tabs)) {
                for (const id of meta.tabs) {
                    if (Number.isInteger(id)) {
                        lastSig.set(id, '?');
                    }
                }
            }
        }

        // Schema-8 seeds always carry the host's canonical revisions.
        const seedSites =
            local.seedSites &&
            typeof local.seedSites === 'object' &&
            local.seedSites.websites &&
            typeof local.seedSites.websitesRev === 'string'
                ? local.seedSites
                : null;

        const seed =
            local.seed &&
            typeof local.seed === 'object' &&
            local.seed.colors &&
            typeof local.seed.colorsRev === 'string'
                ? {
                    ...local.seed,
                    ...(seedSites || {})
                }
                : null;

        const src =
            sess.colors &&
            typeof sess.colors === 'object' &&
            sess.colors.colors
                ? {
                    ...(seed || {}),
                    ...sess.colors,
                    ...(sess.sites || {})
                }
                : seed;

        if (seedSites) {
            seedSitesRev = seedSites.websitesRev;
        }

        // Prevent an unchanged cold seed from being needlessly rewritten
        // after every event-page reconstruction.
        if (seed) {
            const disabled =
                Array.isArray(seed.disabledSites)
                    ? cleanSites(seed.disabledSites)
                    : [];

            const disabledRev = hash32(disabled.join('\n'));

            seedHash = hash32([
                seed.colorsRev,
                disabledRev,
                !!seed.webThemeEnabled,
                !!seed.forceUnthemedWebsites
            ].join('|'));
        }

        if (src?.colors) {
            adopt(src, false);
        }

        if (S.cfg.browserThemeEnabled) {
            if (S.theme) {
                queueTheme();
            }
        } else if (S.themeHash) {
            resetTheme();
        }

        await ensureWatchdog(false);

        if (S.enabled) {
            connect();
        }

        browser.action.setTitle({
            title: S.enabled ? APP : `${APP} (paused)`
        }).catch(swallow);
    }

    /**
     * Schema 8 invalidates older paint entries and revision-bearing state.
     * Old paint entries may refer to disabled hosts or obsolete scan semantics.
     */
    async function migrate() {
        try {
            const records = await browser.storage.local.get(null);
            const paintKeys = Object.keys(records).filter((key) => key.startsWith('paint:'));
            await Promise.all([
                browser.storage.local.remove([...paintKeys, 'paintIndex',
                    'config',
                    'themeData',
                    'paintCache',
                    'seed',
                    'seedSites'
                ]),

                browser.storage.session.remove([
                    'colors',
                    'sites'
                ])
            ]);

            await browser.storage.local.set({
                schema: SCHEMA
            });
        } catch (e) {
            swallow(e);
        }
    }

    /* ── 6. URL helpers + matcher ────────────────────────────────────────── */
    const RESTRICTED_HOSTS = new Set(['addons.mozilla.org', 'accounts.firefox.com', 'support.mozilla.org',
        'discovery.addons.mozilla.org', 'install.mozilla.org']);
    const urlOf = (u) => { try { return new URL(u); } catch { return null; } };
    function isInjectable(url) {
        const p = url ? urlOf(url) : null;
        return !!p && (p.protocol === 'http:' || p.protocol === 'https:') && !RESTRICTED_HOSTS.has(p.hostname);
    }
    const hostOf = (url) => urlOf(url)?.hostname.toLowerCase() ?? '';

    /**
     * Specificity-scored domain matcher (deterministic ordering):
     *   exact 1000+len · canonical www 950+len · explicit wildcard 500+len · single label 100+len.
     * A pattern without a wildcard never matches arbitrary subdomains.
     */
    function matchScore(hostname, pattern, allowSingleLabel) {
        const h = String(hostname || '').toLowerCase().trim().replace(/\.+$/, '');
        let raw = String(pattern || '').toLowerCase().trim().replace(/\.+$/, '');
        if (!h || !raw) return 0;
        if (!raw.includes('.') && raw.includes('_')) raw = raw.replaceAll('_', '.');   // stem form: gemini_google_com
        const wildcard = raw.startsWith('*.') || raw.startsWith('.');
        const d = raw.replace(/^\*?\./, '');
        if (!d) return 0;
        if (h === d) return 1000 + d.length;
        if (h === 'www.' + d) return 950 + d.length;
        if (d === 'www.' + h) return 950 + h.length;
        if (wildcard && h.endsWith('.' + d)) return 500 + d.length;
        if (allowSingleLabel && !d.includes('.')) {
            const parts = (h.startsWith('www.') ? h.slice(4) : h).split('.').filter(Boolean);
            if (parts.length >= 2 && parts[0] === d) return 100 + d.length;
        }
        return 0;
    }
    function isSiteDisabled(host) {
        for (const d of S.theme?.disabled ?? []) if (matchScore(host, d, true) > 0) return true;
        for (const d of S.cfg.disabledSites) if (matchScore(host, d, true) > 0) return true;
        return false;
    }

    /* ── 7. Native port (I3) ─────────────────────────────────────────────── */
    const outbox = [];
    const rpc = new Map();       // rid -> {resolve,reject}
    const chunks = new Map();    // id -> {parts,total,bytes,at}
    let ridSeq = 0;

    const known = () => ({
        websitesRev: S.theme?.websitesRev ?? null
    });

    function connect() {
        if (!S.enabled || S.port) return;
        clearT('retry');
        let port;
        try { port = browser.runtime.connectNative(NATIVE_APP); }
        catch (e) { S.lastError = String(e?.message ?? e); fail('connectNative threw:', S.lastError); scheduleReconnect(); return; }

        const gen = ++S.gen;
        const live = () => S.gen === gen && S.port === port;
        S.port = port; S.ready = false; S.connectedAt = S.rxAt = now();
        port.onMessage.addListener((m) => { if (live()) onHostMessage(m); });
        port.onDisconnect.addListener(() => { if (live()) onHostDisconnect(port); });

        // A native manifest can exist while the interpreter is broken: demand proof of life.
        S.tm.hs = setTimeout(() => {
            S.tm.hs = 0;
            if (live() && !S.ready) { S.lastError = 'handshake timeout'; warn(S.lastError); teardown(port); scheduleReconnect(); }
        }, T.HANDSHAKE_MS);

        rawSend({ type: 'HELLO', wire: WIRE, extension: browser.runtime.id, known: known() });
        const ov = hostOverrides();
        if (ov) rawSend({ type: 'SET_CONFIG', config: ov });
        rawSend({ type: 'FETCH_NOW', known: known() });
        armPing();
        log('port opened gen', gen);
    }

    function teardown(port) {
        if (!port) return;
        try { port.disconnect(); } catch { /* already gone */ }
        if (S.port === port) {
            S.port = null; S.ready = false; S.gen++;
            clearT('ping'); clearT('hs');
            for (const e of rpc.values()) e.reject(new Error('port closed'));
            rpc.clear(); chunks.clear();
        }
    }

    function onHostDisconnect(port) {
        S.lastError = port.error?.message || browser.runtime.lastError?.message || 'host closed the pipe';
        const lived = now() - S.connectedAt;
        teardown(port);
        if (lived > 30000) S.attempt = 0;   // a healthy host restarted: retry immediately
        if (S.enabled) scheduleReconnect();
    }

    /** Decorrelated-jitter backoff (many profiles must not retry in lockstep). */
    function scheduleReconnect() {
        clearT('retry');
        if (!S.enabled) return;
        const exp = Math.min(T.RECONNECT_BASE_MS * 2 ** S.attempt, T.RECONNECT_MAX_MS);
        const delay = Math.round(exp / 2 + Math.random() * exp / 2);
        S.attempt = Math.min(S.attempt + 1, 24);
        S.nextAt = now() + delay;
        S.tm.retry = setTimeout(() => { S.tm.retry = 0; connect(); }, delay);
    }

    /** Half-open detector: a wedged interpreter keeps the pipe nominally open. */
    function armPing() {
        clearT('ping');
        S.tm.ping = setTimeout(() => {
            S.tm.ping = 0;
            if (!S.port) return;
            if (now() - S.rxAt < T.IDLE_PING_MS) { armPing(); return; }
            const port = S.port, gen = S.gen;
            rawSend({ type: 'PING', at: now() });
            setTimeout(() => {
                if (S.gen !== gen || S.port !== port) return;
                if (now() - S.rxAt > T.IDLE_PING_MS + T.PING_GRACE_MS) {
                    S.lastError = 'host unresponsive'; warn(S.lastError);
                    teardown(port); S.attempt = 0; scheduleReconnect();
                } else armPing();
            }, T.PING_GRACE_MS);
        }, T.IDLE_PING_MS);
    }

    function rawSend(frame) {
        const port = S.port;
        if (!port) return false;
        try { port.postMessage(frame); return true; }
        catch (e) { S.lastError = String(e?.message ?? e); warn('postMessage failed:', e); teardown(port); scheduleReconnect(); return false; }
    }
    const COLLAPSIBLE = new Set(['SET_CONFIG', 'FETCH_NOW', 'LIVE_THEME_RESPONSE', 'PING', 'PONG', 'HELLO']);
    function send(frame) {
        if (S.port && S.ready) return rawSend(frame);
        if (COLLAPSIBLE.has(frame.type)) for (let i = outbox.length - 1; i >= 0; i--) if (outbox[i].type === frame.type) outbox.splice(i, 1);
        outbox.push(frame);
        while (outbox.length > LIM.OUTBOX) outbox.shift();
        return false;
    }
    function flushOutbox() {
        if (!S.port) return;
        for (const f of outbox.splice(0)) if (!rawSend(f)) break;
    }
    /** rid-correlated request over the stream (the host echoes rid). */
    function request(frame) {
        const rid = ++ridSeq;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { rpc.delete(rid); reject(new Error('rpc timeout ' + frame.type)); }, T.RPC_MS);
            rpc.set(rid, {
                resolve: (v) => { clearTimeout(timer); rpc.delete(rid); resolve(v); },
                reject: (e) => { clearTimeout(timer); rpc.delete(rid); reject(e); }
            });
            send({ ...frame, rid });
        });
    }
    /** Host frames above 1 MiB arrive as {type:'CHUNK', id, seq, total, part}. */
    function reassemble(msg) {
        const id = String(msg.id || '');
        if (!id) return null;
        const t = now();
        for (const [k, v] of chunks) if (t - v.at > T.CHUNK_TTL_MS) chunks.delete(k);
        let slot = chunks.get(id);
        if (!slot) { slot = { parts: [], total: msg.total | 0, bytes: 0, at: t }; chunks.set(id, slot); }
        const part = typeof msg.part === 'string' ? msg.part : '';
        slot.parts[msg.seq | 0] = part;
        slot.bytes += part.length;
        if (slot.bytes > LIM.CHUNK_BYTES) { chunks.delete(id); fail('chunk stream exceeded cap'); return null; }
        let have = 0;
        for (let i = 0; i < slot.total; i++) if (typeof slot.parts[i] === 'string') have++;
        if (have < slot.total) return null;
        chunks.delete(id);
        try { return JSON.parse(slot.parts.join('')); } catch (e) { fail('chunk JSON parse failed', e); return null; }
    }

    /* ── 8. Inbound host protocol ────────────────────────────────────────── */
    async function onHostMessage(raw) {
        S.rxAt = now(); stats.hostFrames++;
        if (!S.ready) { S.ready = true; S.attempt = 0; S.lastError = null; clearT('hs'); flushOutbox(); log('handshake complete'); }
        if (!raw || typeof raw !== 'object') return;
        let msg = raw;
        if (msg.type === 'CHUNK') { msg = reassemble(msg); if (!msg) return; }
        if (msg.rid !== undefined && rpc.has(msg.rid)) { rpc.get(msg.rid).resolve(msg); return; }
        await ready();
        switch (msg.type) {
            case 'MATUGEN_UPDATE': onPaletteUpdate(msg.data); return;
            case 'DOMAIN_FIX_RESPONSE': cacheDomainFix(msg); return;
            case 'HELLO_ACK':
                if (
                    (msg.wire | 0) <
                    WIRE
                ) {
                    fail(
                        `native host speaks wire ${msg.wire}; wire ${WIRE} required — reinstall dusky_sites_host.py`
                    );
                }

                return;
            case 'QUERY_LIVE_THEME': replyLiveTheme(); return;
            case 'PING': send({ type: 'PONG', at: now() }); return;   // host keep-alive: the reply is also our liveness proof
            case 'PONG': return;
            default: log('host frame', msg.type, msg);
        }
    }

    /**
     * Merge a host frame (or a stored seed) into S.theme.
     * @returns {null|{colors:boolean,rules:boolean,webOn:boolean,webOff:boolean}}
     */
    function adopt(d, fromHost) {
        if (
            !d ||
            typeof d !== 'object' ||
            !d.colors ||
            typeof d.colors !== 'object' ||
            typeof d.colorsRev !== 'string' ||
            typeof d.websitesRev !== 'string'
        ) {
            return null;
        }

        const prev = S.theme;

        const hasSites =
            !!d.websites &&
            typeof d.websites === 'object';

        // A delta is only valid if we already possess exactly the rule
        // revision it references. Never silently pair new metadata with
        // an older rule map.
        if (
            !hasSites &&
            (!prev || d.websitesRev !== prev.websitesRev)
        ) {
            warn(
                'host omitted websites for an unknown revision; requesting full snapshot'
            );

            send({
                type: 'FETCH_NOW',
                known: {
                    websitesRev: null
                }
            });

            return null;
        }

        const websites =
            hasSites
                ? d.websites
                : prev.websites;

        // These identities are computed over canonical JSON by the native host.
        // Do not re-hash the objects independently in JavaScript.
        const websitesRev = d.websitesRev;
        const colorsRev = d.colorsRev;

        const disabled =
            Array.isArray(d.disabledSites)
                ? cleanSites(d.disabledSites)
                : (prev?.disabled ?? []);

        const disabledRev =
            hash32(disabled.join('\n'));

        const webBefore =
            S.cfg.webThemeEnabled;

        const forcedBefore =
            S.cfg.forceUnthemedWebsites;

        const patch = {};

        if (typeof d.webThemeEnabled === 'boolean') {
            patch.webThemeEnabled = d.webThemeEnabled;
        }

        if (typeof d.forceUnthemedWebsites === 'boolean') {
            patch.forceUnthemedWebsites =
                d.forceUnthemedWebsites;
        }

        S.cfg = mergeConfig(S.cfg, patch);

        const webOn =
            !webBefore &&
            S.cfg.webThemeEnabled;

        const webOff =
            webBefore &&
            !S.cfg.webThemeEnabled;

        const forcedChanged =
            forcedBefore !==
            S.cfg.forceUnthemedWebsites;

        const colors =
            colorsRev !== prev?.colorsRev;

        const rules =
            websitesRev !== prev?.websitesRev ||
            disabledRev !== prev?.disabledRev ||
            forcedChanged;

        if (
            !colors &&
            !rules &&
            !webOn &&
            !webOff
        ) {
            return null;
        }

        S.theme = Object.freeze({
            colors: d.colors,
            colorsRev,

            websites,
            websitesRev,

            disabled,
            disabledRev,

            status:
                Array.isArray(d.status)
                    ? d.status.slice(0, 8)
                    : null,

            at:
                Number(d.timestamp) ||
                now()
        });

        if (colors || !S.palette) {
            S.palette = buildPalette(d.colors);
            stats.revs++;
        }

        if (rules) {
            rulesCache.clear();
            stats.ruleRevs++;
        }

        if (fromHost) {
            persistWarm(hasSites && rules);
        }

        return {
            colors,
            rules,
            webOn,
            webOff
        };
    }

    function onPaletteUpdate(d) {
        const ch = adopt(d, true);
        if (!ch) { log('frame carried no change'); return; }
        if (ch.colors && S.cfg.browserThemeEnabled) queueTheme();
        // Rule/disabled-site transitions invalidate every cached host, including
        // background tabs that eco mode deliberately does not visit.
        if (ch.rules || ch.webOff) clearPaint();
        if (ch.webOff) rollbackAll();
        else if (S.cfg.webThemeEnabled && (ch.colors || ch.rules || ch.webOn)) {
            broadcastActive(false);
            if (!S.cfg.ecoMode) S.wantTrickle = true;
        }
        armSettle();
    }

    function cacheDomainFix(m) {
        if (!m || typeof m.domain !== 'string') return;
        const host = m.domain.toLowerCase();
        domainCache.set(host, {
            css: typeof m.css === 'string' ? m.css.slice(0, LIM.SITE_CSS_BYTES) : '',
            isDarkSite: !!m.isDarkSite,
            hints: Array.isArray(m.hints) ? m.hints.filter((s) => typeof s === 'string' && s.length < 200).slice(0, 16) : null,
            neg: false, at: now()
        });
        rulesCache.delete(host);
        browser.tabs.query({ active: true, discarded: false }).then((tabs) => {
            for (const t of tabs) if (hostOf(t.url) === host) pushTab(t.id, t.url, false);
        }).catch(swallow);
    }

    function requestDomainFix(host) {
        if (domainInflight.has(host)) return;
        const p = request({ type: 'GET_DOMAIN_FIX', domain: host })
            .then(cacheDomainFix)
            .catch(() => { domainCache.set(host, { css: '', isDarkSite: false, hints: null, neg: true, at: now() }); })
            .finally(() => domainInflight.delete(host));
        domainInflight.set(host, p);
    }

    async function replyLiveTheme() {
        try { send({ type: 'LIVE_THEME_RESPONSE', theme: await browser.theme.getCurrent() }); }
        catch (e) { warn('theme.getCurrent failed', e); }
    }

    /* ── 9. Browser chrome theme — adaptive limiter (I4) ─────────────────── */
    function queueTheme() { S.themeDirty = true; scheduleTheme(); }

    function scheduleTheme() {
        if (
            S.tm.theme ||
            S.themeBusy ||
            !S.themeDirty
        ) {
            return;
        }

        const gap = clamp(
            S.themeCost * T.THEME_COST_X,
            T.THEME_MIN_GAP_MS,
            T.THEME_MAX_GAP_MS
        );

        S.tm.theme = setTimeout(
            runTheme,
            Math.max(
                0,
                S.themeAt + gap - now()
            )
        );
    }

    async function runTheme() {
        S.tm.theme = 0;

        if (!S.themeDirty) {
            return;
        }

        S.themeDirty = false;

        if (
            !S.enabled ||
            !S.cfg.browserThemeEnabled ||
            !S.theme
        ) {
            return;
        }

        const payload =
            buildThemePayload(S.theme.colors);

        const h =
            hash32(JSON.stringify(payload));

        if (h === S.themeHash) {
            stats.themeSkipped++;
            return;
        }

        const epoch = S.themeEpoch;

        S.themeBusy = true;

        const t0 = performance.now();

        let ok = false;

        try {
            await browser.theme.update(payload);
            ok = true;
        } catch (e) {
            fail('theme.update rejected:', e);
        }

        const rtt =
            performance.now() - t0;

        if (epoch === S.themeEpoch) {
            S.themeHash =
                ok
                    ? h
                    : null;

            if (ok) {
                stats.themeUpdates++;
            }
        }

        /*
         * browser.theme.update() promise latency is a congestion signal.
         * It is NOT a direct measurement of Gecko's subsequent chrome
         * restyle/reflow/paint cost, so retain the hard 250 ms floor.
         */
        if (ok) {
            S.themeCost =
                S.themeCost
                    ? S.themeCost +
                        T.THEME_EWMA_ALPHA *
                        (rtt - S.themeCost)
                    : rtt;

            stats.themeLastCostMs =
                Math.round(rtt);
        }

        S.themeAt = now();
        S.themeBusy = false;

        await persistMeta();

        // A newer palette may have dirtied us while theme.update() was pending.
        scheduleTheme();
    }

    async function resetTheme() {
        S.themeEpoch++;
        S.themeHash = null;
        S.themeDirty = false;

        clearT('theme');

        try {
            await browser.theme.reset();
        } catch (e) {
            warn(e);
        }

        await persistMeta();
    }

    function rolePalette(colors) {
        const p = {};
        for (const [role, cssVar] of Object.entries(S.cfg.paletteTemplate)) p[role] = colors[cssVar] ?? null;
        return p;
    }

    function buildThemePayload(colors) {
        const roles = rolePalette(colors);
        const bg = flatten(parseColor(roles.background) || { r: 24, g: 26, b: 27, a: 1 }, { r: 0, g: 0, b: 0, a: 1 });
        const out = {};
        for (const [key, role] of Object.entries(S.cfg.browserTemplate)) {
            if (!VALID_THEME_KEYS.has(key)) continue;
            const c = parseColor(roles[role]);
            if (c) out[key] = toHex(OPAQUE_THEME_KEYS.has(key) ? flatten(c, bg) : c);
        }
        out.frame ??= toHex(bg);
        // Hysteresis: a burst hovering around L* = 50 must not flip prefers-color-scheme for every content document.
        const L = lstar(parseColor(out.frame) || bg);
        if (S.scheme === 'dark' ? L > 55 : L < 45) S.scheme = S.scheme === 'dark' ? 'light' : 'dark';
        out.tab_background_text ??= S.scheme === 'light' ? '#15141a' : '#fbfbfe';
        const cs = S.cfg.contentColorScheme;
        return { colors: out, properties: { color_scheme: S.scheme, content_color_scheme: cs === 'auto' ? S.scheme : cs } };
    }

    /* ── 10. CSS factory: palette once per revision, rules once per host ── */
    function buildPalette(colors) {
        const vars = {};
        let n = 0;
        for (const k of Object.keys(colors)) {
            if (!CSS_VAR_KEY.test(k)) continue;
            const v = colors[k];
            if (typeof v !== 'string') continue;
            const val = v.trim();
            if (!SAFE_VALUE.test(val) || BAD_VALUE.test(val)) continue;
            vars[k] = val;
            if (++n >= LIM.VAR_COUNT) break;
        }
        return Object.freeze({ vars, hash: hash32(JSON.stringify(vars)) });
    }

    const FALLBACK_CSS = [
        '@media screen {',
        '  :root { color-scheme: var(--color-scheme, dark) !important; }',
        '  html, body, header, nav, main, footer, aside, section, article,',
        '  form, table, thead, tbody, tfoot, tr, td, th, ul, ol, li, dl, dt, dd,',
        '  details, summary, figure, fieldset, legend,',
        '  [class*="card"], [class*="header"], [class*="footer"], [class*="sidebar"], [class*="panel"], [class*="box"] {',
        '    background-color: var(--background, var(--surface, #181a1b)) !important;',
        '    color: var(--on_background, var(--on_surface, #e0e0e0)) !important;',
        '    border-color: var(--outline_variant, rgba(255,255,255,.08)) !important;',
        '  }',
        '  [class*="overlay"], [class*="backdrop"], [class*="off-canvas"], [class*="canvas"], [class*="wrapper"], [id*="wrapper"], [class*="screenshot"] { background-color: transparent !important; }',
        '  h1, h2, h3, h4, h5, h6, p, li, dt, dd, label, b, strong, i, em, small, mark, blockquote { color: var(--on_background, var(--on_surface, inherit)) !important; }',
        '  a:link, a:link *, [role="link"] { color: var(--primary, #8ab4f8) !important; }',
        '  a:visited, a:visited * { color: var(--tertiary, #c58af9) !important; }',
        '  a:hover, a:hover * { color: var(--primary, #8ab4f8) !important; text-decoration: underline; }',
        '  pre, code, kbd, samp { background-color: var(--surface_container_high, var(--surface, #2b2a33)) !important; color: var(--on_surface, inherit) !important; border-radius: 4px; }',
        '  button, select, textarea, option, optgroup, [role="button"], [role="combobox"], [role="option"], [role="listbox"] {',
        '    background-color: var(--surface_container, var(--surface, #2b2a33)) !important;',
        '    color: var(--on_surface, #fbfbfe) !important; border-color: var(--outline, rgba(255,255,255,.12)) !important;',
        '  }',
        '  [class*="search"] input, form input, [role="combobox"] input, input[type="text"], input[type="search"] { background-color: transparent !important; color: var(--on_surface, #e0e0e0) !important; box-shadow: none !important; }',
        '  input[type="checkbox"], input[type="radio"], input[type="range"], progress { accent-color: var(--primary_container, #8ab4f8) !important; }',
        '  input::placeholder, textarea::placeholder { color: var(--on_surface_variant, rgba(255,255,255,.5)) !important; }',
        '  table th { background-color: var(--surface_container_high, var(--surface, #2b2a33)) !important; color: var(--on_surface, #fbfbfe) !important; }',
        '  tbody tr:nth-child(even) { background-color: var(--surface_container_low, var(--surface, #1e1d27)) !important; }',
        '  img, video, canvas, iframe, embed, object, svg { background-color: transparent !important; }',
        '  ::backdrop { background-color: rgba(0,0,0,.7) !important; }',
        '  hr { border-color: var(--outline_variant, rgba(255,255,255,.12)) !important; }',
        '  ::selection { background-color: var(--primary_container, var(--primary, #364765)) !important; color: var(--on_primary_container, var(--on_primary, #fff)) !important; }',
        '  html { scrollbar-color: var(--outline, #42414d) var(--surface, #1c1b22); }',
        '}', ''
    ].join('\n');

    /** Author rules for one host, least specific first; exact/www matches suppress lower tiers. */
    function siteRulesFor(host, websites) {
        const hits = [];
        for (const key of Object.keys(websites)) {
            const score = matchScore(host, key, true);
            if (score > 0 && typeof websites[key] === 'string') hits.push({ key, score, css: websites[key] });
        }
        if (!hits.length) return '';
        const top = Math.max(...hits.map((h) => h.score));
        const floor = top >= 1000 ? 1000 : top >= 950 ? 950 : 0;
        let out = '';
        for (const h of hits.filter((x) => x.score >= floor).sort((a, b) => a.score - b.score || (a.key < b.key ? -1 : 1))) {
            out += '/* dusky:' + h.key + ' */\n' + h.css.slice(0, LIM.SITE_CSS_BYTES) + '\n';
        }
        return out;
    }

    const packRules = (css, scan, hints, provisional) =>
        Object.freeze({ css, hash: hash32(JSON.stringify([css, scan, hints || null])), scan, hints: hints || null, provisional: !!provisional });

    /** @returns {object|null} rules entry for a host (cached across palette revisions) or null => nothing to apply. */
    function resolveRules(host) {
        if (!S.theme || !host) return null;
        const hit = rulesCache.get(host);
        if (hit !== undefined) {
            const fix = domainCache.get(host);
            const ttl = fix?.neg ? LIM.DOMAIN_NEG_TTL_MS : LIM.DOMAIN_TTL_MS;
            if (!S.cfg.forceUnthemedWebsites || hit?.scan === false
                || (fix && now() - fix.at <= ttl) || isSiteDisabled(host)) return hit;
            rulesCache.delete(host);
        }
        let out = null;
        if (!isSiteDisabled(host)) {
            const site = siteRulesFor(host, S.theme.websites);
            if (site) out = packRules(site, false, null, false);
            else if (S.cfg.forceUnthemedWebsites) {
                const fix = domainCache.get(host);
                const ttl = fix?.neg ? LIM.DOMAIN_NEG_TTL_MS : LIM.DOMAIN_TTL_MS;
                if (!fix || now() - fix.at > ttl) {
                    requestDomainFix(host);                                   // authoritative answer re-pushes the tab
                    return packRules(FALLBACK_CSS, true, null, true);         // provisional: deliberately not cached
                }
                out = fix.isDarkSite ? null : packRules(fix.css ? FALLBACK_CSS + '\n' + fix.css : FALLBACK_CSS, true, fix.hints, false);
            }
        }
        rulesCache.set(host, out);
        return out;
    }

    function resolve(host) {
        const rules = resolveRules(host);
        if (!rules || !S.palette) return null;
        return { palette: S.palette, rules, sig: S.palette.hash + '/' + rules.hash + (rules.scan ? '/s' : '') };
    }
    const rulesHashOfSig = (sig) => (typeof sig === 'string' ? sig.split('/')[1] ?? null : null);
    const payloadOf = (res, full) => ({
        palette: res.palette,
        rules: full ? { css: res.rules.css, hash: res.rules.hash } : { hash: res.rules.hash },
        scan: res.rules.scan,
        hints: res.rules.hints
    });

    /* ── 11. Delivery (I5, I6) ───────────────────────────────────────────── */
    function claimTabEpoch(tabId) {
        const epoch = ++tabEpochSeq;

        tabEpoch.set(
            tabId,
            epoch
        );

        return epoch;
    }

    function commitTabSig(
        tabId,
        epoch,
        sig
    ) {
        // A later delivery/pull superseded this async operation.
        if (
            tabEpoch.get(tabId) !== epoch
        ) {
            return false;
        }

        if (sig === undefined) {
            lastSig.delete(tabId);
            tabEpoch.delete(tabId);
            return true;
        }

        lastSig.set(
            tabId,
            sig
        );

        if (
            lastSig.size >
            LIM.TRACKED_TABS
        ) {
            const oldest =
                lastSig.keys().next().value;

            lastSig.delete(oldest);
            tabEpoch.delete(oldest);
        }

        return true;
    }

    function noteTabSig(
        tabId,
        sig
    ) {
        commitTabSig(
            tabId,
            claimTabEpoch(tabId),
            sig
        );
    }

    function queueTab(
        tabId,
        msg,
        sig
    ) {
        const epoch =
            claimTabEpoch(tabId);

        const s =
            slots.get(tabId);

        if (s) {
            /*
             * Latest wins inside the slot.
             *
             * But a palette-only payload must not displace the queued full
             * stylesheet when both refer to the same rule revision.
             */
            const a = s.msg.data;
            const b = msg.data;

            if (
                a &&
                b &&
                !b.rules.css &&
                a.rules.css &&
                a.rules.hash === b.rules.hash
            ) {
                b.rules = a.rules;
            }

            s.msg = msg;
            s.sig = sig;
            s.epoch = epoch;

            return;
        }

        const e = {
            msg,
            sig,
            epoch,
            timer: 0
        };

        e.timer = setTimeout(
            async () => {
                slots.delete(tabId);

                stats.tabSends++;

                if (
                    e.msg.data &&
                    !e.msg.data.rules.css
                ) {
                    stats.paletteOnly++;
                }

                try {
                    await browser.tabs.sendMessage(
                        tabId,
                        e.msg
                    );

                    commitTabSig(
                        tabId,
                        e.epoch,
                        e.sig
                    );
                } catch (error) {
                    /*
                     * Only invalidate if this is still the newest delivery
                     * attempt for the tab. An older Promise must never erase
                     * state established by a later navigation/pull/send.
                     */
                    commitTabSig(
                        tabId,
                        e.epoch,
                        undefined
                    );

                    swallow(error);
                }
            },
            T.TAB_SLOT_MS
        );

        slots.set(
            tabId,
            e
        );
    }

    function pushTab(tabId, url, force) {
        if (!isInjectable(url)) return;
        const host = hostOf(url);
        const res = S.enabled && S.cfg.webThemeEnabled ? resolve(host) : null;
        const sig = res ? res.sig : 'x';
        const prev = slots.get(tabId)?.sig ?? lastSig.get(tabId);
        if (!force && prev === sig) { stats.tabSkips++; return; }
        if (!res) {
            if (prev !== undefined && prev !== 'x') { stats.rollbacks++; queueTab(tabId, { type: 'MATUGEN_ROLLBACK' }, 'x'); dropPaint(host); }
            return;                                        // never told anything → nothing to roll back
        }
        const full = force || rulesHashOfSig(prev) !== res.rules.hash;
        queueTab(tabId, { type: 'MATUGEN_UPDATE', data: payloadOf(res, full) }, sig);
        if (!res.rules.provisional) markPaint(host, res.rules);
    }

    async function broadcastActive(force) {
        if (!S.theme) return;
        let tabs;
        try { tabs = await browser.tabs.query({ active: true, discarded: false }); } catch (e) { warn(e); return; }
        for (const t of tabs) pushTab(t.id, t.url, force);
    }

    /** Non-eco: background tabs get the settled palette in batches; their content scripts defer the restyle until visible. */
    async function trickle() {
        S.wantTrickle = false;
        clearT('trickle');
        let tabs;
        try { tabs = await browser.tabs.query({ active: false, discarded: false }); } catch { return; }
        let i = 0;
        const step = () => {
            S.tm.trickle = 0;
            const end = Math.min(i + T.TRICKLE_BATCH, tabs.length);
            for (; i < end; i++) pushTab(tabs[i].id, tabs[i].url, false);
            if (i < tabs.length) S.tm.trickle = setTimeout(step, T.TRICKLE_MS);
        };
        step();
    }

    /** Transition only: tell every tab we ever themed to drop the stylesheet. */
    async function rollbackAll() {
        let tabs;
        try { tabs = await browser.tabs.query({ discarded: false }); } catch { return; }
        for (const t of tabs) {
            const prev = slots.get(t.id)?.sig ?? lastSig.get(t.id);
            if (prev !== undefined && prev !== 'x' && isInjectable(t.url)) { stats.rollbacks++; queueTab(t.id, { type: 'MATUGEN_ROLLBACK' }, 'x'); }
        }
    }

    function dropTab(tabId) {
        const s =
            slots.get(tabId);

        if (s) {
            clearTimeout(s.timer);
            slots.delete(tabId);
        }

        lastSig.delete(tabId);
        tabEpoch.delete(tabId);
    }

    /* ── 12. Persistence: session per revision, disk after settle ────────── */
    function armSettle() { clearT('settle'); S.tm.settle = setTimeout(onSettle, T.SETTLE_MS); }

    async function onSettle() {
        S.tm.settle = 0;
        stats.settles++;

        if (
            S.wantTrickle &&
            S.enabled &&
            S.cfg.webThemeEnabled
        ) {
            trickle();
        }

        await flushPaint();
        await persistSeed();
        await persistMeta();

        if (DEBUG) {
            log(
                'settled',
                JSON.stringify({
                    ...stats,
                    themeCostEwmaMs:
                        Math.round(S.themeCost),

                    rulesCache:
                        rulesCache.size,

                    trackedTabs:
                        lastSig.size
                })
            );
        }
    }

    /** storage.session: the 4 KB colours record on every change, the rule map only when the host sent one. */
    function persistWarm(
        sitesChanged
    ) {
        const th = S.theme;

        const w = {
            colors: {
                colors: th.colors,
                colorsRev: th.colorsRev,

                disabledSites:
                    th.disabled,

                webThemeEnabled:
                    S.cfg.webThemeEnabled,

                forceUnthemedWebsites:
                    S.cfg.forceUnthemedWebsites,

                status:
                    th.status,

                timestamp:
                    th.at
            }
        };

        if (sitesChanged) {
            w.sites = {
                websites:
                    th.websites,

                websitesRev:
                    th.websitesRev
            };
        }

        browser.storage.session
            .set(w)
            .catch(swallow);
    }

    /** storage.local cold-start seed: seed (4 KB) per settle when changed, seedSites only when the rule map changed. */
    async function persistSeed() {
        const th = S.theme;

        if (!th) {
            return;
        }

        const put = {};

        let nextSeedHash =
            seedHash;

        let nextSitesRev =
            seedSitesRev;

        const h = hash32([
            th.colorsRev,
            th.disabledRev,
            S.cfg.webThemeEnabled,
            S.cfg.forceUnthemedWebsites
        ].join('|'));

        if (h !== seedHash) {
            nextSeedHash = h;

            put.seed = {
                colors:
                    th.colors,

                colorsRev:
                    th.colorsRev,

                disabledSites:
                    th.disabled,

                webThemeEnabled:
                    S.cfg.webThemeEnabled,

                forceUnthemedWebsites:
                    S.cfg.forceUnthemedWebsites,

                status:
                    th.status,

                timestamp:
                    th.at
            };
        }

        if (
            th.websitesRev !==
            seedSitesRev
        ) {
            nextSitesRev =
                th.websitesRev;

            put.seedSites = {
                websites:
                    th.websites,

                websitesRev:
                    th.websitesRev
            };
        }

        if (
            !Object.keys(put).length
        ) {
            return;
        }

        try {
            await browser.storage.local.set(
                put
            );

            /*
             * Advance the in-memory "persisted" identities only AFTER
             * storage confirms success. A failed write must be retried
             * at the next settle.
             */
            seedHash =
                nextSeedHash;

            seedSitesRev =
                nextSitesRev;
        } catch (e) {
            swallow(e);
        }
    }

    function persistMeta() {
        const tabs = [];

        for (
            const [id, sig]
            of lastSig
        ) {
            if (sig !== 'x') {
                tabs.push(id);
            }
        }

        return browser.storage.session
            .set({
                meta: {
                    themeHash:
                        S.themeHash,

                    scheme:
                        S.scheme,

                    tabs:
                        tabs.slice(-2048)
                }
            })
            .catch(swallow);
    }

    /** First-paint cache: paint:palette (shared) + paint:<host> (rules, hash-deduped). Written at settle only. */
    const paintHashOf = (host) => paintIndex.find((e) => e[0] === host)?.[1] ?? null;

    function markPaint(host, rules) {
        if (!S.cfg.fastPaint || !host || rules.css.length > LIM.PAINT_ENTRY_BYTES) return;
        if (paintHashOf(host) === rules.hash && !paintDirty.has(host)) return;      // already on disk
        paintDirty.set(host, { rules: { css: rules.css, hash: rules.hash }, scan: rules.scan, hints: rules.hints, at: now() });
    }
    function dropPaint(host) {
        if (!host) return;
        if (paintHashOf(host) !== null) paintDirty.set(host, null); else paintDirty.delete(host);
    }

    function flushPaint() {
        paintWork = paintWork.then(flushPaintNow).catch(swallow);
        return paintWork;
    }

    async function flushPaintNow() {
        if (!S.cfg.fastPaint || !S.enabled || !S.cfg.webThemeEnabled) {
            return;
        }

        const palette =
            S.palette;

        const paletteStale =
            !!palette &&
            palette.hash !== paletteWritten;

        if (
            !paintDirty.size &&
            !paletteStale
        ) {
            return;
        }

        /*
         * Snapshot the work. Do not mutate the authoritative in-memory
         * bookkeeping until storage.local confirms success.
         */
        const pending =
            new Map(paintDirty);

        const nextIndex =
            paintIndex.slice();

        const put = {};
        const del = [];

        for (
            const [host, entry]
            of pending
        ) {
            for (
                let i = nextIndex.length - 1;
                i >= 0;
                i--
            ) {
                if (
                    nextIndex[i][0] === host
                ) {
                    nextIndex.splice(i, 1);
                }
            }

            if (entry === null) {
                del.push(
                    'paint:' + host
                );
            } else {
                put[
                    'paint:' + host
                ] = entry;

                nextIndex.push([
                    host,
                    entry.rules.hash
                ]);
            }
        }

        while (
            nextIndex.length >
            LIM.PAINT_ENTRIES
        ) {
            del.push(
                'paint:' +
                nextIndex.shift()[0]
            );
        }

        if (paletteStale) {
            put['paint:palette'] =
                palette;
        }

        put.paintIndex =
            nextIndex;

        try {
            if (del.length) {
                await browser.storage.local.remove(
                    del
                );
            }

            await browser.storage.local.set(
                put
            );

            /*
             * Delete only snapshot entries that weren't replaced while
             * the asynchronous storage operation was underway.
             */
            for (
                const [host, entry]
                of pending
            ) {
                if (
                    paintDirty.get(host) ===
                    entry
                ) {
                    paintDirty.delete(host);
                }
            }

            paintIndex =
                nextIndex;

            if (paletteStale) {
                paletteWritten =
                    palette.hash;
            }

            stats.paintWrites++;
        } catch (e) {
            swallow(e);
        }
    }

    function clearPaint() {
        // Clear memory now; serialize disk removal after any in-flight flush.
        paintDirty.clear();
        paintWork = paintWork.then(async () => {
            const keys = paintIndex.map((e) => 'paint:' + e[0]).concat(['paint:palette', 'paintIndex']);
            await browser.storage.local.remove(keys);
            paintIndex = []; paletteWritten = null;
        }).catch(swallow);
        return paintWork;
    }

    /* ── 13. Runtime message router (content scripts + console diagnostics) */
    browser.runtime.onMessage.addListener((req, sender) => {
        if (!req || typeof req.type !== 'string' || sender.id !== browser.runtime.id) return false;
        switch (req.type) {
            case 'GET_THEME_DATA': return ready().then(() => themeDataFor(sender, req));
            case 'GET_STATUS': return ready().then(status);
            case 'FORCE_REFRESH':
                return ready().then(() => {
                    rulesCache.clear(); domainCache.clear(); lastSig.clear(); tabEpoch.clear();
                    S.themeHash = null; queueTheme(); broadcastActive(true); armSettle();
                    return { ok: true };
                });
            default: return false;
        }
    });

    function themeDataFor(sender, req) {
        stats.contentPulls++;
        if (!S.enabled || !S.cfg.webThemeEnabled) return { data: null };
        // Resolve against the TOP document so third-party frames inherit the embedding page's rules.
        const url = sender.tab?.url ?? sender.url;
        if (!isInjectable(url)) return { data: null };
        const host = hostOf(url);
        const res = resolve(host);
        const top = !!sender.tab && sender.frameId === 0;
        if (!res) {
            if (top) {
                pushTab(sender.tab.id, url, true);
            }

            return {
                data: null
            };
        }

        if (top) {
            noteTabSig(
                sender.tab.id,
                '?'
            );
        }
        if (!res.rules.provisional) { markPaint(host, res.rules); if (!S.tm.settle) armSettle(); }
        if (req.have && req.have.p === res.palette.hash && req.have.r === res.rules.hash) return { same: true };
        return { data: payloadOf(res, true) };
    }

    function status() {
        return {
            connected: !!S.port && S.ready, enabled: S.enabled, lastError: S.lastError,
            nextAttemptIn: S.port ? 0 : Math.max(0, S.nextAt - now()),
            colorsRev:
                S.theme?.colorsRev ?? null,

            websitesRev:
                S.theme?.websitesRev ?? null,
            lastSync: S.theme?.at ?? null, hostStatus: S.theme?.status ?? null,
            themeHash: S.themeHash, themeCostEwmaMs: Math.round(S.themeCost), scheme: S.scheme,
            config: S.cfg, wire: WIRE, stats: { ...stats }
        };
    }

    async function setEnabled(on) {
        if (S.enabled === on) return;
        S.enabled = on;
        await browser.storage.local
            .set({
                enabled: on
            })
            .catch(swallow);
        if (on) {
            S.attempt = 0; connect();
            if (S.cfg.browserThemeEnabled) queueTheme();
            if (S.cfg.webThemeEnabled) broadcastActive(true);
            armSettle();
        } else {
            clearT('retry'); clearT('trickle'); clearT('settle'); teardown(S.port); outbox.length = 0;
            await rollbackAll();
            await clearPaint();
            await resetTheme();
        }
        await persistMeta();
        browser.action.setTitle({ title: on ? APP : `${APP} (paused)` }).catch(swallow);
    }

    /* ── 14. Event wiring (all synchronous — I1) ─────────────────────────── */
    // A new document's content script registers its own signature through GET_THEME_DATA, so this is only a
    // safety net; the signature compare makes it free. No 'url' in the filter: pushState churn must not wake us.
    browser.tabs.onUpdated.addListener((tabId, change, tab) => {
        ready().then(() => {
            if (change.discarded) { dropTab(tabId); return; }
            if (change.status !== 'complete' || !S.theme) return;
            if (tab.active || !S.cfg.ecoMode) pushTab(tabId, tab.url, false);
        });
    }, { properties: ['status', 'discarded'] });

    browser.tabs.onActivated.addListener((info) => {
        ready().then(async () => {
            if (!S.theme) return;
            try { const t = await browser.tabs.get(info.tabId); pushTab(t.id, t.url, false); } catch { /* closed */ }
        });
    });

    browser.windows.onFocusChanged.addListener((windowId) => {
        if (windowId === browser.windows.WINDOW_ID_NONE) return;
        ready().then(async () => {
            if (!S.theme) return;
            try { const [t] = await browser.tabs.query({ active: true, windowId }); if (t) pushTab(t.id, t.url, false); } catch { /* gone */ }
        });
    });

    browser.tabs.onRemoved.addListener(dropTab);

    let enableWork = Promise.resolve();
    browser.action.onClicked.addListener(() => {
        enableWork = enableWork.then(() => ready()).then(() => setEnabled(!S.enabled)).catch(swallow);
    });

    /** API-surface probe (not a compat shim): touching .addListener on an event Gecko lacks throws during eval. */
    function on(ns, event, handler) {
        const e = ns?.[event];
        if (e && typeof e.addListener === 'function') { e.addListener(handler); return true; }
        warn('event unavailable:', event);
        return false;
    }

    on(browser.permissions, 'onAdded', () => ready().then(() => broadcastActive(true)));
    on(browser.permissions, 'onRemoved', () => ready().then(() => broadcastActive(true)));

    // Someone reset the theme: re-assert ours, but bounded — never live-lock with another dynamic theme.
    const reasserts = [];
    on(browser.theme, 'onUpdated', (info) => {
        ready().then(() => {
            if (!S.enabled || !S.cfg.browserThemeEnabled || !S.theme || !S.themeHash) return;
            if (info?.windowId !== undefined) return;            // per-window override by another add-on
            if (info?.theme?.colors) return;                     // a real theme was applied (ours included): no fight
            const t = now();
            while (reasserts.length && t - reasserts[0] > T.REASSERT_WINDOW_MS) reasserts.shift();
            if (reasserts.length >= T.REASSERT_MAX) { warn('theme re-assert budget exhausted'); return; }
            reasserts.push(t); S.themeHash = null; queueTheme();
        });
    });

    browser.alarms.onAlarm.addListener((alarm) => {
        if (alarm.name !== ALARM) return;
        ready().then(() => {
            // Durable revival: timers die with the event page, alarms do not.
            if (S.enabled && !S.port && now() >= S.nextAt) connect();
            if (paintDirty.size && !S.tm.settle) armSettle();
        });
    });

    async function ensureWatchdog(force) {
        try {
            const period = S.cfg.watchdogMinutes || 0.5;
            const cur = await browser.alarms.get(ALARM);
            if (cur && !force && cur.periodInMinutes === period) return;
            await browser.alarms.create(ALARM, { periodInMinutes: period, delayInMinutes: period });
        } catch (e) { warn('alarm setup failed', e); }
    }

    on(browser.runtime, 'onStartup', () => { ready(); });
    on(browser.runtime, 'onInstalled', () => { ready(); });
    on(
        browser.runtime,
        'onSuspend',
        () => {
            teardown(S.port);
        }
    );

    ready();
})();
