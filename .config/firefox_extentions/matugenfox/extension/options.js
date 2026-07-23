/* ═══════════════════════════════════════════
   MatugenFox Options Page — v2.0
   ═══════════════════════════════════════════ */

'use strict';

let config = {};

// ─── Navigation ───
document.querySelectorAll('.sidebar-link').forEach(btn => {
    btn.addEventListener('click', () => {
        const panel = document.getElementById('panel-' + btn.dataset.panel);
        if (!panel) return;
        document.querySelectorAll('.sidebar-link').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.options-panel').forEach(p => p.classList.remove('active'));
        btn.classList.add('active');
        panel.classList.add('active');
    });
});

// ─── Self-Theming ───
const THEME_MAP = {
    '--primary': '--mg-accent',
    '--on_primary': '--mg-on-accent',
    '--background': '--mg-bg-0',
    '--surface': '--mg-bg-1',
    '--surface_container': '--mg-bg-2',
    '--surface_container_high': '--mg-bg-3',
    '--on_surface': '--mg-text-0',
    '--on_surface_variant': '--mg-text-1',
    '--outline': '--mg-border',
    '--outline_variant': '--mg-border',
    '--error': '--mg-error',
    '--secondary': '--mg-accent',
};

function applySelfTheme(colors) {
    if (!colors) return;
    const root = document.documentElement;
    let accentSet = false;
    for (const [src, target] of Object.entries(THEME_MAP)) {
        if (colors[src]) {
            root.style.setProperty(target, colors[src]);
            if (target === '--mg-accent') accentSet = true;
        }
    }
    if (!accentSet) {
        for (const [key, value] of Object.entries(colors)) {
            if (key.includes('primary') && !key.includes('on') && !key.includes('container') && !key.includes('inverse')) {
                root.style.setProperty('--mg-accent', value);
                break;
            }
        }
    }
    const accent = root.style.getPropertyValue('--mg-accent');
    if (accent && accent.startsWith('#')) {
        let c = accent.replace('#', '');
        if (c.length === 3) c = c.split('').map(x => x + x).join('');
        const rgb = parseInt(c.slice(0, 2), 16) + ',' + parseInt(c.slice(2, 4), 16) + ',' + parseInt(c.slice(4, 6), 16);
        root.style.setProperty('--mg-accent-dim', `rgba(${rgb}, 0.15)`);
        root.style.setProperty('--mg-accent-glow', `rgba(${rgb}, 0.4)`);
    }
}

// ─── Init ───
async function init() {
    const [stored, themeDataRes] = await Promise.all([
        browser.storage.local.get('config'),
        browser.storage.local.get('themeData'),
    ]);

    const themeData = themeDataRes.themeData;
    config = mergeConfig(stored.config);
    if (themeData?.colors) applySelfTheme(themeData.colors);

    const versionEl = document.querySelector('.sidebar-footer');
    if (versionEl && versionEl.firstChild) {
        versionEl.firstChild.textContent = 'v' + browser.runtime.getManifest().version + ' · ';
    }

    // General
    document.getElementById('opt-eco').checked = config.ecoMode || false;
    document.getElementById('opt-browser-theme-general').checked = config.browserThemeEnabled !== false;
    document.getElementById('opt-web-theme').checked = config.webThemeEnabled || false;

    // Paths
    const defaultColors = '~/.config/matugen/generated/firefox_websites.css';
    const defaultDirs = '~/.config/dusky_sites';
    document.getElementById('opt-colors-path').value = (config.colorsPath && config.colorsPath !== defaultColors) ? config.colorsPath : '';
    document.getElementById('opt-websites-dir').value = (config.websitesDir && config.websitesDir !== defaultDirs) ? config.websitesDir : '';
    const warningEl = document.getElementById('paths-warning-group');
    if (warningEl) warningEl.hidden = !(themeData?.status?.some(s => s.includes('not found')));

    // Browser Theme
    renderPaletteTemplateForm(themeData?.colors);
    renderBrowserTemplateForm();

    // DuckDuckGo
    document.getElementById('opt-duckduckgo').checked = config.duckduckgoEnabled || false;

    // userChrome
    document.getElementById('opt-userchrome').checked = config.userChromeEnabled || false;
    document.getElementById('opt-usercontent').checked = config.userContentEnabled || false;
    document.getElementById('opt-font-size').value = config.fontSize || 13;
    loadProfilePaths();

    browser.runtime.sendMessage({ type: 'GET_STATUS' }).then(status => {
        const el = document.getElementById('host-status');
        if (el && status) {
            if (status.connected) {
                el.textContent = 'Connected';
                el.style.color = 'var(--mg-success)';
            } else {
                el.textContent = 'Disconnected';
                el.style.color = 'var(--mg-error)';
            }
        }
    }).catch(() => {});
}
init();

