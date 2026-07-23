/* ═══════════════════════════════════════════
   MatugenFox Background — Central State v2.0
   ═══════════════════════════════════════════ */

'use strict';

// ─── Constants ───
const NATIVE_NAME = 'matugenfox';
const RECONNECT_BASE = 2000;
const RECONNECT_MAX = 300000;

// ─── State ───
const state = {
    port: null,
    shouldConnect: true,
    isConnecting: false,
    reconnectTimer: null,
    reconnectDelay: RECONNECT_BASE,
    lastThemeData: null,
    isApplied: false,
    config: { ...DEFAULT_CONFIG },
    hasPromptedPaths: false,
    configWritePromise: Promise.resolve(),
};

const broadcastQueue = new Map();

// ─── Utilities ───
function notifyUI(msg) {
    browser.runtime.sendMessage(msg).catch(e => console.warn('MatugenFox:', e));
}

// ─── Native Host ───
function connectNative() {
    if (!state.shouldConnect || state.isConnecting || state.port) return;
    state.isConnecting = true;
    try {
        const port = browser.runtime.connectNative(NATIVE_NAME);
        state.port = port;

        port.onMessage.addListener(handleHostMessage);
        port.onDisconnect.addListener(handleHostDisconnect);

        safePostMessage({ type: 'SET_CONFIG', config: state.config });
        safePostMessage({ type: 'FETCH_NOW' });

        notifyUI({ type: 'HOST_STATUS', connected: true });
    } catch (err) {
        console.error('MatugenFox: connectNative error:', err);
        scheduleReconnect();
    } finally {
        state.isConnecting = false;
    }
}

function safePostMessage(msg) {
    if (!state.port) return false;
    try {
        state.port.postMessage(msg);
        return true;
    } catch (e) {
        console.warn('MatugenFox: postMessage failed:', e);
        state.port = null;
        scheduleReconnect();
        return false;
    }
}

function handleHostDisconnect(p) {
    const err = p.error?.message || 'unknown';
    console.error('MatugenFox: host disconnected:', err);
    state.port = null;
    notifyUI({ type: 'HOST_STATUS', connected: false, error: err, manuallyStopped: !state.shouldConnect });
    if (state.shouldConnect) scheduleReconnect();
}

function scheduleReconnect() {
    if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(() => {
        state.reconnectTimer = null;
        connectNative();
    }, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, RECONNECT_MAX);
}

function disconnectNative() {
    state.shouldConnect = false;
    if (state.reconnectTimer) { clearTimeout(state.reconnectTimer); state.reconnectTimer = null; }
    if (state.port) { try { state.port.disconnect(); } catch { } state.port = null; }
    broadcastRollback();
    resetBrowserTheme();
    state.isApplied = false;
}

// ─── Theme Resolution ───
function resolveThemeData() {
    if (!state.lastThemeData) return null;
    return {
        ...state.lastThemeData,
        colors: { ...state.lastThemeData.colors }
    };
}

// ─── Palette & Browser Theme ───
function buildPalette(colors) {
    const tmpl = state.config.paletteTemplate || DEFAULT_CONFIG.paletteTemplate;
    const palette = {};
    for (const [role, varName] of Object.entries(tmpl)) {
        palette[role] = colors[varName] || null;
    }
    return palette;
}

function buildBrowserThemeColors(colors) {
    const palette = buildPalette(colors);
    const tmpl = state.config.browserTemplate || DEFAULT_CONFIG.browserTemplate;
    const out = {};
    for (const [element, role] of Object.entries(tmpl)) {
        const c = palette[role];
        if (c) out[element] = c;
    }
    return out;
}

function isColorLight(hex) {
    if (!hex) return false;
    let c = hex.replace('#', '');
    if (c.length === 3) c = c.split('').map(x => x + x).join('');
    if (c.length !== 6 && c.length !== 8) return false;
    const r = parseInt(c.slice(0, 2), 16);
    const g = parseInt(c.slice(2, 4), 16);
    const b = parseInt(c.slice(4, 6), 16);
    return ((0.299 * r + 0.587 * g + 0.114 * b) / 255) > 0.5;
}

