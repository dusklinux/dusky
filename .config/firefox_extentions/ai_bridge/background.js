// background.js - Firefox 159 MV3 - ESM - Event Page
// Robust bridge with activeTabId Lock, Non-Destructive Keep-Alive, & Universal Support

const WS_URL = "ws://127.0.0.1:8765";
const AI_URL_PATTERNS = ["chatgpt.com", "chat.openai.com", "claude.ai", "meta.ai", "gemini.google.com"];

let ws = null;
let isConnecting = false;
let activeTabId = null;
let heartbeatInterval = null;
let reconnectTimer = null;
const knownAiTabs = new Set();

function log(...args){ console.log("[LocalAI BG]", ...args); }

// --- Badge / Title ---
async function setStatus(connected, extra=""){
  try{
    const title = connected ? "Local AI Bridge - Connected" : `Local AI Bridge - Disconnected ${extra}`.trim();
    const text = connected ? "ON" : "OFF";
    const color = connected ? "#2ecc71" : "#e74c3c";
    if(browser.action){
      await browser.action.setTitle({title});
      await browser.action.setBadgeText({text});
      await browser.action.setBadgeBackgroundColor({color});
    }
  }catch(e){}
}

function scheduleReconnect(){
  if(reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS(false);
  }, 2500);
}

function isAiUrl(url){
  return !!(url && AI_URL_PATTERNS.some(p => url.includes(p)));
}

async function sendMessageWithTimeout(tabId, msg, timeoutMs = 2500){
  return new Promise(async (resolve, reject) => {
    let done = false;
    const timer = setTimeout(() => {
      if(!done){
        done = true;
        reject(new Error("Tab message timeout after " + timeoutMs + "ms"));
      }
    }, timeoutMs);

    try {
      await browser.tabs.sendMessage(tabId, msg);
      if(!done){
        done = true;
        clearTimeout(timer);
        resolve(true);
      }
    } catch(err) {
      if(!done){
        done = true;
        clearTimeout(timer);
        reject(err);
      }
    }
  });
}

async function injectScriptsIntoTab(tabId) {
  try {
    await browser.scripting.executeScript({
      target: { tabId },
      files: ["content_isolated.js"],
    });
  } catch (e) {}
  try {
    await browser.scripting.executeScript({
      target: { tabId },
      files: ["content_main.js"],
      world: "MAIN",
    });
  } catch (e) {
    log("MAIN world executeScript error on tab", tabId, e);
  }
}

async function ensureContentScriptsInAllAiTabs() {
  try {
    const tabs = await browser.tabs.query({});
    for (const tab of tabs) {
      if (tab.id && isAiUrl(tab.url)) {
        // Ping tab first: only inject if content script is not already responding
        try {
          await sendMessageWithTimeout(tab.id, { type: "DOM_STATUS" }, 600);
          knownAiTabs.add(tab.id);
          continue;
        } catch {
          await injectScriptsIntoTab(tab.id);
          knownAiTabs.add(tab.id);
        }
      }
    }
  } catch (e) {}
}