// ─── General ───
document.getElementById('opt-eco').addEventListener('change', e => {
    sendUpdate({ ecoMode: e.target.checked });
});

function setBrowserTheme(enabled) {
    sendUpdate({ browserThemeEnabled: enabled });
    document.getElementById('opt-browser-theme-general').checked = enabled;
}
document.getElementById('opt-browser-theme-general').addEventListener('change', e => setBrowserTheme(e.target.checked));
document.getElementById('opt-web-theme').addEventListener('change', e => {
    sendUpdate({ webThemeEnabled: e.target.checked });
});

function savePaths() {
    const update = {
        colorsPath: document.getElementById('opt-colors-path').value.trim() || '~/.config/matugen/generated/firefox_websites.css',
        websitesDir: document.getElementById('opt-websites-dir').value.trim() || '~/.config/dusky_sites',
    };
    sendUpdate(update).then(() => {
        const el1 = document.getElementById('opt-colors-path');
        const el2 = document.getElementById('opt-websites-dir');
        const origBg = el1.style.background;
        el1.style.background = 'var(--mg-accent-dim)';
        el2.style.background = 'var(--mg-accent-dim)';
        setTimeout(() => {
            el1.style.background = origBg;
            el2.style.background = origBg;
        }, 500);
    });
}
document.getElementById('opt-colors-path').addEventListener('blur', savePaths);
document.getElementById('opt-websites-dir').addEventListener('blur', savePaths);

// ─── Browser Theme ───
const ROLES = [
    { key: 'background', label: 'Background' },
    { key: 'backgroundLight', label: 'Background Light' },
    { key: 'backgroundExtra', label: 'Background Extra' },
    { key: 'accentPrimary', label: 'Accent Primary' },
    { key: 'accentSecondary', label: 'Accent Secondary' },
    { key: 'text', label: 'Text' },
    { key: 'textFocus', label: 'Text Focus' },
];

const CHROME_ELEMENTS = [
    { key: 'toolbar', label: 'Toolbar' },
    { key: 'tab_selected', label: 'Active Tab' },
    { key: 'tab_line', label: 'Active Tab Line' },
    { key: 'toolbar_field', label: 'URL Bar' },
    { key: 'toolbar_field_focus', label: 'URL Bar (Focus)' },
    { key: 'popup', label: 'Menus & Popups' },
    { key: 'sidebar', label: 'Sidebar' },
];

function renderPaletteTemplateForm(colorsData) {
    const container = document.getElementById('palette-template-form');
    if (!container) return;
    container.replaceChildren();
    const tmpl = config.paletteTemplate || {};
    const colorKeys = colorsData ? Object.keys(colorsData).filter(k => !k.endsWith('_rgb')) : [];

    ROLES.forEach(role => {
        const row = document.createElement('div');
        row.className = 'template-row';

        const label = document.createElement('div');
        label.className = 'template-label';
        label.textContent = role.label;

        const select = document.createElement('select');
        select.className = 'mg-select';
        select.dataset.role = role.key;

        const val = tmpl[role.key] || '';
        if (colorKeys.length === 0) {
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = val || 'Unset';
            select.appendChild(opt);
        } else {
            colorKeys.forEach(k => {
                const opt = document.createElement('option');
                opt.value = k;
                opt.textContent = k;
                select.appendChild(opt);
            });
            if (val && !colorKeys.includes(val)) {
                const opt = document.createElement('option');
                opt.value = val;
                opt.textContent = val + ' (missing)';
                select.appendChild(opt);
            }
        }
        select.value = val;

        select.addEventListener('change', e => {
            if (!config.paletteTemplate) config.paletteTemplate = {};
            config.paletteTemplate[e.target.dataset.role] = e.target.value;
            sendUpdate({ paletteTemplate: config.paletteTemplate });
        });

        row.appendChild(label);
        row.appendChild(select);
        container.appendChild(row);
    });
}