function applyBrowserTheme(colors) {
    if (!colors || !state.config.browserThemeEnabled) return;
    const themeColors = buildBrowserThemeColors(colors);
    if (!Object.keys(themeColors).length) return;
    const scheme = isColorLight(themeColors.frame) ? 'light' : 'dark';
    browser.theme.update({
        colors: themeColors,
        properties: { color_scheme: scheme, content_color_scheme: scheme },
    }).catch(e => console.warn('MatugenFox:', e));
    state.isApplied = true;
}

function resetBrowserTheme() {
    browser.theme.reset().catch(e => console.warn('MatugenFox:', e));
    state.isApplied = false;
}



// ─── Tab Broadcasting ───
function filterWebsiteCss(url, websites) {
    if (!url || !websites) return '';
    try {
        const hostname = new URL(url).hostname;
        let css = '';
        for (const [domain, siteCss] of Object.entries(websites)) {
            if (hostname === domain || hostname.endsWith('.' + domain)) {
                css += `/* ${domain} */\n${siteCss}\n`;
            }
        }
        return css;
    } catch { return ''; }
}

function broadcastToTabs(force = false) {
    const data = resolveThemeData();
    if (!data?.colors || !Object.keys(data.colors).length) return;
    const isEco = state.config.ecoMode;

    browser.tabs.query({}).then(tabs => {
        if (isEco) {
            const activeByWindow = {};
            for (const t of tabs) {
                if (t.active && !t.discarded) activeByWindow[t.windowId] = t;
            }
            for (const t of Object.values(activeByWindow)) {
                sendToTab(t.id, data, t.url, force);
            }
        } else {
            const targets = tabs.filter(t => t.status === 'complete' && !t.discarded);
            targets.forEach(tab => sendToTab(tab.id, data, tab.url, force));
        }
    }).catch(e => console.warn('MatugenFox:', e));
}

function sendToTab(tabId, data, url, force = false) {
    if (!url) return;
    // Only theme sites that have a matching CSS template
    if (!state.config.webThemeEnabled) return;
    const siteCss = filterWebsiteCss(url, data.websites);
    if (!siteCss) return;

    if (broadcastQueue.has(tabId)) clearTimeout(broadcastQueue.get(tabId));
    broadcastQueue.set(tabId, setTimeout(() => {
        broadcastQueue.delete(tabId);
        browser.tabs.sendMessage(tabId, {
            type: 'MATUGEN_UPDATE',
            data: {
                colors: data.colors,
                websiteCss: siteCss,
                timestamp: data.timestamp,
                force,
            },
        }).catch(e => console.warn('MatugenFox:', e));
    }, 16));
}

function broadcastRollback() {
    browser.tabs.query({}).then(tabs => {
        for (const t of tabs) {
            browser.tabs.sendMessage(t.id, { type: 'MATUGEN_ROLLBACK' }).catch(e => console.warn('MatugenFox:', e));
        }
    }).catch(e => console.warn('MatugenFox:', e));
}

// ─── Config Management ───
function loadConfig() {
    browser.storage.local.get(['config', 'themeData']).then(res => {
        if (res.config) state.config = mergeConfig(res.config);
        if (res.themeData) state.lastThemeData = res.themeData;
        connectNative();
    }).catch(err => console.error('MatugenFox: loadConfig error:', err));
}

function saveConfig(partial = null) {
    if (partial) Object.assign(state.config, partial);
    state.configWritePromise = state.configWritePromise
        .then(() => browser.storage.local.set({ config: state.config }))
        .then(() => {
            safePostMessage({ type: 'SET_CONFIG', config: state.config });
        })
        .catch(err => console.error('MatugenFox: saveConfig error:', err));
    return state.configWritePromise;
}

// ─── Host Message Handler ───
function handleHostMessage(msg) {
    state.reconnectDelay = RECONNECT_BASE;
    switch (msg.type) {
        case 'MATUGEN_UPDATE': {
            if (!msg.data?.colors) return;
            state.lastThemeData = msg.data;
            browser.storage.local.set({ themeData: msg.data }).catch(e => console.warn('MatugenFox: storage error:', e));

            broadcastToTabs();
            if (state.config.browserThemeEnabled) applyBrowserTheme(msg.data.colors);
            notifyUI({ type: 'THEME_APPLIED', colors: msg.data.colors });
            break;
        }
        case 'STORED_CONFIG': {
            if (msg.config) {
                const prev = JSON.stringify(state.config);
                state.config = mergeConfig({ ...state.config, ...msg.config });
                if (prev !== JSON.stringify(state.config)) {
                    browser.storage.local.set({ config: state.config });
                    notifyUI({ type: 'CONFIG_RECOVERED', config: state.config });
                }
            }
            break;
        }
        case 'SAVE_CONFIG_SUCCESS':
            break;
        default:
            notifyUI({ type: 'HOST_RESPONSE', data: msg });
    }
}