// --- WebSocket Connection ---
function connectWS(force = false){
  if(!force && ws && ws.readyState === WebSocket.OPEN){
    setStatus(true);
    return;
  }

  if(isConnecting && !force) return;
  isConnecting = true;

  if(ws){
    try{
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      ws.close();
    }catch(e){}
    ws = null;
  }

  try{
    ws = new WebSocket(WS_URL);
  }catch(e){
    isConnecting = false;
    setStatus(false, "Init Error");
    scheduleReconnect();
    return;
  }

  ws.onopen = async ()=>{
    isConnecting = false;
    log("WS connected successfully!");
    await setStatus(true);
    ensureContentScriptsInAllAiTabs();

    if(heartbeatInterval) clearInterval(heartbeatInterval);
    heartbeatInterval = setInterval(() => {
      if(ws && ws.readyState === WebSocket.OPEN){
        try{ ws.send(JSON.stringify({type: "HEARTBEAT"})); }catch{}
      }
      try{ browser.storage.session?.get("activeTabId"); }catch{}
    }, 20000);
  };

  ws.onmessage = async (event)=>{
    let msg;
    try{ msg = JSON.parse(event.data); }catch{ return; }

    if(msg.type === "RELOAD_EXTENSION"){
      log("Reloading extension...");
      try{ ws.send(JSON.stringify({type: "RELOAD_ACK"})); }catch{}
      try{ browser.runtime.reload(); }catch{ connectWS(true); }
      return;
    }

    if(msg.type === "INSPECT_DOM"){
      try {
        const res = await browser.scripting.executeScript({
          target: {tabId: msg.tabId || activeTabId},
          func: () => {
            const ed = document.querySelector('#prompt-textarea, [contenteditable="true"], textarea');
            const btns = Array.from(document.querySelectorAll('button')).map(b => ({
              testid: b.getAttribute('data-testid'),
              label: b.getAttribute('aria-label'),
              text: b.textContent.trim(),
              disabled: b.disabled,
              vis: b.offsetParent !== null
            })).filter(b => b.vis && (b.testid?.includes('send') || b.label?.toLowerCase().includes('send') || b.testid?.includes('stop') || b.label?.toLowerCase().includes('stop')));
            const arts = Array.from(document.querySelectorAll('article, [data-testid*="turn"], [data-message-author-role]')).slice(-6).map(a => ({
              tag: a.tagName,
              testid: a.getAttribute('data-testid'),
              role: a.getAttribute('data-message-author-role'),
              className: a.className.slice(0, 60),
              text: a.textContent.slice(0, 100)
            }));
            const mds = Array.from(document.querySelectorAll('.markdown, .prose')).slice(-4).map(m => ({
              tag: m.tagName,
              className: m.className.slice(0, 60),
              text: m.textContent.slice(0, 100)
            }));
            const mainEl = document.querySelector('main');
            const mainChildren = mainEl ? Array.from(mainEl.children).map(c => ({
              tag: c.tagName,
              className: c.className.slice(0, 60),
              id: c.id,
              text: c.textContent.slice(0, 100)
            })) : [];
            const bodyClasses = document.body.className;
            const title = document.title;
            const wsContent = document.querySelector('[class*="WorkspaceContent"]');
            const turnNodes = wsContent ? Array.from(wsContent.querySelectorAll('div, section, article')).filter(n => {
              const txt = n.textContent;
              return (txt.includes('ChatGPT said:') || txt.includes('You said:') || n.getAttribute('data-message-author-role'));
            }).slice(0, 10).map(n => ({
              tag: n.tagName,
              className: n.className.slice(0, 60),
              testid: n.getAttribute('data-testid'),
              role: n.getAttribute('data-message-author-role'),
              text: n.textContent.slice(0, 80)
            })) : [];
            const assistantLeaves = Array.from(document.querySelectorAll('*')).filter(n => {
              const h = n.querySelector('h6, h5, h4, span, div');
              return (n.textContent.startsWith('ChatGPT said:') || (h && h.textContent.includes('ChatGPT said:'))) && n.children.length < 15;
            }).slice(0, 3).map(n => ({
              tag: n.tagName,
              className: n.className.slice(0, 60),
              attributes: Array.from(n.attributes).map(a => `${a.name}="${a.value}"`).join(' '),
              innerTags: Array.from(n.children).map(c => c.tagName + '.' + c.className.slice(0, 30)),
              text: n.textContent.slice(0, 120)
            }));
            const ws = document.querySelector('.Workspace-IfYAxV, [class*="Workspace-"]');
            const messages = ws ? Array.from(ws.querySelectorAll('*')).filter(el => {
              const t = el.textContent || '';
              return (t.includes('ChatGPT said:') || t.includes('UWSM')) && el.children.length > 0 && el.children.length < 5;
            }).slice(-5).map(el => ({
              tag: el.tagName,
              className: el.className.slice(0, 50),
              attributes: Array.from(el.attributes).map(a => `${a.name}="${a.value}"`).join(' '),
              text: el.textContent.slice(0, 100)
            })) : [];
            return {
              url: window.location.href,
              wsFound: !!ws,
              messages: messages,
              editor: ed ? {
                tag: ed.tagName,
                id: ed.id,
                contenteditable: ed.getAttribute('contenteditable'),
                text: ed.textContent,
                lexical: ed.getAttribute('data-lexical-editor')
              } : null,
              buttons: btns,
              articles: arts,
              markdowns: mds
            };
          }
        });
        ws?.send(JSON.stringify({type: "INSPECT_REPLY", result: res[0]?.result}));
      } catch (e) {
        ws?.send(JSON.stringify({type: "INSPECT_REPLY", error: String(e)}));
      }
      return;
    }

    if(msg.type === "PING" || msg.type === "HEARTBEAT"){
      try{ ws.send(JSON.stringify({type: "PONG", v: "build_2026_test_v2"})); }catch{}
      return;
    }

    if(msg.type === "REINJECT_SCRIPTS"){
      await ensureContentScriptsInAllAiTabs();
      try{ ws.send(JSON.stringify({type: "REINJECT_ACK"})); }catch{}
      return;
    }

    if(msg.type === "RELOAD_TAB"){
      const targetId = await findTargetTab(msg.tabId);
      if(targetId){
        try{
          await browser.tabs.reload(targetId);
          ws?.send(JSON.stringify({type: "RELOAD_TAB_ACK", tabId: targetId}));
        }catch(e){
          ws?.send(JSON.stringify({type: "RELOAD_TAB_ERR", error: String(e)}));
        }
      }
      return;
    }

    if(msg.type === "DOM_STATUS"){
      const targetId = await findTargetTab(msg.tabId, msg.model);
      if(!targetId){
        ws?.send(JSON.stringify({type: "DOM_STATUS_REPLY", error: "findTargetTab returned null"}));
        return;
      }
      try {
        await wakeTabIfNeeded(targetId);
        await sendMessageWithTimeout(targetId, msg, 2000);
      } catch (e) {
        try {
          await injectScriptsIntoTab(targetId);
          await new Promise(r => setTimeout(r, 250));
          await browser.tabs.sendMessage(targetId, msg);
        } catch (err) {
          ws?.send(JSON.stringify({type: "DOM_STATUS_REPLY", error: "sendMessage to tab " + targetId + " failed: " + err.message}));
        }
      }
      return;
    }

    if(msg.type === "DIAGNOSE_TABS"){
      try{
        const all = await browser.tabs.query({});
        const tabList = all.map(t => ({
          id: t.id,
          url: t.url,
          active: t.active,
          discarded: Boolean(t.discarded),
          lastAccessed: t.lastAccessed || 0,
          windowId: t.windowId,
          title: t.title
        }));
        ws?.send(JSON.stringify({type: "DIAGNOSE_REPLY", activeTabId, tabs: tabList}));
      }catch(e){
        ws?.send(JSON.stringify({type: "DIAGNOSE_REPLY", error: String(e)}));
      }
      return;
    }

    if(msg.type === "STOP_QUERY") {
      const targetId = await findTargetTab(msg.tabId || activeTabId, msg.model);
      if(targetId) {
        browser.tabs.sendMessage(targetId, msg).catch(() => {});
      }
      return;
    }

    if(msg.type === "RUN_QUERY" && (msg.query || (msg.files && msg.files.length > 0))){
      const targetId = await findTargetTab(msg.tabId, msg.model);
      if(!targetId){
        const modelHint = msg.model ? ` matching model '${msg.model}'` : "";
        ws?.send(JSON.stringify({type:"ERROR", error:`No AI tab found${modelHint}. Open chatgpt.com/claude.ai/gemini.google.com`, requestId: msg.requestId}));
        return;
      }

      let isGemini = false;
      let targetUrl = "";
      try{
        const tab = await wakeTabIfNeeded(targetId);
        targetUrl = tab?.url || "";
        isGemini = targetUrl.includes("gemini.google.com");
        // Activate the tab inside its Firefox window without stealing Wayland OS focus unless Gemini
        if (!tab?.active) {
          await browser.tabs.update(targetId, {active: true});
        }
        if (isGemini && tab?.windowId) {
          await browser.windows.update(tab.windowId, {focused: true});
        }
      }catch(e){ log("Tab activation warning (non-fatal):", e); }

      try {
        ws?.send(JSON.stringify({
          type: "TAB_INFO",
          requestId: msg.requestId,
          tabId: targetId,
          url: targetUrl,
          isGemini: isGemini
        }));
      } catch(e) {}

      try{
        await sendMessageWithTimeout(targetId, msg, 2500);
        log("Tab message delivered successfully to:", targetId);
      }catch(e){
        log("Tab message failed/timed out, attempting auto-heal:", targetId, e.message);
        try {
          await injectScriptsIntoTab(targetId);
          await new Promise(r => setTimeout(r, 350));

          try {
            await sendMessageWithTimeout(targetId, msg, 2500);
            log("Programmatic injection succeeded for tab:", targetId);
            return;
          } catch(retryErr){
            log("sendMessage still failed, reloading tab:", targetId, retryErr.message);
          }

          await browser.tabs.reload(targetId);
          let settled = false;
          const cleanup = () => {
            if(settled) return;
            settled = true;
            browser.tabs.onUpdated.removeListener(onLoaded);
            clearTimeout(failSafe);
          };
          const onLoaded = (tId, changeInfo) => {
            if(tId === targetId && changeInfo.status === "complete"){
              cleanup();
              setTimeout(async () => {
                await injectScriptsIntoTab(targetId);
                await new Promise(r => setTimeout(r, 500));
                browser.tabs.sendMessage(targetId, msg).catch((err) => {
                  ws?.send(JSON.stringify({type:"ERROR", error:"Tab message failed after reload: " + err.message, requestId: msg.requestId}));
                });
              }, 1200);
            }
          };
          const failSafe = setTimeout(() => {
            cleanup();
            ws?.send(JSON.stringify({type:"ERROR", error:"Tab auto-heal timed out", requestId: msg.requestId}));
          }, 15000);
          browser.tabs.onUpdated.addListener(onLoaded);
        } catch(reloadErr) {
          ws?.send(JSON.stringify({type:"ERROR", error:"Tab message failed: " + e.message, requestId: msg.requestId}));
        }
      }
    }
  };

  ws.onclose = ()=>{
    isConnecting = false;
    ws = null;
    if(heartbeatInterval){
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
    setStatus(false);
    scheduleReconnect();
  };

  ws.onerror = ()=>{
    isConnecting = false;
    if(ws){
      try{
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        ws.close();
      }catch(e){}
      ws = null;
    }
    if(heartbeatInterval){
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
    setStatus(false, "Err");
    scheduleReconnect();
  };
}

// --- Firefox MV3 Alarms Keep-Alive (Non-Destructive: NEVER re-inject over live tabs) ---
browser.alarms?.onAlarm?.addListener((alarm) => {
  if (alarm.name === "keepAlive") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWS(false);
    }
  }
});

