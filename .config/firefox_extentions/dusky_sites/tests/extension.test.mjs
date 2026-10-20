import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = process.env.DUSKY_EXTENSION_SOURCE ?? fileURLToPath(new URL('../extension/', import.meta.url));
const event = () => ({ addListener() {} });
const clone = (value) => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function background() {
    const disk = {}, session = {}, timers = new Map(), sends = [];
    let timer = 0;
    const storage = (data) => ({
        async get(keys) { return Object.fromEntries((keys ?? Object.keys(data)).filter(k => k in data).map(k => [k, clone(data[k])])); },
        async set(up) { Object.assign(data, clone(up)); },
        async remove(keys) { for (const k of keys) delete data[k]; }
    });
    const browser = {
        storage: { local: storage(disk), session: storage(session) },
        runtime: { id: 'test', onMessage: event(), onStartup: event(), onInstalled: event(), onSuspend: event() },
        tabs: { onUpdated: event(), onActivated: event(), onRemoved: event(), async query() { return []; }, async sendMessage(...args) { sends.push(args); } },
        windows: { onFocusChanged: event(), WINDOW_ID_NONE: -1 },
        action: { onClicked: event(), async setTitle() {} },
        permissions: { onAdded: event(), onRemoved: event() },
        theme: { onUpdated: event(), async update() {}, async reset() {} },
        alarms: { onAlarm: event(), async get() {}, async create() {} }
    };
    const context = vm.createContext({ browser, URL, console, performance, setTimeout(fn) { timers.set(++timer, fn); return timer; }, clearTimeout(id) { timers.delete(id); } });
    const source = readFileSync(root + 'background.js', 'utf8').replace(/    ready\(\);\s*\}\)\(\);\s*$/, `globalThis.api = { S, adopt, onPaletteUpdate, resolve, themeDataFor, pushTab, queueTab, lastSig, slots, flushPaint, clearPaint, markPaint, rulesCache, packRules, matchScore, reassemble, persistWarm, boot, setEnabled, domainCache, buildThemePayload, migrate };})();`);
    vm.runInContext(source, context);
    const api = context.api;
    api.S.cfg.webThemeEnabled = true;
    api.adopt({ colors: { '--background': '#112233', '--on_background': '#eeeeee' }, colorsRev: 'c1', websitesRev: 'r1', websites: { 'example.com': 'body{color:red}' }, webThemeEnabled: true }, false);
    return { ...api, browser, disk, session, timers, sends };
}

function content() {
    const props = new Map(), priorities = new Map(), timers = new Map(), listeners = new Map();
    const style = {
        setProperty(k, v, priority = '') { props.set(k, v); priorities.set(k, priority); },
        getPropertyValue(k) { return props.get(k) ?? ''; },
        getPropertyPriority(k) { return priorities.get(k) ?? ''; },
        removeProperty(k) { props.delete(k); priorities.delete(k); }
    };
    let timer = 0;
    const rootElement = { style, appendChild(el) { el.isConnected = true; el.parentNode = this; this.lastChild = el; } };
    const document = { documentElement: rootElement, head: rootElement, styleSheets: [], hidden: false, addEventListener() {}, getElementById() {}, querySelector() { return null; }, createElementNS() { return { textContent: '', remove() { this.isConnected = false; } }; } };
    const window = { addEventListener(name, fn) { listeners.set(name, fn); } }; window.top = window;
    let response = Promise.resolve({ data: null });
    const browser = { storage: { local: { async get() { return {}; } } }, runtime: { id: 'test', onMessage: event(), sendMessage() { return response; } } };
    const context = vm.createContext({ document, window, browser, location: { hostname: '' }, console, performance,
        MutationObserver: class { observe() {} disconnect() {} takeRecords() {} },
        requestAnimationFrame(fn) { timers.set(++timer, fn); return timer; }, cancelAnimationFrame(id) { timers.delete(id); },
        requestIdleCallback(fn) { timers.set(++timer, fn); return timer; }, cancelIdleCallback(id) { timers.delete(id); },
        setTimeout(fn) { timers.set(++timer, fn); return timer; }, clearTimeout(id) { timers.delete(id); }
    });
    const source = readFileSync(root + 'content.js', 'utf8').replace('    fastPaint().then(sync);', 'globalThis.api = { apply, clear, flush, walk, sync, have, setVars, unsetVars, scheduleScan, runScanSlice, fastPaint, get state() { return { pending, palHash, rulHash, scanState }; } };');
    vm.runInContext(source, context);
    return { ...context.api, get state() { return context.api.state; }, document, style, timers, listeners, browser, respond(p) { response = p; }, setHost(host) { context.location.hostname = host; } };
}
const data = (p = 'p1', r = 'r1', css = 'body{color:red}') => ({ palette: { vars: { '--primary': '#123456' }, hash: p }, rules: { hash: r, ...(css === undefined ? {} : { css }) } });

