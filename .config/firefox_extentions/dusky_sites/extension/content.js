/* =============================================================================
 * Dusky Sites — Content Runtime v6.2.2
 * Firefox 158+ · document_start · every frame (all_frames + about:blank)
 *
 * PER-REVISION COST MODEL — the only thing that matters under a matugen burst
 *   palette  ~100 × root.style.setProperty('--x', v, 'important'), diffed against the previous
 *            map. Descendants consuming inherited custom properties can still require cascade/style
 *            recomputation, but palette changes do NOT rebuild or reparse the much larger Dusky
 *            site-rule stylesheet.
 *   rules    untouched. The constructed sheet (style-element fallback) is replaced only when
 *            ITS hash changes: site file edited, host switched, domain fix arrived.
 *   pacing   one flush per animation frame, further spaced by 3 × the measured frame cost;
 *            hidden documents flush when shown (rAF semantics).
 *   first    synchronous, from paint:palette + paint:<host> at document_start.
 *   hashing  none on the page thread — hashes travel with the payload.
 * ===========================================================================*/

'use strict';

(() => {
    if (globalThis.__duskySites6) return;          // Xray expando: invisible to the page
    globalThis.__duskySites6 = true;

    const XHTML = 'http://www.w3.org/1999/xhtml';
    const STYLE_ID = 'dusky-sites-theme';
    const MAX_FIGHTS = 24;
    const COST_X = 3;                              // min gap between flushes = 3 × measured frame cost
    const GAP_MAX_MS = 1000;
    const IS_TOP = (() => { try { return window.top === window; } catch { return false; } })();

    /* ── 0. State ────────────────────────────────────────────────────────── */
    let pending = null;        // latest payload awaiting flush
    let raf = 0, gapTimer = 0, lastFlushAt = -1e9, minGap = 0;
    let everApplied = false;
    let disposed = false;
    let deliveryEpoch = 0;       // ignore pulls superseded by a push/rollback
    const originalVars = new Map();

    let palHash = null, palVars = null, palKeys = [];
    let rulHash = null, rulCss = '', derived = '', nativeDark = false;
    let mode = null;           // 'adopted' | 'element'
    let sheet = null, styleEl = null;

    let rootObs = null, headObs = null, observedHead = null;
    let fights = 0, fightAt = 0;

    let scanState = null, hints = null;

    /* ── 1. Palette: !important custom properties on the root element ───── */
    function setVars(vars) {
        const st = document.documentElement.style;
        const keys = Object.keys(vars);
        if (palVars) for (const k of palKeys) if (!Object.hasOwn(vars, k)) restoreVar(st, k);
        for (const k of keys) {
            if (!originalVars.has(k)) originalVars.set(k, [st.getPropertyValue(k), st.getPropertyPriority(k)]);
            if (!palVars || palVars[k] !== vars[k]) st.setProperty(k, vars[k], 'important');
        }
        palVars = vars; palKeys = keys;
        rootObs?.takeRecords();                    // our own writes are not a fight
    }
    function varIntact(st, key) {
        return st.getPropertyPriority(key) === 'important'
            && st.getPropertyValue(key).trim() === String(palVars[key]).trim();
    }

    function varsIntact() {
        const style = document.documentElement?.style;
        return !style || !palVars || palKeys.every((key) => varIntact(style, key));
    }

    function reassertVars() {
        const style = document.documentElement?.style;
        if (!style || !palVars) return;
        for (const key of palKeys) {
            if (!varIntact(style, key)) style.setProperty(key, palVars[key], 'important');
        }
        rootObs?.takeRecords();
    }
    function restoreVar(st, k) {
        const original = originalVars.get(k);
        if (original && varIntact(st, k)) {
            if (original[0]) st.setProperty(k, ...original);
            else st.removeProperty(k);
        }
        originalVars.delete(k);
    }
    function unsetVars() {
        const st = document.documentElement?.style;
        if (st && palVars) for (const k of palKeys) restoreVar(st, k);
        palVars = null; palKeys = []; palHash = null;
    }
    /** Single-node attribute observer: fires only when something else rewrites the root's style attribute. */
    function watchRoot() {
        if (rootObs || disposed || !document.documentElement) return;
        rootObs = new MutationObserver(() => { if (!disposed && palVars && !varsIntact()) repairTheme(); });
        rootObs.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
    }

    /* ── 2. Rules: constructed sheet, namespaced style element as fallback ─ */
    function adoptedHas() {
        try { return Array.from(document.adoptedStyleSheets).includes(sheet); } catch { return false; }
    }
    function setRules(css) {
        if (!css) { unmountRules(); return; }
        if (mode !== 'element') {
            try {
                const cur = document.adoptedStyleSheets;
                if (!cur || typeof cur.length !== 'number') throw new TypeError('adoptedStyleSheets unreadable through Xray');
                sheet ??= new CSSStyleSheet();
                sheet.replaceSync(css);
                const list = Array.from(cur);
                if (!list.includes(sheet)) document.adoptedStyleSheets = [...list, sheet];
                if (adoptedHas()) { mode = 'adopted'; stopHead(); return; }
                throw new TypeError('adoption not observable');
            } catch {
                sheet = null; mode = 'element';       // never guess: an unverifiable adoption could clobber page sheets
            }
        }
        if (!styleEl) { styleEl = document.createElementNS(XHTML, 'style'); styleEl.id = STYLE_ID; }   // XHTML ns: inert in SVG/XML docs otherwise
        if (styleEl.textContent !== css) styleEl.textContent = css;
        mountElement();
        watchHead();
    }
    /** Cheap liveness check on every flush: a page that reassigns document.adoptedStyleSheets drops us. */
    function ensureRules() {
        if (!rulHash || nativeDark) return;
        if (mode === 'adopted') {
            if (sheet && !adoptedHas()) { try { document.adoptedStyleSheets = [...Array.from(document.adoptedStyleSheets), sheet]; } catch { /* next flush */ } }
        } else if (mode === 'element' && styleEl && !styleEl.isConnected) { mountElement(); watchHead(); }
    }
    function mountElement() {
        const host = document.head || document.documentElement;
        if (host && styleEl && (styleEl.parentNode !== host || styleEl !== host.lastChild)) host.appendChild(styleEl);
    }
    function unmountRules() {
        if (sheet) { try { document.adoptedStyleSheets = Array.from(document.adoptedStyleSheets).filter((s) => s !== sheet); } catch { /* opaque */ } }
        if (styleEl) styleEl.remove();
        stopHead();
    }
    /** childList-only, two targets (root + head): catches head swaps and our node's removal, never page churn. */
    function watchHead() {
        if (mode !== 'element' || disposed || !document.documentElement) return;
        headObs ??= new MutationObserver(onHeadMutated);
        headObs.disconnect();
        headObs.observe(document.documentElement, { childList: true });
        observedHead = document.head || null;
        if (observedHead) headObs.observe(observedHead, { childList: true });
    }
    function onHeadMutated() {
        if (disposed || !styleEl) return;
        const head = document.head || null;
        if (head === observedHead && styleEl.isConnected && (!head || styleEl.parentNode === head)) return;
        repairTheme();
    }
    function stopHead() { if (headObs) { headObs.disconnect(); headObs = null; } observedHead = null; }
    function stopRoot() { if (rootObs) { rootObs.disconnect(); rootObs = null; } }

    /** Repair in the observer microtask, before paint. rAF can leave a white frame
     * when the page replaces root styles/head during its own animation frame.
     * Our writes drain/disconnect their observers; repeated page fights stay bounded. */
    function repairTheme() {
        if (disposed) return;
        const t = Date.now();
        if (t - fightAt > 5000) { fightAt = t; fights = 0; }
        if (++fights > MAX_FIGHTS) { stopHead(); stopRoot(); return; }
        reassertVars();
        if (mode === 'element' && styleEl) { mountElement(); watchHead(); }
    }

    /* ── 3. Flush pipeline ───────────────────────────────────────────────── */
    function apply(data) {
        if (disposed) return;
        if (!data || typeof data !== 'object' || !data.rules || typeof data.rules.hash !== 'string') { clear(); return; }
        deliveryEpoch++;
        // Keep a queued full stylesheet when a newer palette-only frame refers
        // to that same rule revision (hidden documents may defer many frames).
        if (!data.rules.css && pending?.rules.css && pending.rules.hash === data.rules.hash) {
            data = { ...data, rules: pending.rules };
        }
        pending = data;
        if (!everApplied) { flush(); return; }             // pre-paint path: synchronous
        schedule();
    }

    function schedule() {
        if (raf || gapTimer || !pending) return;
        const wait = lastFlushAt + minGap - performance.now();
        if (wait > 4) gapTimer = setTimeout(() => { gapTimer = 0; if (pending && !raf) raf = requestAnimationFrame(flush); }, wait);
        else raf = requestAnimationFrame(flush);           // coalesces; hidden documents wait until shown
    }

    function flush(frameTs) {
        raf = 0;
        if (disposed || !pending) return;
        if (!document.documentElement) { raf = requestAnimationFrame(flush); return; }
        const d = pending; pending = null;
        const t0 = performance.now();
        if (d.rules.hash !== rulHash) { cancelScan(); nativeDark = false; }
        if (!nativeDark && d.palette && d.palette.vars && typeof d.palette.vars === 'object' && d.palette.hash !== palHash) {
            setVars(d.palette.vars); palHash = d.palette.hash; watchRoot();
        }
        if (d.rules.hash !== rulHash) {
            if (typeof d.rules.css !== 'string' || !d.rules.css) { everApplied = true; sync(); return; }   // palette-only frame for a revision we lack
            rulCss = d.rules.css; rulHash = d.rules.hash; derived = ''; nativeDark = false;
            setRules(rulCss);
        } else ensureRules();
        everApplied = true;
        lastFlushAt = t0;
        // Frame cost = time until the next animation frame: style + layout + paint of this change. Only
        // measured for rAF-driven flushes; the synchronous first paint would count document parsing.
        if (typeof frameTs === 'number') {
            requestAnimationFrame((t) => { minGap = Math.min(GAP_MAX_MS, Math.max(0, (t - t0) * COST_X)); if (pending) schedule(); });
        }
        if (d.scan && !nativeDark) { hints = Array.isArray(d.hints) ? d.hints.slice(0, 16) : null; scheduleScan('payload'); }
        else if (!d.scan && scanState) cancelScan();
    }

    function clear() {
        deliveryEpoch++;
        pending = null;
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
        if (gapTimer) { clearTimeout(gapTimer); gapTimer = 0; }
        cancelScan(); stopHead(); stopRoot();
        unsetVars();
        unmountRules(); rulHash = null; rulCss = ''; derived = ''; nativeDark = false;
        const stale = document.getElementById(STYLE_ID);      // an earlier build's node
        if (stale && stale !== styleEl) stale.remove();
    }

    /* ── 4. CSSOM-derived overrides (forceUnthemedWebsites only) ──────────
     *      Pure arithmetic colour parsing, idle-scheduled, budgeted, each sheet
     *      visited once. Nothing here reads layout or computed style. */
    const NAMED = { white: 255, black: 0, silver: 192, gray: 128, grey: 128 };
    const CAPS = { sheets: 60, rules: 6000, out: 120000, depth: 6, slice: 6, runs: 8 };
    const SKIP_SEL = /(^|[\s,>+~])(input|textarea|select)|\[type=|search|::(before|after|placeholder|selection|backdrop)|:root|(^|,)\s*html\b/i;

    function quickLuma(v) {
        if (typeof v !== 'string') return -1;
        const s = v.trim().toLowerCase();
        if (!s || s.length > 48) return -1;
        if (s.charCodeAt(0) === 35) {
            const x = s.slice(1);
            if (!/^[0-9a-f]{3,8}$/.test(x)) return -1;
            let r, g, b;
            if (x.length === 3 || x.length === 4) {
                r = parseInt(x[0] + x[0], 16); g = parseInt(x[1] + x[1], 16); b = parseInt(x[2] + x[2], 16);
                if (x.length === 4 && parseInt(x[3] + x[3], 16) < 26) return -1;
            } else if (x.length === 6 || x.length === 8) {
                r = parseInt(x.slice(0, 2), 16); g = parseInt(x.slice(2, 4), 16); b = parseInt(x.slice(4, 6), 16);
                if (x.length === 8 && parseInt(x.slice(6, 8), 16) < 26) return -1;
            } else return -1;
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        const m = s.match(/^rgba?\s*\(([^()]*)\)$/);
        if (m) {
            const p = m[1].replaceAll('/', ' ').split(/[\s,]+/).filter(Boolean);
            if (p.length < 3) return -1;
            const conv = (t) => (t.endsWith('%') ? parseFloat(t) * 2.55 : parseFloat(t));
            const r = conv(p[0]), g = conv(p[1]), b = conv(p[2]);
            if (![r, g, b].every(Number.isFinite)) return -1;
            if (p.length > 3) { const a = p[3].endsWith('%') ? parseFloat(p[3]) / 100 : parseFloat(p[3]); if (Number.isFinite(a) && a < 0.1) return -1; }
            return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        }
        const h = s.match(/^hsla?\s*\(([^()]*)\)$/);
        if (h) { const l = parseFloat(h[1].replaceAll('/', ' ').split(/[\s,]+/).filter(Boolean)[2]); return Number.isFinite(l) ? l / 100 : -1; }
        return Object.hasOwn(NAMED, s) ? NAMED[s] / 255 : -1;
    }

    /** Detector hints from the host (Dark Reader MATCH selectors): the site already ships a dark theme. */
    function nativelyDark() {
        if (!hints) return false;
        for (const sel of hints) { try { if (document.querySelector(sel)) return true; } catch { /* invalid selector */ } }
        return false;
    }

    function collectSheets() {
        const list = [];
        let sheets;
        try { sheets = document.styleSheets; } catch { return list; }
        for (let i = 0; i < sheets.length && list.length < CAPS.sheets; i++) {
            const s = sheets[i];
            try {
                if (!s || s.disabled || (styleEl && s.ownerNode === styleEl)) continue;
                if (!s.cssRules || scanState.seen.has(s)) continue;    // throws on cross-origin sheets
                list.push(s);
            } catch { /* SecurityError */ }
        }
        return list;
    }

    function walk(rules, depth, out, budget) {
        for (const rule of rules) {
            if (budget.rules-- <= 0 || budget.chars <= 0) return;
            if (!rule || typeof rule.keyText === 'string') continue;
            const sel = rule.selectorText ?? (rule.constructor?.name === 'CSSNestedDeclarations' ? '&' : undefined);
            const st = rule.style;
            let body = '';
            if (typeof sel === 'string' && st && sel && sel.length <= 400 && !SKIP_SEL.test(sel)) {
                if (quickLuma(st.backgroundColor) > 0.45) body += 'background-color:var(--background,var(--surface,#181a1b))!important;';
                const fg = quickLuma(st.color);
                if (fg >= 0 && fg < 0.5) body += 'color:var(--on_background,var(--on_surface,#e0e0e0))!important;';
                if (quickLuma(st.borderColor) > 0.6) body += 'border-color:var(--outline_variant,rgba(255,255,255,.10))!important;';
            }
            // Keep grouping conditions and nesting context. Flattening a rule
            // from @media print or @container would change when it applies.
            const children = [];
            const kids = rule.cssRules;
            const header = typeof sel === 'string' ? sel : (rule.cssText ?? '').split('{', 1)[0].trim();
            const canNest = typeof sel === 'string' || /^@(media|supports|layer|container|scope|starting-style)\b/i.test(header);
            const wrapperCost = header.length + 2;
            if (canNest && kids?.length && depth < CAPS.depth && budget.chars > wrapperCost + body.length) {
                budget.chars -= wrapperCost + body.length;
                walk(kids, depth + 1, children, budget);
                budget.chars += wrapperCost + body.length;
            }
            if (!body && !children.length) continue;
            const css = header + '{' + body + children.join('') + '}';
            // Children already consumed their own budget above.
            const ownCost = wrapperCost + body.length;
            if (ownCost > budget.chars) continue;
            budget.chars -= ownCost;
            out.push(css);
        }
    }

    function cancelScan() {
        if (scanState?.handle) cancelIdleCallback(scanState.handle);
        scanState = null;
    }
    function scheduleScan(reason) {
        if (disposed || nativeDark) return;
        scanState ??= { seen: new WeakSet(), out: [], handle: 0, runs: 0, budget: { rules: CAPS.rules, chars: CAPS.out }, sheets: 0 };
        if (scanState.handle || scanState.runs >= CAPS.runs) return;
        scanState.handle = requestIdleCallback((deadline) => { scanState.handle = 0; scanState.runs++; runScanSlice(deadline); },
            { timeout: reason === 'load' ? 1500 : 4000 });
    }
    function runScanSlice(deadline) {
        if (disposed || !scanState) return;
        if (nativelyDark()) {
            // The site already ships a dark theme: hands off until the rules revision changes.
            nativeDark = true; hints = null; cancelScan(); derived = ''; stopRoot(); unsetVars(); unmountRules();
            return;
        }
        const sheets = collectSheets();
        if (!sheets.length) return;
        const budget = scanState.budget;
        const start = performance.now();
        for (const s of sheets) {
            if (scanState.sheets >= CAPS.sheets || budget.rules <= 0 || budget.chars <= 0) break;
            scanState.sheets++;
            scanState.seen.add(s);
            try { walk(s.cssRules, 0, scanState.out, budget); } catch { /* detached sheet */ }
            if (budget.rules <= 0 || performance.now() - start > CAPS.slice || (deadline.timeRemaining() <= 1 && !deadline.didTimeout)) break;
        }
        if (scanState.out.length) {
            const next = '@media screen{' + scanState.out.join('') + '}';
            if (next !== derived) { derived = next; setRules(rulCss + '\n' + derived); }
        }
        if (budget.rules > 0 && budget.chars > 0 && scanState.sheets < CAPS.sheets && collectSheets().length) scheduleScan('continue');   // lazily loaded chunks get the next slice
    }

    /* ── 5. Transport ────────────────────────────────────────────────────── */
    const have = () => ({ p: pending?.palette?.hash ?? palHash, r: pending?.rules?.hash ?? rulHash });

    /** Parent-process storage read at document_start: lands before first paint without waking the event page. */
    function fastPaint() {
        if (!IS_TOP) return Promise.resolve();
        let host = '';
        try { host = location.hostname.toLowerCase(); } catch { /* opaque origin */ }
        if (!host) return Promise.resolve();
        const epoch = deliveryEpoch;
        return browser.storage.local.get(['schema', 'enabled', 'seed', 'paint:palette', 'paint:' + host]).then((r) => {
            if (epoch !== deliveryEpoch || r.schema !== 8 || r.enabled === false || !r.seed?.webThemeEnabled) return;
            if (everApplied || pending) return;                          // authoritative payload already won the race
            const p = r['paint:palette'], e = r['paint:' + host];
            if (!p || typeof p.vars !== 'object' || !p.vars || typeof p.hash !== 'string') return;
            if (!e || typeof e.rules?.css !== 'string' || typeof e.rules.hash !== 'string') return;
            apply({ palette: p, rules: e.rules, scan: !!e.scan, hints: e.hints });
        }).catch(() => { /* storage unavailable in this context */ });
    }

    let attempt = 0;
    function sync() {
        if (disposed) return;
        const epoch = deliveryEpoch;
        browser.runtime.sendMessage({ type: 'GET_THEME_DATA', have: have() }).then((res) => {
            if (disposed || epoch !== deliveryEpoch) return;
            attempt = 0;
            if (!res || res.same) return;
            apply(res.data ?? null);
        }).catch(() => {
            // Event page cold start: back off with jitter so N frames of one page don't stampede a single wake-up.
            if (++attempt > 6) return;
            setTimeout(sync, Math.min(250 * 2 ** attempt, 8000) * (0.6 + Math.random() * 0.8));
        });
    }

    browser.runtime.onMessage.addListener((msg, sender) => {
        if (!msg || sender.id !== browser.runtime.id) return;
        if (msg.type === 'MATUGEN_UPDATE') apply(msg.data ?? null);
        else if (msg.type === 'MATUGEN_ROLLBACK') clear();
    });

    /* ── 6. Document lifecycle ───────────────────────────────────────────── */
    window.addEventListener('pageshow', (e) => {
        if (!e.persisted) return;                       // bfcache restore: DOM intact, palette may have moved on
        disposed = false;
        if (palVars) watchRoot();
        ensureRules();
        if (pending) schedule();
        sync();
    }, true);

    window.addEventListener('pagehide', (e) => {
        // Zero live observers/timers into bfcache, or Gecko evicts the entry.
        stopHead(); stopRoot(); cancelScan();
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
        if (gapTimer) { clearTimeout(gapTimer); gapTimer = 0; }
        if (!e.persisted) disposed = true;
    }, true);

    document.addEventListener('visibilitychange', () => {
        if (document.hidden || !scanState || scanState.handle || scanState.runs >= CAPS.runs) return;
        scheduleScan('visible');
    }, true);

    // Late CSS (route chunks, widgets) only shows up in document.styleSheets after load.
    window.addEventListener('load', () => { if (scanState) scheduleScan('load'); }, { once: true, capture: true });

    fastPaint().then(sync);
})();