try {
  browser.alarms?.create("keepAlive", { periodInMinutes: 1 });
} catch (e) {}

// --- Model Mapping & Tab Wakeup ---
const MODEL_HOST_MAP = {
  chatgpt: ["chatgpt.com", "chat.openai.com"],
  gpt: ["chatgpt.com", "chat.openai.com"],
  gemini: ["gemini.google.com"],
  claude: ["claude.ai"],
  meta: ["meta.ai"],
};

function matchesModel(url, modelFilter) {
  if (!isAiUrl(url)) return false;
  if (!modelFilter) return true;
  const key = String(modelFilter).toLowerCase().trim();
  const patterns = MODEL_HOST_MAP[key] || [key];
  return patterns.some((p) => url.toLowerCase().includes(p));
}

async function wakeTabIfNeeded(tabId) {
  let tab = await browser.tabs.get(tabId);
  if (tab.discarded) {
    log("Waking discarded AI tab:", tabId, tab.url);
    await browser.tabs.update(tabId, { active: true });
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 200));
      tab = await browser.tabs.get(tabId);
      if (!tab.discarded && tab.status === "complete") break;
    }
    await injectScriptsIntoTab(tabId);
    await new Promise((r) => setTimeout(r, 300));
  }
  return tab;
}

// --- Active Window / Tab Priority Tab Targeting ---
async function findTargetTab(preferredId, modelFilter = null) {
  // 1. Explicit tabId override
  if (preferredId) {
    try {
      const tab = await browser.tabs.get(preferredId);
      if (tab && matchesModel(tab.url, modelFilter)) {
        activeTabId = preferredId;
        return preferredId;
      }
    } catch {}
  }

  // 2. Active tab in lastFocusedWindow matching modelFilter
  try {
    const focusedTabs = await browser.tabs.query({ active: true, lastFocusedWindow: true });
    for (const tab of focusedTabs) {
      if (tab?.id && matchesModel(tab.url, modelFilter)) {
        activeTabId = tab.id;
        knownAiTabs.add(tab.id);
        try { await browser.storage.session.set({ activeTabId: tab.id }); } catch {}
        return tab.id;
      }
    }
  } catch {}

  // 3. Previously tracked activeTabId (if still valid and matches modelFilter)
  if (activeTabId) {
    try {
      const tab = await browser.tabs.get(activeTabId);
      if (tab && matchesModel(tab.url, modelFilter)) {
        return activeTabId;
      }
    } catch {
      activeTabId = null;
    }
  }

  // 4. All AI tabs matching modelFilter, prioritizing active tabs and most recent lastAccessed timestamp
  try {
    const allTabs = await browser.tabs.query({});
    const candidates = allTabs.filter((t) => t?.id && matchesModel(t.url, modelFilter));
    if (candidates.length > 0) {
      candidates.sort((a, b) => {
        if (Boolean(a.active) !== Boolean(b.active)) return a.active ? -1 : 1;
        return (b.lastAccessed || 0) - (a.lastAccessed || 0);
      });
      const chosen = candidates[0];
      activeTabId = chosen.id;
      knownAiTabs.add(chosen.id);
      try { await browser.storage.session.set({ activeTabId: chosen.id }); } catch {}
      return chosen.id;
    }
  } catch (e) {}

  return null;
}

