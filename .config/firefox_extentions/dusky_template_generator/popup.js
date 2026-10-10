/*
 * Dusky Template Generator — popup.js (ES module, Gecko 157.0.1+)
 *
 * A view over exactly one file: $XDG_CONFIG_HOME/dusky_sites/<domain>.css
 *   open      → read from disk (disk is the only source of truth)
 *   Auto-map  → scan the page, splice into the "auto" region
 *   Pick      → start the in-page picker (it owns the "picks" region) and close
 *   Save      → write the textarea verbatim (Ctrl+S); empty text removes the file
 *   Reload    → discard local edits, re-read disk
 *   Copy / Delete
 *
 * Mutating calls carry base_rev, except an explicitly confirmed overwrite,
 * so an ordinary write cannot replace a change since this popup last read.
 * Every mutating call also carries an opaque `origin` nonce which the broker
 * echoes in "dusky:changed", so this view can tell its own writes from foreign
 * ones without the (ambiguous) rev comparison v3 used.
 */
"use strict";

const theme = globalThis.__duskyTemplateTheme;
const themeStyle = document.createElement("style");
themeStyle.textContent = theme.css;
document.head.append(themeStyle);
theme.apply(document.documentElement);

const $ = (id) => document.getElementById(id);
const ui = {
  domain: $("domain"), css: $("css"), path: $("path"), status: $("status"),
  auto: $("auto"), pick: $("pick"), save: $("save"),
  copy: $("copy"), reload: $("reload"), del: $("delete"),
};
const BUTTONS = [ui.auto, ui.pick, ui.save, ui.copy, ui.reload, ui.del];
const ORIGIN = crypto.randomUUID();

const view = {
  tab: null, domain: "", saved: "", exists: false, path: "", rev: 0,
  busy: false, pickerOn: false, delArm: 0, autoArm: 0, delTimer: 0, autoTimer: 0,
  force: false, timer: 0, ready: false, docEpoch: 0,
};

function domainOf(url) {
  const u = URL.parse(url ?? "");
  if (!u || (u.protocol !== "https:" && u.protocol !== "http:")) return "";
  return u.hostname.replace(/^www\./, "").replace(/\.$/, "");
}
const fileName = () => `${view.domain}.css`;
const shortPath = (p) => String(p ?? "").replace(/^\/home\/[^/]+(?=\/)/, "~");
const dirty = () => ui.css.value !== view.saved;

function label(btn, title, sub) {
  btn.querySelector(".t").textContent = title;
  if (sub !== undefined) btn.querySelector(".s").textContent = sub;
}

function disarmAuto() {
  clearTimeout(view.autoTimer);
  view.autoTimer = view.autoArm = 0;
  label(ui.auto, "⚡ Auto-map", "scan this site's colour tokens");
}

function disarmDelete() {
  clearTimeout(view.delTimer);
  view.delTimer = view.delArm = 0;
  label(ui.del, "🗑 Delete", "remove this file from disk");
}

function say(text, kind = "") {
  clearTimeout(view.timer);
  ui.status.textContent = text;
  ui.status.className = `status ${kind}`;
  if (kind === "ok") {
    view.timer = setTimeout(() => { ui.status.textContent = ""; ui.status.className = "status"; }, 4000);
  }
}

/* Background broker. Conflicts are a normal reply, not an exception. */
async function bg(msg, tolerateConflict = false) {
  const reply = await browser.runtime.sendMessage({ ...msg, origin: ORIGIN });
  if (!reply || typeof reply !== "object") throw new Error("No reply from the background script");
  if (!reply.ok && !(tolerateConflict && reply.conflict)) throw new Error(reply.error ?? "Unknown error");
  return reply;
}
/* Page traffic always goes through the broker so it can inject on demand. */
const pg = (msg) => bg({ type: "page", tabId: view.tab.id, msg });

function setDoc(reply, keepDirty = false) {
  const wasDirty = dirty();
  const changed = (reply.rev ?? 0) !== view.rev;
  const conflict = keepDirty && wasDirty && (view.force || changed);
  if (changed) disarmDelete();
  view.docEpoch++;
  view.saved = reply.css ?? "";
  view.exists = !!reply.exists;
  view.rev = reply.rev ?? 0;
  if (reply.path) view.path = reply.path;
  if (!(keepDirty && wasDirty)) ui.css.value = view.saved;
  view.force = conflict && dirty();
  refresh();
}

