// background.js - Firefox 153 MV3 - ESM - Event Page
// Robust bridge with activeTabId Lock, Auto-Healing, & Universal Support

const WS_URL = "ws://127.0.0.1:8765";
const AI_URL_PATTERNS = ["chatgpt.com", "chat.openai.com", "claude.ai", "meta.ai", "gemini.google.com"];

let ws = null;
let isConnecting = false;
let activeTabId = null;
let heartbeatInterval = null;

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

let reconnectTimer = null;

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
            return {
              url: window.location.href,
              editor: ed ? {
                tag: ed.tagName,
                id: ed.id,
                contenteditable: ed.getAttribute('contenteditable'),
                text: ed.textContent,
                lexical: ed.getAttribute('data-lexical-editor')
              } : null,
              buttons: btns
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
      try{ ws.send(JSON.stringify({type: "PONG", v: "build_2026_test_v1"})); }catch{}
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
      const targetId = await findTargetTab(msg.tabId);
      if(!targetId){
        ws?.send(JSON.stringify({type: "DOM_STATUS_REPLY", error: "findTargetTab returned null"}));
        return;
      }
      browser.tabs.sendMessage(targetId, msg).catch((e) => {
        ws?.send(JSON.stringify({type: "DOM_STATUS_REPLY", error: "sendMessage to tab " + targetId + " failed: " + e.message}));
      });
      return;
    }

    if(msg.type === "DIAGNOSE_TABS"){
      try{
        const all = await browser.tabs.query({});
        const tabList = all.map(t => ({id: t.id, url: t.url, active: t.active, title: t.title}));
        ws?.send(JSON.stringify({type: "DIAGNOSE_REPLY", tabs: tabList}));
      }catch(e){
        ws?.send(JSON.stringify({type: "DIAGNOSE_REPLY", error: String(e)}));
      }
      return;
    }

async function sendMessageWithTimeout(tabId, msg, timeoutMs = 2000){
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

    if(msg.type === "RUN_QUERY" && msg.query){
      const targetId = await findTargetTab(msg.tabId);
      if(!targetId){
        ws?.send(JSON.stringify({type:"ERROR", error:"No AI tab found. Open chatgpt.com/claude.ai/gemini.google.com", requestId: msg.requestId}));
        return;
      }

      let isGemini = false;
      let targetUrl = "";
      try{
        const tab = await browser.tabs.get(targetId);
        targetUrl = tab.url || "";
        isGemini = targetUrl.includes("gemini.google.com");
        if(tab.windowId) await browser.windows.update(tab.windowId, {focused: true});
        await browser.tabs.update(targetId, {active: true});
      }catch(e){ log("Focus error (non-fatal):", e); }

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
          try {
            await browser.scripting.executeScript({
              target: {tabId: targetId},
              files: ["content_isolated.js"]
            });
          } catch(err){}
          try {
            await browser.scripting.executeScript({
              target: {tabId: targetId},
              files: ["content_main.js"],
              world: "MAIN"
            });
          } catch(err){
            try {
              await browser.scripting.executeScript({
                target: {tabId: targetId},
                files: ["content_main.js"]
              });
            } catch(e) {}
          }

          await new Promise(r => setTimeout(r, 400));

          try {
            await sendMessageWithTimeout(targetId, msg, 2500);
            log("Programmatic injection succeeded for tab:", targetId);
            return;
          } catch(retryErr){
            log("sendMessage still failed, reloading tab:", targetId, retryErr.message);
          }

          // Last resort: reload tab
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
                try {
                  await browser.scripting.executeScript({ target: {tabId: targetId}, files: ["content_isolated.js"] });
                } catch(e2) {}
                try {
                  await browser.scripting.executeScript({ target: {tabId: targetId}, files: ["content_main.js"], world: "MAIN" });
                } catch(e2) {}
                await new Promise(r => setTimeout(r, 600));
                browser.tabs.sendMessage(targetId, msg).catch((err) => {
                  ws?.send(JSON.stringify({type:"ERROR", error:"Tab message failed after reload: " + err.message, requestId: msg.requestId}));
                });
              }, 1500);
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