function renderBrowserTemplateForm() {
    const container = document.getElementById('browser-template-form');
    if (!container) return;
    container.replaceChildren();
    const tmpl = config.browserTemplate || {};

    CHROME_ELEMENTS.forEach(el => {
        const row = document.createElement('div');
        row.className = 'template-row';

        const label = document.createElement('div');
        label.className = 'template-label';
        label.textContent = el.label;

        const select = document.createElement('select');
        select.className = 'mg-select';
        select.dataset.element = el.key;

        ROLES.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.key;
            opt.textContent = r.label;
            select.appendChild(opt);
        });
        select.value = tmpl[el.key] || 'background';

        select.addEventListener('change', e => {
            if (!config.browserTemplate) config.browserTemplate = {};
            config.browserTemplate[e.target.dataset.element] = e.target.value;
            sendUpdate({ browserTemplate: config.browserTemplate });
        });

        row.appendChild(label);
        row.appendChild(select);
        container.appendChild(row);
    });
}



// ─── DuckDuckGo ───
document.getElementById('opt-duckduckgo').addEventListener('change', e => {
    sendUpdate({ duckduckgoEnabled: e.target.checked });
});

// ─── userChrome ───
let pathTimeout = null;
function loadProfilePaths() {
    if (pathTimeout) clearTimeout(pathTimeout);
    pathTimeout = setTimeout(() => {
        const el = document.getElementById('profile-path-info');
        if (el && el.textContent.includes('Checking')) {
            el.textContent = 'Could not reach native host. Paths not loaded.';
        }
    }, 5000);
    browser.runtime.sendMessage({ type: 'GET_PROFILE_PATHS' });
}

document.getElementById('opt-userchrome').addEventListener('change', e => {
    sendUpdate({ userChromeEnabled: e.target.checked });
    browser.runtime.sendMessage({ type: 'WRITE_USER_CHROME', enabled: e.target.checked, fontSize: config.fontSize || 13 });
});

document.getElementById('opt-usercontent').addEventListener('change', e => {
    sendUpdate({ userContentEnabled: e.target.checked });
    browser.runtime.sendMessage({ type: 'WRITE_USER_CONTENT', enabled: e.target.checked, fontSize: config.fontSize || 13 });
});

document.getElementById('opt-font-size').addEventListener('change', e => {
    let size = parseInt(e.target.value);
    if (isNaN(size)) size = 13;
    size = Math.max(9, Math.min(24, size));
    e.target.value = size;
    sendUpdate({ fontSize: size });
    if (config.userChromeEnabled) {
        browser.runtime.sendMessage({ type: 'SET_FONT_SIZE', fontSize: size });
    }
});

// ─── Background Message Listener ───
browser.runtime.onMessage.addListener(msg => {
    if (msg.type === 'MATUGEN_UPDATE' && msg.data?.colors) {
        applySelfTheme(msg.data.colors);
        const warningEl = document.getElementById('paths-warning-group');
        if (warningEl) warningEl.hidden = !(msg.data.status?.some(s => s.includes('not found')));
        renderPaletteTemplateForm(msg.data.colors);
    } else if (msg.type === 'CONFIG_RECOVERED') {
        config = msg.config;
        init();
    } else if (msg.type === 'HOST_STATUS') {
        const el = document.getElementById('host-status');
        if (el) {
            if (msg.connected) {
                el.textContent = 'Connected';
                el.style.color = 'var(--mg-success)';
            } else {
                el.textContent = 'Disconnected';
                el.style.color = 'var(--mg-error)';
            }
        }
    } else if (msg.type === 'HOST_RESPONSE') {
        const data = msg.data;
        if (data.type === 'PROFILE_PATHS') {
            if (pathTimeout) clearTimeout(pathTimeout);
            const el = document.getElementById('profile-path-info');
            if (el) {
                el.textContent = data.autoChrome
                    ? 'Auto-detected: ' + data.autoChrome
                    : 'Could not auto-detect profile path.';
            }
        }
    }
});

// ─── Helpers ───
function sendUpdate(partialUpdate) {
    Object.assign(config, partialUpdate);
    return browser.runtime.sendMessage({ type: 'UPDATE_CONFIG', partialUpdate });
}