function refresh() {
  const d = dirty();
  ui.path.textContent = shortPath(view.path) + (view.exists ? "" : "  · not created yet");
  if (d) {
    const b = document.createElement("b");
    b.textContent = "  · unsaved changes";
    ui.path.append(b);
  }
  ui.save.disabled = !d && !view.force;
  ui.save.classList.toggle("attention", d || view.force);
  label(ui.save,
    view.force ? "💾 Overwrite disk" : d ? "💾 Save changes" : "💾 Saved",
    view.force ? "the file changed underneath — this replaces it"
      : d ? "write the textarea to disk (Ctrl+S)" : "no unsaved changes");
  ui.copy.disabled = !ui.css.value.trim();
  ui.del.disabled = !view.exists;
  ui.pick.classList.toggle("on", view.pickerOn);
  label(ui.pick,
    view.pickerOn ? "■ Stop picking" : "🎯 Pick elements",
    view.pickerOn ? "picker is running on this page" : "click things on the page, assign a role");
  if (view.busy || !view.ready) for (const b of BUTTONS) b.disabled = true;
}

function busy(on) {
  ui.css.disabled = on;
  for (const b of BUTTONS) b.disabled = on;
  if (!on) refresh();
}

async function run(task) {
  if (view.busy || !view.ready) return;
  view.busy = true;
  view.docEpoch++;
  busy(true);
  try { await task(); }
  catch (err) { say(err.message ?? String(err), "err"); }
  finally {
    view.busy = false; busy(false);
    if (syncPending) void syncFromDisk();
  }
}

async function save() {
  const submitted = ui.css.value;
  const req = { type: "write", domain: view.domain, css: submitted, tabId: view.tab.id };
  if (!view.force) req.base_rev = view.rev;
  const reply = await bg(req, true);
  if (reply.conflict) {
    view.rev = reply.rev ?? view.rev;
    view.force = true;
    refresh();
    say("The file changed on disk (picker, or another editor). Click Overwrite to replace it, or ↻ Reload to see it.", "warn");
    return false;
  }
  setDoc(reply, ui.css.value !== submitted);
  say(reply.exists ? `Saved ${fileName()}` : "Template was empty — file removed", "ok");
  return true;
}

async function flushEdits() {
  if (view.force) {
    say("Resolve the disk conflict with Overwrite or Reload first.", "warn");
    return false;
  }
  if (!dirty()) return true;
  return save();
}

const readDisk = () => bg({ type: "read", domain: view.domain });

async function spliceRegion(region, body) {
  let reply = await bg({
    type: "splice", domain: view.domain, region, body, base_rev: view.rev, tabId: view.tab.id,
  }, true);
  if (reply.conflict) {                          /* refresh the base and retry once */
    view.rev = reply.rev ?? 0;
    reply = await bg({
      type: "splice", domain: view.domain, region, body, base_rev: view.rev, tabId: view.tab.id,
    }, true);
    if (reply.conflict) throw new Error("The file keeps changing on disk — press ↻ Reload and try again.");
  }
  setDoc(reply);
  return reply;
}

/* ── Auto-map ─────────────────────────────────────────────────────────── */
ui.auto.addEventListener("click", () => run(async () => {
  if (!await flushEdits()) return;
  say("Scanning the page's colour tokens…", "busy");
  const scan = await pg({ type: "scan" });
  if (!scan.ok) throw new Error(scan.error ?? "Scan failed");
  if (!scan.mapped) {
    say(`Found ${scan.found} colour variable(s), none matched a palette role — use Pick elements.`, "warn");
    return;
  }
  if (scan.kind === "structural" && Date.now() - view.autoArm > 6000) {
    clearTimeout(view.autoTimer);
    view.autoArm = Date.now();
    label(ui.auto, "⚡ Confirm Auto-map", "applies the broad structural theme");
    view.autoTimer = setTimeout(disarmAuto, 6000);
    say(`${view.domain} exposes no usable colour tokens (${scan.found} found). Auto-map would install a broad ` +
      "structural theme that repaints nearly every element. Click again within 6 s to apply it.", "warn");
    return;
  }
  disarmAuto();
  await spliceRegion("auto", scan.body);
  const how = scan.kind === "structural" ? "structural theme" : `${scan.mapped} of ${scan.found} tokens`;
  say(`Mapped ${how} → saved ${fileName()}${scan.palette ? "" : "  (palette variables not visible on this page)"}`, "ok");
}));