function scheduleReconnect(){
  if(reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS(false);
  }, 2500);
}

// --- Firefox MV3 Alarms Keep-Alive & Auto-Heal ---
browser.alarms?.onAlarm?.addListener((alarm) => {
  if (alarm.name === "keepAlive") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectWS(false);
    }
    ensureContentScriptsInAllAiTabs();
  }
});

try {
  browser.alarms?.create("keepAlive", { periodInMinutes: 1 });
} catch (e) {}

const knownAiTabs = new Set();

function isAiUrl(url){
  return !!(url && AI_URL_PATTERNS.some(p => url.includes(p)));
}

async function ensureContentScriptsInAllAiTabs() {
  try {
    const tabs = await browser.tabs.query({});
    for (const tab of tabs) {
      if (tab.id && isAiUrl(tab.url)) {
        try {
          await browser.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content_isolated.js"]
          });
        } catch (e) {}
        try {
          await browser.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content_main.js"],
            world: "MAIN"
          });
        } catch (e) {
          try {
            await browser.scripting.executeScript({
              target: { tabId: tab.id },
              files: ["content_main.js"]
            });
          } catch (e2) {}
        }
        knownAiTabs.add(tab.id);
      }
    }
  } catch (e) {}
}

// --- Active Window / Tab Priority Tab Targeting ---
async function findTargetTab(preferredId){
  // 1. Explicit preferred tab requested by caller
  if(preferredId){
    try{
      const tab = await browser.tabs.get(preferredId);
      if(tab && isAiUrl(tab.url)) {
        activeTabId = preferredId;
        return preferredId;
      }
    }catch{}
  }

  // 2. Active tab in the focused / active window (USER IS CURRENTLY LOOKING AT THIS)
  try{
    const focusedTabs = await browser.tabs.query({ active: true, lastFocusedWindow: true });
    for(const tab of focusedTabs){
      if(tab?.id && isAiUrl(tab.url)){
        activeTabId = tab.id;
        knownAiTabs.add(tab.id);
        try{ await browser.storage.session.set({activeTabId: tab.id}); }catch{}
        return tab.id;
      }
    }
  }catch{}

  // 3. Active tab in any open window
  try{
    const activeTabs = await browser.tabs.query({ active: true });
    for(const tab of activeTabs){
      if(tab?.id && isAiUrl(tab.url)){
        activeTabId = tab.id;
        knownAiTabs.add(tab.id);
        try{ await browser.storage.session.set({activeTabId: tab.id}); }catch{}
        return tab.id;
      }
    }
  }catch{}

  // 4. Previously locked / active AI tab
  if(activeTabId){
    try{
      const tab = await browser.tabs.get(activeTabId);
      if(tab && isAiUrl(tab.url)) return activeTabId;
      activeTabId = null;
    }catch{
      activeTabId = null;
    }
  }

  // 5. Any known AI tab
  for(const tabId of knownAiTabs){
    try{
      const tab = await browser.tabs.get(tabId);
      if(tab && isAiUrl(tab.url)){
        activeTabId = tabId;
        return tabId;
      }
      knownAiTabs.delete(tabId);
    }catch{
      knownAiTabs.delete(tabId);
    }
  }

  // 6. Fallback: search all tabs
  try{
    const allTabs = await browser.tabs.query({});
    const aiTabs = allTabs.filter(tab => isAiUrl(tab.url));
    if(aiTabs.length > 0){
      const activeAi = aiTabs.find(t => t.active) || aiTabs[0];
      activeTabId = activeAi.id;
      knownAiTabs.add(activeAi.id);
      try{ await browser.storage.session.set({activeTabId: activeAi.id}); }catch{}
      return activeAi.id;
    }
  }catch(e){}

  return null;
}

// Track active tab with strict URL validation & session persistence
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

// Forward content script messages -> WS & Lock activeTabId
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