browser.tabs.onActivated?.addListener(async ({tabId})=>{
  try{
    const tab = await browser.tabs.get(tabId);
    if(isAiUrl(tab?.url)){
      activeTabId = tabId;
      knownAiTabs.add(tabId);
      try{ await browser.storage.session.set({activeTabId: tabId}); }catch{}
    }
  }catch{}
});

browser.tabs.onUpdated?.addListener(async (tabId, change, tab)=>{
  if(change.url !== undefined || change.status === "complete"){
    if(isAiUrl(tab?.url)){
      activeTabId = tabId;
      knownAiTabs.add(tabId);
      try{ await browser.storage.session.set({activeTabId: tabId}); }catch{}
    } else if(activeTabId === tabId){
      activeTabId = null;
      knownAiTabs.delete(tabId);
    }
  }
});

browser.tabs.onRemoved?.addListener((tabId)=>{
  knownAiTabs.delete(tabId);
  if(activeTabId === tabId) activeTabId = null;
});

browser.runtime.onMessage.addListener((msg, sender)=>{
  if(!msg?.type) return;
  if(sender?.tab?.id && isAiUrl(sender.tab.url)){
    activeTabId = sender.tab.id;
    knownAiTabs.add(sender.tab.id);
    browser.storage.session.set({activeTabId: sender.tab.id}).catch(() => {});
  }
  if(msg.type === "TAB_REGISTER"){
    log("Tab registered via content script:", sender?.tab?.id);
    return;
  }
  if(ws && ws.readyState === WebSocket.OPEN){
    try{
      ws.send(JSON.stringify({...msg, tabId: sender?.tab?.id}));
    }catch(e){}
  }
});

browser.runtime.onStartup.addListener(() => {
  connectWS(true);
  ensureContentScriptsInAllAiTabs();
});

browser.runtime.onInstalled.addListener(() => {
  connectWS(true);
  ensureContentScriptsInAllAiTabs();
});

browser.storage.session?.get("activeTabId").then((stored) => {
  if (stored?.activeTabId) activeTabId = stored.activeTabId;
}).catch(() => {});

connectWS(true);

if (browser.action?.onClicked) {
  browser.action.onClicked.addListener(() => {
    connectWS(true);
    ensureContentScriptsInAllAiTabs();
  });
}