/* ── Picker ───────────────────────────────────────────────────────────── */
ui.pick.addEventListener("click", () => run(async () => {
  if (view.pickerOn) {
    await pg({ type: "picker", enable: false });
    view.pickerOn = false;
    setDoc(await readDisk(), true);
    say("Picker stopped", "ok");
    return;
  }
  if (!await flushEdits()) return;
  await pg({ type: "picker", enable: true });
  window.close();
}));

ui.save.addEventListener("click", () => run(save));

ui.reload.addEventListener("click", () => run(async () => {
  setDoc(await readDisk());
  say("Reloaded from disk", "ok");
}));

ui.copy.addEventListener("click", () => run(async () => {
  await navigator.clipboard.writeText(ui.css.value);
  say("Copied to clipboard", "ok");
}));

ui.del.addEventListener("click", () => run(async () => {
  if (Date.now() - view.delArm > 3000) {
    clearTimeout(view.delTimer);
    view.delArm = Date.now();
    label(ui.del, "🗑 Confirm delete", "click again to remove the file");
    say(`Click again within 3 s to delete ${fileName()} from disk.`, "warn");
    view.delTimer = setTimeout(disarmDelete, 3000);
    return;
  }
  disarmDelete();
  const reply = await bg({ type: "delete", domain: view.domain, base_rev: view.rev, tabId: view.tab.id }, true);
  if (reply.conflict) {
    setDoc(reply, true);
    say("The file changed on disk. Review it and click Delete again to confirm.", "warn");
    return;
  }
  setDoc(reply);
  say(`Deleted ${fileName()}`, "ok");
}));

ui.css.addEventListener("input", refresh);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (!ui.save.disabled) ui.save.click();
  }
});

/* ── Live sync: the background announces every successful mutation ────── */
let syncPending = false, syncRunning = false;
async function syncFromDisk() {
  syncPending = true;
  if (view.busy || syncRunning) return;
  syncRunning = true;
  try {
    while (syncPending && !view.busy) {
      syncPending = false;
      const epoch = view.docEpoch;
      const reply = await readDisk();
      // A local operation started while this snapshot was in flight. Read
      // again after it finishes rather than rewinding a successful save.
      if (epoch !== view.docEpoch || view.busy) { syncPending = true; continue; }
      setDoc(reply, true);
      if (view.force) say("The file changed on disk. Your unsaved edits are kept — Overwrite replaces it, ↻ Reload discards your edits.", "warn");
      else say("Updated from disk", "ok");
    }
  } catch (err) { say(err.message ?? String(err), "err"); }
  finally { syncRunning = false; }
}
browser.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== "dusky:changed" || msg.domain !== view.domain || !view.ready) return;
  if (msg.origin !== ORIGIN) void syncFromDisk();
});

/* ── Init ─────────────────────────────────────────────────────────────── */
(async function init() {
  void bg({ type: "palette" }).then((reply) => theme.apply(document.documentElement, reply.colors))
    .catch(() => {}); // Neutral, readable defaults remain available when the host is offline.
  busy(true);
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  view.tab = tab;
  view.domain = domainOf(tab?.url);
  if (!view.domain) {
    ui.domain.textContent = "not a website";
    ui.css.placeholder =
      "Open a normal http(s) website to theme it.\nFirefox keeps extensions out of about:, file: and add-on pages.";
    for (const b of BUTTONS) b.disabled = true;
    say("Nothing to theme here. The extension is fine — this page type is off limits to every add-on.", "warn");
    return;
  }
  ui.domain.textContent = view.domain;
  ui.css.placeholder =
    `No template for ${view.domain} yet.\n\n` +
    "⚡ Auto-map fills this from the site's colour tokens,\n" +
    "🎯 Pick elements lets you click parts of the page,\n" +
    "or paste CSS here and Save.";
  try {
    const reply = await readDisk();
    view.domain = reply.domain || view.domain;
    ui.domain.textContent = view.domain;
    setDoc(reply);
    for (const w of reply.warnings ?? []) say(w, "warn");
  } catch (err) {
    say(err.message, "err");
    refresh();
  }
  view.ready = true;
  /* Ping through the broker: it injects the content script if needed, so the
   * picker state is correct even on a freshly loaded tab. */
  const state = await pg({ type: "ping" }).catch(() => null);
  view.pickerOn = !!state?.active;
  busy(false);
})().catch((err) => {
  say(err.message ?? String(err), "err");
  for (const b of BUTTONS) b.disabled = true;
});
