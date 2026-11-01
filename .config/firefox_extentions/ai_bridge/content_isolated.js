(function () {
  if (typeof globalThis.__LOCAL_AI_BRIDGE_ISOLATED_CLEANUP__ === "function") {
    try { globalThis.__LOCAL_AI_BRIDGE_ISOLATED_CLEANUP__(); } catch (e) {}
  }

  const BRIDGE = "__LOCAL_AI_BRIDGE__";
  const RELAY_TYPES = new Set(["RUN_QUERY", "STOP_QUERY", "DOM_STATUS"]);

  try {
    browser.runtime.sendMessage({ type: "TAB_REGISTER", url: window.location.href }).catch(() => {});
  } catch (e) {}

  // Forward BG -> MAIN strictly via window.postMessage
  const onBgMessage = (msg) => {
    if (msg?.type === "PING_READY") {
      const hasEditor = Boolean(
        document.querySelector('rich-textarea, div#prompt-textarea, div[contenteditable="true"], div.ql-editor')
      );
      return Promise.resolve({ status: "ready", editorHydrated: hasEditor });
    }
    if (msg && RELAY_TYPES.has(msg.type)) {
      window.postMessage(
        { __bridge: BRIDGE, direction: "BG_TO_MAIN", payload: msg },
        "*"
      );
      return Promise.resolve({ status: "received" });
    }
  };
  try { browser.runtime.onMessage.addListener(onBgMessage); } catch (e) {}

  // Forward MAIN -> BG
  const onWinMessage = (e) => {
    if (e.source !== window) return;
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