test('palette changes retain rule map on a delta', () => {
    const b = background();
    b.adopt({ colors: { '--background': '#334455' }, colorsRev: 'c2', websitesRev: 'r1' }, true);
    assert.equal(b.resolve('example.com').rules.css.includes('color:red'), true);
    assert.equal(b.session.colors.colorsRev, 'c2');
});
test('domain matcher distinguishes exact, www, wildcard and unrelated', () => {
    const b = background();
    assert.ok(b.matchScore('example.com', 'example.com', false) > b.matchScore('www.example.com', 'example.com', false));
    assert.equal(b.matchScore('sub.example.com', 'example.com', false), 0);
    assert.ok(b.matchScore('sub.example.com', '*.example.com', false));
    assert.equal(b.matchScore('notexample.com', '*.example.com', false), 0);
});
test('partial-frame pull cannot acknowledge the entire tab', () => {
    const b = background();
    b.themeDataFor({ id: 'test', tab: { id: 1, url: 'https://example.com' }, frameId: 0 }, {});
    b.pushTab(1, 'https://example.com', false);
    assert.equal(b.slots.size, 1);
});
test('queued first theme is rolled back before its send when globally disabled', async () => {
    const b = background();
    b.pushTab(1, 'https://example.com', true);
    b.browser.tabs.query = async () => [{ id: 1, url: 'https://example.com' }];
    b.onPaletteUpdate({ colors: b.S.theme.colors, colorsRev: 'c1', websitesRev: 'r1', webThemeEnabled: false });
    await tick();
    assert.equal(b.slots.get(1).msg.type, 'MATUGEN_ROLLBACK');
});
test('rule transitions invalidate first-paint cache for background hosts', async () => {
    const b = background();
    b.markPaint('example.com', b.resolve('example.com').rules); await b.flushPaint();
    assert.ok(b.disk['paint:example.com']);
    b.onPaletteUpdate({ colors: b.S.theme.colors, colorsRev: 'c1', websitesRev: 'r1', disabledSites: ['example.com'] });
    await tick();
    assert.equal(b.disk['paint:example.com'], undefined);
});
test('paused extension cannot repopulate first-paint palette', async () => {
    const b = background(); b.S.enabled = false;
    await b.clearPaint(); await b.flushPaint();
    assert.equal(b.disk['paint:palette'], undefined);
});
test('fallback cache expires without an unrelated rule revision', () => {
    const b = background(); b.S.cfg.forceUnthemedWebsites = true;
    b.domainCache.set('other.test', { css: '', neg: false, at: Date.now() - 700000 });
    b.rulesCache.set('other.test', b.packRules('stale', true, null, false));
    assert.notEqual(b.resolve('other.test').rules.css, 'stale');
});
test('rollback restores original inline palette values and priorities', () => {
    const c = content(); c.style.setProperty('--primary', 'original', '');
    c.apply(data()); c.clear();
    assert.equal(c.style.getPropertyValue('--primary'), 'original');
    assert.equal(c.style.getPropertyPriority('--primary'), '');
});
test('rollback leaves a newer page-owned inline value intact', () => {
    const c = content(); c.apply(data()); c.style.setProperty('--primary', 'page-edit'); c.clear();
    assert.equal(c.style.getPropertyValue('--primary'), 'page-edit');
});
test('palette-only frame preserves queued full rules', () => {
    const c = content(); c.apply(data());
    c.apply(data('p2', 'r2', 'body{color:blue}'));
    c.apply({ palette: { vars: { '--primary': '#654321' }, hash: 'p3' }, rules: { hash: 'r2' } });
    c.flush(1);
    assert.equal(c.state.rulHash, 'r2');
});
test('late content pull cannot undo a newer pushed rollback', async () => {
    const c = content(); let resolve;
    c.respond(new Promise(r => { resolve = r; }));
    c.sync(); c.clear(); resolve({ data: data() }); await tick();
    assert.equal(c.state.rulHash, null);
});
test('fallback scanner preserves media conditions', () => {
    const c = content(), out = [], budget = { rules: 100, chars: 120000 };
    c.walk([{ cssText: '@media print { .paper { color:black } }', cssRules: [{ selectorText: '.paper', style: { color: 'black' } }] }], 0, out, budget);
    assert.match(out.join(''), /@media print/);
});
test('fallback scanner preserves nested selector context', () => {
    const c = content(), out = [], budget = { rules: 100, chars: 120000 };
    c.walk([{ selectorText: '.card', style: {}, cssRules: [{ selectorText: '& .label', style: { color: 'black' } }] }], 0, out, budget);
    assert.match(out.join(''), /\.card\s*\{/);
});
test('fallback output is bounded by characters, not selector count', () => {
    const c = content(), out = [], budget = { rules: 6000, chars: 120000 };
    c.walk(Array.from({ length: 2000 }, (_, i) => ({ selectorText: '.item' + i + 'x'.repeat(300), style: { color: 'black', backgroundColor: 'white' } })), 0, out, budget);
    assert.ok(out.join('').length <= 120000);
});

test('migration removes obsolete paint entries', async () => {
    const b = background();
    b.disk['paint:palette'] = { hash: 'old' };
    b.disk['paint:example.com'] = { rules: { css: 'old' } };
    b.disk.paintIndex = [['example.com', 'old']];
    await b.migrate();
    assert.equal(b.disk.schema, 8);
    assert.equal(b.disk['paint:example.com'], undefined);
    assert.equal(b.disk['paint:palette'], undefined);
});
test('stale warm cache metadata cannot suppress a missing disk palette', async () => {
    const b = background();
    b.disk.schema = 8; b.disk.enabled = false;
    b.session.meta = { paletteWritten: b.S.palette.hash };
    await b.boot(); b.S.enabled = true;
    await b.flushPaint();
    assert.equal(b.disk['paint:palette'].hash, b.S.palette.hash);
});
test('clear and publication serialize when disk storage is delayed', async () => {
    const b = background(); let release;
    const first = new Promise(r => { release = r; });
    const save = b.browser.storage.local.set; let blocked = true;
    b.browser.storage.local.set = async (up) => { if (blocked) { blocked = false; await first; } await save(up); };
    b.markPaint('example.com', b.resolve('example.com').rules);
    const flush = b.flushPaint(); await tick();
    const clear = b.clearPaint(); release(); await Promise.all([flush, clear]);
    assert.equal(b.disk['paint:example.com'], undefined);
    assert.equal(b.disk['paint:palette'], undefined);
});
test('failed paint writes can be retried', async () => {
    const b = background(), save = b.browser.storage.local.set;
    b.markPaint('example.com', b.resolve('example.com').rules);
    b.browser.storage.local.set = async () => { throw new Error('fixture disk failure'); };
    await b.flushPaint(); b.browser.storage.local.set = save;
    await b.flushPaint(); assert.ok(b.disk['paint:example.com']);
});
test('rule identity includes scan hints', () => {
    const b = background();
    assert.notEqual(b.packRules('same', true, ['.dark'], false).hash, b.packRules('same', true, ['.night'], false).hash);
});
test('light palette drives chrome and content scheme by default', () => {
    const b = background();
    const payload = b.buildThemePayload({ '--background': '#fafafa', '--on_background': '#111111' });
    assert.equal(payload.properties.color_scheme, 'light');
    assert.equal(payload.properties.content_color_scheme, 'light');
});
test('scanner budgets are shared between sheets', () => {
    const c = content(); c.scheduleScan('payload');
    const budget = c.state.scanState.budget;
    assert.equal(budget.chars, 120000);
    c.document.styleSheets = Array.from({ length: 70 }, () => ({ cssRules: [{ selectorText: '.item', style: { color: 'black' } }] }));
    c.runScanSlice({ didTimeout: true, timeRemaining() { return 10; } });
    assert.ok(c.state.scanState.sheets <= 60);
    assert.ok(c.state.scanState.budget.chars < 120000);
});
test('500 latest-wins updates preserve full stylesheet and final palette', () => {
    const c = content(); c.apply(data());
    for (let i = 0; i < 500; i++) c.apply({ palette: { hash: 'p' + i, vars: { '--primary': '#' + i.toString(16).padStart(6, '0') } }, rules: { hash: 'r2', ...(i === 0 ? { css: 'body{color:blue}' } : {}) } });
    c.flush(1);
    assert.equal(c.state.palHash, 'p499'); assert.equal(c.state.rulHash, 'r2');
});

test('scanner keeps declarations following nested at-rules in parent context', () => {
    const c = content(), out = [], budget = { rules: 100, chars: 120000 };
    c.walk([{ selectorText: '.card', style: {}, cssRules: [{ constructor: { name: 'CSSNestedDeclarations' }, style: { color: 'black' } }] }], 0, out, budget);
    assert.match(out.join(''), /\.card\{&\{/);
});

test('fast paint honors pause, webpage opt-out and schema migration', async () => {
    for (const flags of [{ enabled: false }, { seed: { webThemeEnabled: false } }, { schema: 7 }, {}]) {
        const c = content(); c.setHost('example.com');
        c.browser.storage.local.get = async () => ({ schema: 8, enabled: true, seed: { webThemeEnabled: true }, 'paint:palette': data().palette, 'paint:example.com': { rules: data().rules }, ...flags });
        await c.fastPaint();
        assert.equal(c.state.rulHash, Object.keys(flags).length ? null : 'r1');
    }
});

test('committed pause wins over an older session snapshot after a respawn', async () => {
    const b = background(); b.disk.schema = 8; b.disk.enabled = false;
    b.session.meta = { enabled: true };
    await b.boot(); assert.equal(b.S.enabled, false);
});

test('cached authored rules avoid rescanning the rule map in forced mode', () => {
    const b = background(); b.S.cfg.forceUnthemedWebsites = true;
    const first = b.resolve('example.com');
    b.S.theme = { ...b.S.theme, websites: new Proxy({}, { ownKeys() { throw new Error('unnecessary rule-map scan'); } }) };
    assert.equal(b.resolve('example.com').rules, first.rules);
});