// ─── Message Router ───
browser.runtime.onMessage.addListener((req, sender) => {
    switch (req.type) {
        case 'UPDATE_CONFIG': {
            const oldBrowser = state.config.browserThemeEnabled;
            const oldWeb = state.config.webThemeEnabled;
            state.config = mergeConfig({ ...state.config, ...req.partialUpdate });
            return saveConfig().then(() => {
                const data = resolveThemeData();
                if ('browserThemeEnabled' in req.partialUpdate && oldBrowser !== state.config.browserThemeEnabled) {
                    state.config.browserThemeEnabled ? applyBrowserTheme(data?.colors) : resetBrowserTheme();
                }
                if ('webThemeEnabled' in req.partialUpdate && oldWeb !== state.config.webThemeEnabled) {
                    if (state.config.webThemeEnabled) {
                        broadcastToTabs(true);
                    } else {
                        broadcastRollback();
                    }
                }
                if ('paletteTemplate' in req.partialUpdate || 'browserTemplate' in req.partialUpdate) {
                    if (state.config.browserThemeEnabled) applyBrowserTheme(data?.colors);
                }
                return { ok: true };
            });
        }
        case 'GET_THEME_DATA': {
            // Strict: only return data if web theming is on AND site has a template
            if (!state.config.webThemeEnabled) return Promise.resolve(null);
            const url = sender.tab?.url || sender.url;
            const data = resolveThemeData();
            if (!data) {
                return browser.storage.local.get('themeData').then(res => {
                    if (!res.themeData) return null;
                    const siteCss = filterWebsiteCss(url, res.themeData.websites);
                    if (!siteCss) return null;
                    return {
                        colors: res.themeData.colors,
                        websiteCss: siteCss,
                        timestamp: res.themeData.timestamp,
                        status: res.themeData.status,
                    };
                });
            }
            const siteCss = filterWebsiteCss(url, data.websites);
            if (!siteCss) return Promise.resolve(null);
            return Promise.resolve({
                colors: data.colors,
                websiteCss: siteCss,
                timestamp: data.timestamp,
                status: data.status,
            });
        }
        case 'GET_STATUS':
            return Promise.resolve({
                connected: !!state.port,
                manuallyStopped: !state.shouldConnect,
                lastSyncTime: state.lastThemeData?.timestamp || null,
                isApplied: state.isApplied,
            });
        case 'GET_PALETTE': {
            const colors = resolveThemeData()?.colors;
            return Promise.resolve({ palette: buildPalette(colors), colors });
        }

        case 'GET_PROFILE_PATHS':
        case 'WRITE_USER_CHROME':
        case 'WRITE_USER_CONTENT':
        case 'SET_FONT_SIZE': {
            if (!sender.url || !sender.url.includes(browser.runtime.id)) {
                console.warn('MatugenFox: Rejected native host command from untrusted sender:', sender);
                return Promise.resolve({ ok: false, error: 'Unauthorized' });
            }
            safePostMessage(req);
            return Promise.resolve({ ok: !!state.port });
        }
        default:
            return false;
    }
});

// ─── Tab Events ───
browser.tabs.onActivated.addListener((activeInfo) => {
    if (state.config.ecoMode && state.lastThemeData) {
        browser.tabs.get(activeInfo.tabId).then(tab => {
            sendToTab(tab.id, resolveThemeData(), tab.url);
        }).catch(e => console.warn('MatugenFox:', e));
    }
});

browser.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'complete' && tab.active && state.config.ecoMode && state.lastThemeData) {
        sendToTab(tabId, resolveThemeData(), tab.url);
    }
});



// ─── Tab Cleanup ───
browser.tabs.onRemoved.addListener(tabId => {
    if (broadcastQueue.has(tabId)) {
        clearTimeout(broadcastQueue.get(tabId));
        broadcastQueue.delete(tabId);
    }
});

// ─── Init ───
loadConfig();