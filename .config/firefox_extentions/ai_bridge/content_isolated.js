(function () {
  if (typeof globalThis.__LOCAL_AI_BRIDGE_ISOLATED_CLEANUP__ === "function") {
    try { globalThis.__LOCAL_AI_BRIDGE_ISOLATED_CLEANUP__(); } catch (e) {}
  }

  const BRIDGE = "__LOCAL_AI_BRIDGE__";

  // Inject content_main.js into page MAIN world dynamically for Firefox MV3 compatibility
  try {
    const s = document.createElement("script");
    s.src = browser.runtime.getURL("content_main.js") + "?t=" + Date.now();
    s.onload = function() { this.remove(); };
    (document.head || document.documentElement).appendChild(s);
  } catch(e) {
    console.error("[LocalAI ISOLATED] Failed to inject content_main.js:", e);
  }

  try {
    browser.runtime.sendMessage({type: "TAB_REGISTER", url: window.location.href}).catch(() => {});
  } catch(e){}

  // Forward BG -> MAIN
  const onBgMessage = (msg) => {
    if (msg?.type === "RUN_QUERY" || msg?.type === "DOM_STATUS") {
      window.postMessage(
        {__bridge: BRIDGE, direction: "BG_TO_MAIN", payload: msg},
        "*"
      );
      return Promise.resolve({status: "received"});
    }
  };
  try { browser.runtime.onMessage.addListener(onBgMessage); } catch(e) {}

  // Forward MAIN -> BG
  const onWinMessage = (e) => {
    const data = e?.data;
    if (!data || data.__bridge !== BRIDGE) return;
    if (data.direction !== "MAIN_TO_BG") return;
    if (data.payload == null || typeof data.payload !== "object") return;
    if (typeof data.payload.type !== "string") return;
    try {
      browser.runtime.sendMessage(data.payload).catch(() => {});
    } catch (err) {}
  };
  window.addEventListener("message", onWinMessage);

  globalThis.__LOCAL_AI_BRIDGE_ISOLATED_CLEANUP__ = () => {
    try { browser.runtime.onMessage.removeListener(onBgMessage); } catch (e) {}
    try { window.removeEventListener("message", onWinMessage); } catch (e) {}
  };
})();
