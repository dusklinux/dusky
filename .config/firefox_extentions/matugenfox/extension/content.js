/* ═══════════════════════════════════════════
   MatugenFox Content Script v2.0
   ═══════════════════════════════════════════ */

'use strict';

// ─── State ───
let styleEl = null;
let lastHash = null;
let observer = null;

// Reject CSS values that could trigger network requests or code execution
const UNSAFE_CSS_VALUE = /url\s*\(|expression\s*\(|@import|-moz-binding/i;

// ─── Theme Application ───
function applyTheme(data, force = false) {
    // Strict: never theme a site without a matching template
    if (!data?.colors || !data?.websiteCss) {
        removeTheme();
        return;
    }

    const cssContent = data.websiteCss || '';
    const hash = data.timestamp + '|' + cssContent.length + '|' + cssContent.slice(-32);
    if (!force && hash === lastHash) return;
    lastHash = hash;

    let css = ':root {\n';
    for (const [k, v] of Object.entries(data.colors)) {
        if (/^--[\w-]+$/.test(k) && typeof v === 'string' && !/[;{}]/.test(v) && !UNSAFE_CSS_VALUE.test(v)) {
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

    startObserver();
}

function removeTheme() {
    stopObserver();
    if (styleEl) { styleEl.remove(); styleEl = null; }
    lastHash = null;
}

// ─── Persistence Observer ───
// Only active when a theme is applied; watches head only (not full subtree)
function startObserver() {
    if (observer) return;
    observer = new MutationObserver(() => {
        if (styleEl && !styleEl.parentNode) {
            const target = document.head || document.documentElement;
            if (target) target.appendChild(styleEl);
        }
    });
    const target = document.head || document.documentElement;
    if (target) observer.observe(target, { childList: true });
}

function stopObserver() {
    if (observer) {
        observer.disconnect();
        observer = null;
    }
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