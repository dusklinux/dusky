/* ═══════════════════════════════════════════
   MatugenFox Content Script v2.0
   ═══════════════════════════════════════════ */

'use strict';

// ─── State ───
let styleEl = null;
let lastHash = null;

// ─── Theme Application ───
function applyTheme(data, force = false) {
    // Strict: never theme a site without a matching template
    if (!data?.colors || !data?.websiteCss) {
        removeTheme();
        return;
    }

    const hash = data.timestamp + '|' + (data.websiteCss ? data.websiteCss.length : 0);
    if (!force && hash === lastHash) return;
    lastHash = hash;

    let css = ':root {\n';
    for (const [k, v] of Object.entries(data.colors)) {
        if (/^--[\w-]+$/.test(k) && typeof v === 'string' && !/[;{}]/.test(v)) {
            css += `  ${k}: ${v} !important;\n`;
        }
    }
    css += '}\n';
    if (data.websiteCss) css += data.websiteCss;

    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'mf-theme';
    }
    styleEl.textContent = css;

    const apply = () => {
        if (!styleEl.parentNode) {
            if (document.head) document.head.appendChild(styleEl);
            else document.documentElement.appendChild(styleEl);
        }
    };

    if (document.documentElement) apply();
    else requestAnimationFrame(apply);
}

function removeTheme() {
    if (styleEl) { styleEl.remove(); styleEl = null; }
    lastHash = null;
}

// ─── Init ───
function initTheme(retries = 3) {
    browser.runtime.sendMessage({ type: 'GET_STATUS' }).then(status => {
        if (status?.manuallyStopped) {
            removeTheme();
        } else {
            browser.runtime.sendMessage({ type: 'GET_THEME_DATA' }).then(data => {
                if (data) applyTheme(data, true);
            }).catch(() => { });
        }
    }).catch(() => {
        if (retries > 0) setTimeout(() => initTheme(retries - 1), 800);
    });
}
initTheme();

// ─── Message Listener ───
browser.runtime.onMessage.addListener((msg, sender) => {
    if (sender.id !== browser.runtime.id) return;
    if (msg.type === 'MATUGEN_UPDATE') {
        applyTheme(msg.data, msg.data?.force);
    } else if (msg.type === 'MATUGEN_ROLLBACK') {
        removeTheme();
    }
});

// ─── Persistence Observer ───
const observer = new MutationObserver(() => {
    if (styleEl && !document.getElementById('mf-theme')) {
        if (document.head) document.head.appendChild(styleEl);
        else document.documentElement.appendChild(styleEl);
    }
});
if (document.documentElement) observer.observe(document.documentElement, { childList: true, subtree: true });