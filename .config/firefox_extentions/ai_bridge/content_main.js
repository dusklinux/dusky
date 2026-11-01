// content_main.js - MAIN world - runs in page realm
// Site-adaptive bridge for Gemini, ChatGPT, Claude, Meta AI, and custom sites
//
// To add a new site, scroll to SITE DEFINITIONS and add a defineSite() call.
// Most sites only need CSS selectors — the defaults handle React/contenteditable.
// Override setInput/clickSend only for sites needing special handling (like Gemini).
(() => {
  if (typeof globalThis.__LOCAL_AI_BRIDGE_MAIN_CLEANUP__ === "function") {
    try { globalThis.__LOCAL_AI_BRIDGE_MAIN_CLEANUP__(); } catch (e) {}
  }

  const BRIDGE = "__LOCAL_AI_BRIDGE__";

  // --- State ---
  let currentRequestId = null;
  let lastFullText = "";
  let observer = null;
  let finalTimer = null;
  let focusKeepaliveTimer = null;
  let thinkingEmitted = false;
  let baselineCount = 0;
  let targetElement = null;
  let lastObservedText = "";
  let hasStartedStreaming = false;
  let lastChangeAt = 0;
  let pollTimer = null;
  let queryTimeoutTimer = null;

  // --- Messaging ---
  function emit(payload) {
    globalThis.postMessage(
      { __bridge: BRIDGE, direction: "MAIN_TO_BG", payload },
      "*"
    );
  }

  // ============================================================
  //  UTILITIES
  // ============================================================

  function isEditableNode(el) {
    if (!el) return false;
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") return true;
    if (el.isContentEditable) return true;
    if (el.hasAttribute && el.hasAttribute("contenteditable") && el.getAttribute("contenteditable") !== "false") return true;
    if (el.classList && (el.classList.contains("ql-editor") || el.classList.contains("ProseMirror"))) return true;
    if (el.getAttribute && el.getAttribute("role") === "textbox") return true;
    return false;
  }

  function isVisible(el) {
    if (!el) return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
      if (el.getAttribute("aria-hidden") === "true" || el.hasAttribute("hidden")) return false;
      const rect = el.getBoundingClientRect();
      if (rect.width < 1 && rect.height < 1) return false;
      return true;
    } catch (e) {
      return !!el;
    }
  }

  function selectAndFocus(el) {
    if (!el) return;
    try { el.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, composed: true, view: window })); } catch {}
    try { el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, composed: true, view: window })); } catch {}
    try { el.focus({ preventScroll: true }); } catch {}
    try { el.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, composed: true, view: window })); } catch {}
    try { el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, composed: true, view: window })); } catch {}
    try { el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, composed: true, view: window })); } catch {}
    try {
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } catch {}
  }

  function hasUnclosedCodeBlock(txt) {
    if (!txt) return false;
    return (txt.match(/```/g) || []).length % 2 !== 0;
  }

  function readResponseText(el) {
    if (!el) return "";
    const body = el.querySelector?.("message-content") ||
                 el.querySelector?.(".markdown") ||
                 el.querySelector?.(".font-claude-message") ||
                 el.querySelector?.(".prose") ||
                 el;
    const txt = body.innerText || body.textContent || "";
    return txt.replace(/\r\n/g, "\n").trim();
  }

  // ============================================================
  //  SITE ADAPTER REGISTRY
  // ============================================================
  //
  // Config fields (all optional except name):
  //   name                        - Display name for logging
  //   editorSelector              - CSS selector(s) for input editor (comma-separated)
  //   sendButtonSelector          - CSS selector(s) for send button (comma-separated)
  //   stopButtonSelector          - CSS selector(s) for stop/cancel button
  //   responseContainerSelector   - CSS selector(s) for AI response containers
  //   thinkingSelector            - CSS selector(s) for thinking indicators
  //   getEditor()                 - Override editor detection (default: generic search)
  //   setInput(editor, text)      - Override input method (default: execCommand + Event("input"))
  //   clickSend()                 - Override send method (default: single btn.click())
  //   isThinking()                - Override thinking detection
  //   isGenerating()              - Override generation detection
  //   completionIdleMs            - Idle ms before completion (default 1200)
  //   completionFallbackMs        - Fallback silence timeout (default 2500)
  //   completionSafetyMs          - Absolute safety timeout (default 4000)

  const SITES = {};

  function defineSite(hostPattern, config) {
    SITES[hostPattern] = { pattern: hostPattern, ...config };
  }

  function getSiteConfig() {
    const host = window.location.hostname;
    for (const [pattern, config] of Object.entries(SITES)) {
      if (pattern !== "_default" && host.includes(pattern)) return config;
    }
    return SITES._default || {};
  }

  // ============================================================
  //  DEFAULT IMPLEMENTATIONS (work for React/contenteditable sites)
  // ============================================================

  function defaultGetEditor() {
    const site = getSiteConfig();

    // 1. Try site-specific selector first
    if (site.editorSelector) {
      for (const sel of site.editorSelector.split(",")) {
        const el = document.querySelector(sel.trim());
        if (isEditableNode(el)) return el;
      }
    }

    // 2. Generic ordered candidates
    const candidates = [
      "#prompt-textarea",
      'div[data-lexical-editor="true"]',
      "div.ProseMirror",
      "div.ql-editor",
      'div[contenteditable="true"][role="textbox"]',
      'div[contenteditable="true"]',
      "[contenteditable]",
      "textarea",
    ];
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (isEditableNode(el)) return el;
    }

    // 3. Brute-force scan
    const allEditable = Array.from(document.querySelectorAll("*")).filter(isEditableNode);
    return allEditable.length > 0 ? allEditable[0] : null;
  }

  function defaultSetInput(editor, text) {
    // Works for React-based sites (ChatGPT, Claude, Meta AI).
    // Does NOT fire InputEvent with data — that causes React to double-insert text.
    if (!editor || !isEditableNode(editor)) return false;

    selectAndFocus(editor);

    let ok = false;
    try { ok = document.execCommand("selectAll", false, null); } catch {}
    try { ok = document.execCommand("insertText", false, text); } catch {}

    if (!ok || !editor.textContent?.includes(text)) {
      try {
        if (editor.tagName === "TEXTAREA" || editor.tagName === "INPUT") {
          const setter = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(editor), "value"
          )?.set;
          if (setter) setter.call(editor, text);
          else editor.value = text;
        } else if (editor.isContentEditable || editor.getAttribute("contenteditable") === "true") {
          while (editor.firstChild) editor.removeChild(editor.firstChild);
          const p = document.createElement("p");
          p.textContent = text;
          editor.appendChild(p);
        }
      } catch {
        try { editor.textContent = text; } catch {}
      }
    }

    // Generic events ONLY — no InputEvent with data (causes React double-insert)
    try { editor.dispatchEvent(new Event("input", { bubbles: true, composed: true })); } catch {}
    try { editor.dispatchEvent(new Event("change", { bubbles: true, composed: true })); } catch {}

    // Try to enable the send button
    const sendBtn = getSendButton();
    if (sendBtn) {
      sendBtn.removeAttribute("disabled");
      sendBtn.disabled = false;
      sendBtn.removeAttribute("aria-disabled");
    }

    return true;
  }

  async function defaultClickSend() {
    // Works for React sites: single btn.click() is sufficient.
    // Do NOT fire form.requestSubmit(), pointer/mouse events, or child icon
    // clicks — each one triggers a separate submission on React sites.
    const site = getSiteConfig();

    for (let i = 0; i < 50; i++) {
      const btn = getSendButton();
      if (btn && !btn.disabled) {
        try { btn.click(); } catch {}
        console.log("[LocalAI MAIN]", site.name || "site", ": single click on send button");
        return true;
      }
      await new Promise(r => setTimeout(r, 200));
    }
    return false;
  }

  // ============================================================
  //  GEMINI-SPECIFIC IMPLEMENTATIONS
  // ============================================================

  function geminiGetEditor() {
    const rich = document.querySelector("rich-textarea");
    if (rich?.shadowRoot) {
      const sEdit = rich.shadowRoot.querySelector(
        '.ql-editor, [contenteditable], p, div[role="textbox"], textarea'
      );
      if (isEditableNode(sEdit)) return sEdit;
      for (const el of rich.shadowRoot.querySelectorAll("*")) {
        if (isEditableNode(el)) return el;
      }
    }
    if (rich) {
      const lEdit = rich.querySelector(
        '.ql-editor, [contenteditable], p, div[role="textbox"], textarea'
      );
      if (isEditableNode(lEdit)) return lEdit;
      for (const el of rich.querySelectorAll("*")) {
        if (isEditableNode(el)) return el;
      }
    }
    return defaultGetEditor();
  }

  function geminiSetInput(editor, text) {
    // Gemini's Angular framework requires the FULL InputEvent chain with data.
    // This is the opposite of React sites where InputEvent(data) causes duplication.
    if (!editor) return false;

    const rich = document.querySelector("rich-textarea");
    if (rich && rich !== editor) selectAndFocus(rich);
    selectAndFocus(editor);

    let ok = false;
    try { ok = document.execCommand("selectAll", false, null); } catch {}
    try { ok = document.execCommand("insertText", false, text); } catch {}

    const targets = [editor];
    if (rich && !targets.includes(rich)) targets.push(rich);

    for (const el of targets) {
      if (!ok || !el.textContent?.includes(text)) {
        try {
          if (el.tagName === "P") el.textContent = text;
          else if (el.tagName === "RICH-TEXTAREA") {
            try { if (el.value !== undefined) el.value = text; } catch {}
          } else {
            el.innerHTML = "<p>" + text + "</p>";
          }
        } catch { try { el.textContent = text; } catch {} }
        try { if (el.value !== undefined) el.value = text; } catch {}
      }

      // Angular NEEDS the full InputEvent chain with data field
      try { el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, composed: true, inputType: "insertText", data: text })); } catch {}
      try { el.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, composed: true, inputType: "insertText", data: text })); } catch {}
      try { el.dispatchEvent(new Event("input", { bubbles: true, composed: true })); } catch {}
      try { el.dispatchEvent(new Event("change", { bubbles: true, composed: true })); } catch {}
      try { el.dispatchEvent(new KeyboardEvent("keydown", { key: "a", code: "KeyA", bubbles: true, cancelable: true, composed: true })); } catch {}
      try { el.dispatchEvent(new KeyboardEvent("keyup", { key: "a", code: "KeyA", bubbles: true, cancelable: true, composed: true })); } catch {}
    }

    // Force-enable all send buttons
    for (const btn of document.querySelectorAll("button")) {
      const label = (btn.getAttribute("aria-label") || "").toLowerCase();
      if (label.includes("send") || btn.classList.contains("send-button")) {
        btn.removeAttribute("disabled");
        btn.disabled = false;
        btn.removeAttribute("aria-disabled");
      }
    }
    return true;
  }

  function triggerAngularComponentSubmit() {
    const targets = [
      document.querySelector("rich-textarea"),
      document.querySelector("button.send-button"),
      document.querySelector('button[aria-label*="Send"]'),
      document.querySelector("chat-window"),
      document.querySelector("model-response"),
      document.body,
    ];
    for (const el of targets) {
      if (!el) continue;
      if (window.ng && window.ng.getComponent) {
        try {
          const comp = window.ng.getComponent(el);
          if (comp) {
            const sendFn = comp.sendMessage || comp.onSubmit || comp.send || comp.onSendClick || comp.submit;
            if (typeof sendFn === "function") { sendFn.call(comp); return true; }
          }
        } catch {}
      }
      try {
        for (const k in el) {
          if (k.startsWith("__ng") || k.includes("Angular")) {
            const ctx = el[k];
            if (ctx) {
              const fn = ctx.sendMessage || ctx.onSubmit || ctx.send || ctx.onSendClick;
              if (typeof fn === "function") { fn.call(ctx); return true; }
            }
          }
        }
      } catch {}
    }
    return false;
  }

  async function geminiClickSend() {
    // Gemini ONLY accepts isTrusted:true events for form submission.
    // Synthetic clicks (isTrusted:false) either fail silently on first prompt
    // or SUCCEED and cause duplicate submissions on subsequent prompts when
    // Angular is warm. The ONLY reliable submission is wtype hardware Return
    // from bridge.py daemon at T+1.5s. So we just focus the editor and wait.

    const editor = getEditor();
    const focusEditor = () => {
      if (!editor) return;
      let el = editor;
      if (el.tagName === "RICH-TEXTAREA") {
        el = el.querySelector('div[contenteditable="true"], p, div.ql-editor') || el;
      }
      selectAndFocus(el);
    };

    focusEditor();

    // Wait for send button to appear (confirms text was accepted by Angular)
    for (let i = 0; i < 25; i++) {
      const btn = getSendButton();
      if (btn && !btn.disabled) {
        console.log("[LocalAI MAIN] Gemini: send button ready — wtype will submit");
        focusEditor(); // Re-focus editor for wtype hardware Return
        return true;
      }
      await new Promise(r => setTimeout(r, 200));
    }

    // Re-focus even if button not found — wtype might still work
    console.log("[LocalAI MAIN] Gemini: send button not found — relying on wtype");
    focusEditor();
    return true;
  }

  // ============================================================
  //  CHATGPT-SPECIFIC IMPLEMENTATIONS (Lexical & React)
  // ============================================================

  function chatgptSetInput(editor, text) {
    if (!editor || !isEditableNode(editor)) return false;
    selectAndFocus(editor);

    // 1. Try paste event with DataTransfer (primary path for Lexical)
    try {
      const dt = new DataTransfer();
      dt.setData("text/plain", text);
      const pasteEv = new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        composed: true,
        clipboardData: dt,
      });
      editor.dispatchEvent(pasteEv);
    } catch (e) {}

    // 2. Try beforeinput with insertText and insertFromPaste
    if (!editor.textContent || !editor.textContent.includes(text)) {
      try {
        const dt = new DataTransfer();
        dt.setData("text/plain", text);
        const ev = new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          composed: true,
          inputType: "insertFromPaste",
          dataTransfer: dt,
        });
        editor.dispatchEvent(ev);
      } catch (e) {}
    }

    if (!editor.textContent || !editor.textContent.includes(text)) {
      try {
        editor.dispatchEvent(new InputEvent("beforeinput", {
          bubbles: true,
          cancelable: true,
          composed: true,
          inputType: "insertText",
          data: text,
        }));
      } catch (e) {}
    }

    // 3. Try document.execCommand
    if (!editor.textContent || !editor.textContent.includes(text)) {
      try { document.execCommand("selectAll", false, null); } catch {}
      try { document.execCommand("insertText", false, text); } catch {}
    }

    // 4. Fallback: manual paragraph if empty
    if (!editor.textContent || !editor.textContent.includes(text)) {
      try {
        while (editor.firstChild) editor.removeChild(editor.firstChild);
        const p = document.createElement("p");
        p.textContent = text;
        editor.appendChild(p);
      } catch {}
    }

    // 5. Fire standard input events
    try { editor.dispatchEvent(new Event("input", { bubbles: true, composed: true })); } catch {}
    try { editor.dispatchEvent(new Event("change", { bubbles: true, composed: true })); } catch {}

    // Force enable send buttons
    const btn = getSendButton();
    if (btn) {
      btn.removeAttribute("disabled");
      btn.disabled = false;
      btn.removeAttribute("aria-disabled");
    }

    return true;
  }

  async function chatgptClickSend() {
    const editor = getEditor();

    // 1. Wait for send button to be enabled by React
    for (let i = 0; i < 15; i++) {
      const btn = getSendButton();
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
        btn.click();
        console.log("[LocalAI MAIN] ChatGPT: send button clicked");
        return true;
      }
      await new Promise(r => setTimeout(r, 100));
    }

    // 2. Direct Enter keydown dispatch on editor
    if (editor) {
      selectAndFocus(editor);
      try {
        editor.dispatchEvent(new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          cancelable: true,
          composed: true,
        }));
        console.log("[LocalAI MAIN] ChatGPT: dispatched Enter on editor");
        return true;
      } catch (e) {}
    }

    // 3. Fallback click
    const btn = getSendButton();
    if (btn) {
      try {
        btn.removeAttribute("disabled");
        btn.disabled = false;
        btn.removeAttribute("aria-disabled");
        btn.click();
      } catch {}
      return true;
    }
    return false;
  }

  // ============================================================
  //  SITE DEFINITIONS
  //
  //  To add a new site, copy one of the blocks below and adjust
  //  the selectors. For most React-based sites, just selectors
  //  are enough — the defaults handle the rest.
  // ============================================================

  defineSite("gemini.google.com", {
    name: "Gemini",
    editorSelector: 'rich-textarea .ql-editor, rich-textarea [contenteditable], rich-textarea p',
    sendButtonSelector: 'button.send-button, button[aria-label*="Send prompt"], button[aria-label*="Send message"], button[aria-label*="Send"]',
    stopButtonSelector: 'button.stop-button, .send-button-container button[aria-label*="Stop"], rich-textarea ~ * button[aria-label*="Stop"]',
    responseContainerSelector: "model-response",
    thinkingSelector: '.thinking-indicator, [aria-label*="Thinking"]',
    getEditor: geminiGetEditor,
    setInput: geminiSetInput,
    clickSend: geminiClickSend,
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  defineSite("chatgpt.com", {
    name: "ChatGPT",
    editorSelector: '#prompt-textarea, div[data-lexical-editor="true"], div[contenteditable="true"]',
    sendButtonSelector: 'button[data-testid="send-button"], button[aria-label*="Send prompt"], button[aria-label*="Send message"], button[aria-label*="Send"]',
    stopButtonSelector: 'button[data-testid="stop-button"], button[aria-label*="Stop generating"], button[aria-label*="Stop"]',
    responseContainerSelector: 'article[data-testid*="conversation-turn"], div[data-message-author-role="assistant"], div[class*="agent-turn"]',
    thinkingSelector: '.result-thinking, [data-testid="thinking-animation"], [data-testid="thought"], [data-testid="reasoning-expander"]',
    setInput: chatgptSetInput,
    clickSend: chatgptClickSend,
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  // Legacy OpenAI domain alias
  defineSite("chat.openai.com", {
    name: "ChatGPT (Legacy)",
    editorSelector: '#prompt-textarea, div[data-lexical-editor="true"], div[contenteditable="true"]',
    sendButtonSelector: 'button[data-testid="send-button"], button[aria-label*="Send prompt"], button[aria-label*="Send message"], button[aria-label*="Send"]',
    stopButtonSelector: 'button[data-testid="stop-button"], button[aria-label*="Stop generating"], button[aria-label*="Stop"]',
    responseContainerSelector: 'article[data-testid*="conversation-turn"], div[data-message-author-role="assistant"], div[class*="agent-turn"]',
    thinkingSelector: '.result-thinking, [data-testid="thinking-animation"], [data-testid="thought"], [data-testid="reasoning-expander"]',
    setInput: chatgptSetInput,
    clickSend: chatgptClickSend,
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  defineSite("claude.ai", {
    name: "Claude",
    editorSelector: 'div.ProseMirror[contenteditable="true"], div[contenteditable="true"], fieldset div[contenteditable="true"]',
    sendButtonSelector: 'button[aria-label*="Send"], button[data-testid*="send"], button[aria-label*="send"]',
    stopButtonSelector: 'button[aria-label*="Stop"], button[aria-label*="stop"]',
    responseContainerSelector: '.font-claude-message, [data-is-streaming="true"], div[data-message-author-role="assistant"]',
    thinkingSelector: '[data-is-thinking="true"], .thinking-indicator, [data-testid="thought"]',
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  defineSite("meta.ai", {
    name: "Meta AI",
    editorSelector: 'div[contenteditable="true"][role="textbox"], div[contenteditable="true"], textarea',
    sendButtonSelector: 'button[data-testid="composer-send-button"], button[aria-label*="Send"], button[aria-label*="send"], button[type="submit"], div[role="textbox"] ~ * button, form button',
    stopButtonSelector: 'button[aria-label*="Stop"], button[aria-label*="stop"]',
    responseContainerSelector: 'div[role="article"], div[data-testid*="message"], div[aria-label*="Meta AI"], div[class*="response-message"], div[class*="message-text"]',
    thinkingSelector: '[aria-label*="Thinking"], button[aria-label*="Thinking"]',
    completionIdleMs: 1500,
    completionFallbackMs: 3000,
    completionSafetyMs: 5000,
  });

  // Fallback for unrecognized AI sites
  defineSite("_default", {
    name: "Unknown AI",
    sendButtonSelector: 'button[aria-label*="Send"], button.send-button',
    stopButtonSelector: 'button[aria-label*="Stop"]',
    responseContainerSelector: 'div[role="article"], div[data-message-author-role="assistant"]',
    thinkingSelector: "",
    completionIdleMs: 1500,
    completionFallbackMs: 3000,
    completionSafetyMs: 5000,
  });

  // ============================================================
  //  ADAPTER DISPATCH FUNCTIONS
  // ============================================================

  function getEditor() {
    const site = getSiteConfig();
    if (site.getEditor) return site.getEditor();
    return defaultGetEditor();
  }

  function setInput(editor, text) {
    const site = getSiteConfig();
    if (site.setInput) return site.setInput(editor, text);
    return defaultSetInput(editor, text);
  }

  async function clickSend() {
    if (isGenerating()) return false;
    const site = getSiteConfig();
    if (site.clickSend) return site.clickSend();
    return defaultClickSend();
  }

  function getSendButton() {
    const site = getSiteConfig();

    // 1. Site-specific selector
    if (site.sendButtonSelector) {
      for (const sel of site.sendButtonSelector.split(",")) {
        const btn = document.querySelector(sel.trim());
        if (btn && isVisible(btn)) return btn;
      }
    }

    // 2. Shadow DOM check (Gemini rich-textarea)
    const rich = document.querySelector("rich-textarea");
    if (rich?.shadowRoot) {
      const sBtn = rich.shadowRoot.querySelector('button[aria-label*="Send"], button.send-button, button');
      if (sBtn && isVisible(sBtn)) return sBtn;
    }

    // 3. Prompt bar container scan (exclude non-send buttons)
    const promptParent = rich?.parentElement || document.querySelector("form") || document.querySelector(".input-area-container");
    if (promptParent) {
      for (const b of promptParent.querySelectorAll("button")) {
        if (!isVisible(b)) continue;
        const label = (b.getAttribute("aria-label") || b.textContent || "").toLowerCase();
        if (label.includes("mic") || label.includes("voice") || label.includes("upload") ||
            label.includes("add") || label.includes("stop") || label.includes("cancel")) continue;
        return b;
      }
    }

    // 4. Global fallback
    for (const btn of document.querySelectorAll("button")) {
      if (!isVisible(btn)) continue;
      const label = (btn.getAttribute("aria-label") || "").toLowerCase();
      if (label.includes("send") || label.includes("submit") || btn.classList.contains("send-button")) {
        return btn;
      }
    }
    return null;
  }

  function getResponseContainers() {
    const site = getSiteConfig();
    if (site.responseContainerSelector) {
      const result = Array.from(document.querySelectorAll(site.responseContainerSelector));
      if (result.length > 0) return result;
    }
    // Universal fallback cascade matching popular AI site DOMs
    const fallbacks = [
      'div[role="article"]',
      'div[data-testid*="message"]',
      'div[aria-label*="Meta AI"]',
      'model-response',
      '.font-claude-message',
      '[data-is-streaming="true"]',
      'div[data-message-author-role="assistant"]',
      'div[class*="message-content"]',
      'div[class*="assistant-message"]'
    ];
    for (const sel of fallbacks) {
      const els = Array.from(document.querySelectorAll(sel));
      if (els.length > 0) return els;
    }
    return [];
  }

  function siteIsThinking() {
    const site = getSiteConfig();

    // 1. Custom override
    if (site.isThinking) return site.isThinking();

    // 2. CSS selector check
    if (site.thinkingSelector) {
      for (const sel of site.thinkingSelector.split(",")) {
        const s = sel.trim();
        if (!s) continue;
        const el = document.querySelector(s);
        if (el && isVisible(el)) return true;
      }
    }

    // 3. Heuristic: stop button visible but no/minimal response text = thinking
    if (site.stopButtonSelector) {
      let stopVisible = false;
      for (const sel of site.stopButtonSelector.split(",")) {
        const stopBtn = document.querySelector(sel.trim());
        if (stopBtn && isVisible(stopBtn)) { stopVisible = true; break; }
      }
      if (stopVisible) {
        const containers = getResponseContainers();
        if (containers.length > baselineCount) {
          const last = containers[containers.length - 1];
          const text = readResponseText(last);
          if (!text || text.length < 3) return true;
        } else if (containers.length === baselineCount) {
          // No new response container yet but stop button is visible = thinking
          return true;
        }
      }
    }

    return false;
  }

  function isGenerating() {
    const site = getSiteConfig();

    // 1. Custom override
    if (site.isGenerating) return site.isGenerating();

    // 2. Send button enabled = NOT generating
    const sendBtn = getSendButton();
    if (sendBtn && !sendBtn.disabled) return false;

    // 3. Stop button visible = generating
    if (site.stopButtonSelector) {
      for (const sel of site.stopButtonSelector.split(",")) {
        const stopBtn = document.querySelector(sel.trim());
        if (stopBtn && isVisible(stopBtn)) return true;
      }
    }

    // 4. Streaming attribute fallback
    const streaming = document.querySelector('[data-is-streaming="true"], .is-streaming');
    if (streaming && isVisible(streaming)) return true;

    return false;
  }

  // ============================================================
  //  OBSERVER & COMPLETION DETECTION
  // ============================================================

  function emitFinal(reason) {
    if (!currentRequestId || !lastFullText) return;
    emit({ type: "FINAL", full: lastFullText, requestId: currentRequestId, reason: reason || "complete" });
    currentRequestId = null;
    stopObserver();
  }

  function scheduleCompletionCheck() {
    clearTimeout(finalTimer);
    const site = getSiteConfig();
    const idleMs = site.completionIdleMs || 1200;
    const fallbackMs = site.completionFallbackMs || 2500;
    const safetyMs = site.completionSafetyMs || 4000;

    const checkCompletion = () => {
      if (!currentRequestId) return;

      // While AI is thinking, emit THINKING events and keep waiting
      if (siteIsThinking()) {
        if (!thinkingEmitted) {
          emit({ type: "THINKING", requestId: currentRequestId });
          thinkingEmitted = true;
        }
        lastChangeAt = Date.now(); // Reset idle timer
        finalTimer = setTimeout(checkCompletion, 1000);
        return;
      }

      if (!hasStartedStreaming || !lastFullText) {
        finalTimer = setTimeout(checkCompletion, 500);
        return;
      }

      const idleFor = Date.now() - lastChangeAt;
      const openCode = hasUnclosedCodeBlock(lastFullText);

      // Primary: idle + not generating + code fences closed
      if (idleFor >= idleMs && !isGenerating() && !openCode) {
        emitFinal("idle");
        return;
      }

      // Fallback: silence + code fences closed
      if (idleFor >= fallbackMs && !openCode) {
        emitFinal("silence-fallback");
        return;
      }

      // Absolute safety
      if (idleFor >= safetyMs) {
        emitFinal("absolute-safety");
        return;
      }

      finalTimer = setTimeout(checkCompletion, 200);
    };
    finalTimer = setTimeout(checkCompletion, 500);
  }

  function startObserver() {
    stopObserver();
    const containers = getResponseContainers();
    baselineCount = containers.length;
    targetElement = null;
    lastObservedText = "";
    lastFullText = "";
    hasStartedStreaming = false;
    thinkingEmitted = false;
    lastChangeAt = Date.now();

    const onDomTick = () => {
      if (!currentRequestId) return;
      const currentContainers = getResponseContainers();

      if (currentContainers.length > baselineCount) {
        const latest = currentContainers[currentContainers.length - 1];
        const latestContent = latest.querySelector("message-content") || latest;
        if (targetElement !== latestContent) {
          targetElement = latestContent;
          lastObservedText = "";
        }
      } else if (!targetElement || !document.contains(targetElement)) {
        if (currentContainers.length > 0) {
          const last = currentContainers[currentContainers.length - 1];
          targetElement = last.querySelector("message-content") || last;
          lastObservedText = readResponseText(targetElement);
        } else {
          return;
        }
      }

      const full = readResponseText(targetElement);
      if (!full) return;
      if (full === lastObservedText) return;

      let chunk = "";
      if (full.startsWith(lastObservedText)) {
        chunk = full.slice(lastObservedText.length);
      } else if (lastObservedText && lastObservedText.startsWith(full)) {
        // Transient shrink / rewrite mid-stream
        lastObservedText = full;
        lastFullText = full;
        lastChangeAt = Date.now();
        scheduleCompletionCheck();
        return;
      } else {
        // Full re-render: longest common prefix diff
        let i = 0;
        const max = Math.min(lastObservedText.length, full.length);
        while (i < max && lastObservedText.charCodeAt(i) === full.charCodeAt(i)) i++;
        chunk = full.slice(i);
        if (i < Math.min(12, lastObservedText.length) && lastObservedText.length > 0) {
          chunk = full;
        }
      }

      lastObservedText = full;
      lastFullText = full;
      lastChangeAt = Date.now();

      if (chunk) {
        hasStartedStreaming = true;
        emit({ type: "STREAM_CHUNK", text: chunk, full: full, requestId: currentRequestId });
      }

      scheduleCompletionCheck();
    };

    observer = new MutationObserver(onDomTick);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, characterDataOldValue: true });
    pollTimer = setInterval(onDomTick, 500);
    scheduleCompletionCheck();
  }

  function stopObserver() {
    if (observer) { observer.disconnect(); observer = null; }
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    clearTimeout(finalTimer);
  }

  // ============================================================
  //  QUERY HANDLING
  // ============================================================

  const handleQuery = async (msg) => {
    if (!msg || msg.type !== "RUN_QUERY") return;

    // --- BUSY GUARD: If AI is still generating/thinking from a previous query,
    // stop it cleanly before accepting the new one. ---
    if (currentRequestId && (isGenerating() || siteIsThinking() || hasStartedStreaming)) {
      const oldReqId = currentRequestId;
      console.log(`[LocalAI MAIN] Busy guard: AI still active for ${oldReqId}, stopping before new query`);

      // 1. Emit FINAL with whatever we have so the old CLI client can exit cleanly
      emit({ type: "FINAL", full: lastFullText || "", requestId: oldReqId, reason: "interrupted" });
      stopObserver();

      // 2. Click the stop button to halt current generation
      const site = getSiteConfig();
      let stopped = false;
      if (site.stopButtonSelector) {
        for (const sel of site.stopButtonSelector.split(",")) {
          const stopBtn = document.querySelector(sel.trim());
          if (stopBtn && isVisible(stopBtn)) {
            stopBtn.click();
            stopped = true;
            console.log("[LocalAI MAIN] Clicked stop button to halt generation");
            break;
          }
        }
      }

      // 3. Wait for AI to settle (stop button disappears, editor becomes available)
      //    Max 10 seconds — if it doesn't settle, proceed anyway
      const settleStart = Date.now();
      while (Date.now() - settleStart < 10000) {
        await new Promise(r => setTimeout(r, 300));
        const stillGenerating = isGenerating() || siteIsThinking();
        if (!stillGenerating) {
          console.log(`[LocalAI MAIN] AI settled after ${Date.now() - settleStart}ms`);
          break;
        }
      }

      // Small extra pause for the UI to fully update after stopping
      await new Promise(r => setTimeout(r, 500));
    }

    // --- Reset state for new query ---
    currentRequestId = msg.requestId || crypto.randomUUID();
    lastFullText = "";
    lastObservedText = "";
    hasStartedStreaming = false;
    thinkingEmitted = false;

    const query = msg.query || "";
    const site = getSiteConfig();

    // Poll for editor up to 5s (SPA pages may still be rendering)
    let editor = getEditor();
    if (!editor) {
      for (let i = 0; i < 25; i++) {
        await new Promise(r => setTimeout(r, 200));
        editor = getEditor();
        if (editor) break;
      }
    }

    if (!editor) {
      console.log("[LocalAI MAIN] Error: Editor element not found after polling!");
      emit({ type: "ERROR", error: "Editor not found - is AI page loaded?", requestId: currentRequestId });
      return;
    }

    startObserver();

    const ok = setInput(editor, query);
    if (!ok) {
      emit({ type: "ERROR", error: "Failed to set input", requestId: currentRequestId });
      stopObserver();
      return;
    }

    setTimeout(async () => {
      await clickSend();
    }, 250);

    // Thinking-aware safety timeout:
    // - While AI is thinking or generating, keep waiting (up to 30 minutes)
    // - If nothing is happening for 15 seconds, timeout
    clearTimeout(queryTimeoutTimer);
    let quietSeconds = 0;
    const maxThinkingSeconds = 1800; // 30 minutes
    let totalSeconds = 0;

    const checkQueryTimeout = () => {
      if (!currentRequestId || currentRequestId !== msg.requestId) return;
      if (hasStartedStreaming) return; // Streaming started, completion check handles it

      totalSeconds++;

      // If AI is thinking or generating, keep waiting
      if (siteIsThinking() || isGenerating()) {
        quietSeconds = 0; // Reset quiet timer
        // Emit THINKING periodically so CLI client doesn't idle-timeout
        if (totalSeconds % 5 === 0) {
          emit({ type: "THINKING", requestId: currentRequestId });
        }
        if (totalSeconds < maxThinkingSeconds) {
          queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);
          return;
        }
        // Exceeded max thinking time
        console.log("[LocalAI MAIN] Query exceeded max thinking time (30 min)");
        emit({ type: "ERROR", error: "Exceeded maximum thinking time (30 min)", requestId: currentRequestId });
        currentRequestId = null;
        stopObserver();
        return;
      }

      // Not thinking, not generating — count quiet seconds
      quietSeconds++;
      if (quietSeconds > 15) {
        console.log("[LocalAI MAIN] Query response timeout - no activity for 15s");
        emit({ type: "ERROR", error: "Timeout waiting for AI response to start", requestId: currentRequestId });
        currentRequestId = null;
        stopObserver();
        return;
      }

      queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);
    };
    queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);

    // Focus keep-alive: re-focus editor every 200ms for 3s so that when
    // wtype sends hardware Return at ~T=1.9s, the editor has keyboard focus.
    // Without this, focus drifts to the response area after Prompt 1 completes.
    if (focusKeepaliveTimer) {
      clearInterval(focusKeepaliveTimer);
      focusKeepaliveTimer = null;
    }
    let focusKeepaliveCount = 0;
    focusKeepaliveTimer = setInterval(() => {
      focusKeepaliveCount++;
      if (focusKeepaliveCount > 15 || !currentRequestId || hasStartedStreaming) {
        clearInterval(focusKeepaliveTimer);
        focusKeepaliveTimer = null;
        return;
      }
      const ed = getEditor();
      if (ed) {
        let el = ed;
        if (el.tagName === "RICH-TEXTAREA") {
          el = el.querySelector('div[contenteditable="true"], p, div.ql-editor') || el;
        }
        try { el.focus({ preventScroll: true }); } catch {}
      }
    }, 200);
  };

  // ============================================================
  //  MESSAGE LISTENER
  // ============================================================

  const onPageMessage = (e) => {
    const data = e?.data;
    if (!data || data.__bridge !== BRIDGE) return;
    if (data.direction !== "BG_TO_MAIN") return;
    const msg = data.payload;
    const site = getSiteConfig();
    console.log("[LocalAI MAIN]", site.name || "Unknown", "- Received:", msg?.type, msg?.query?.substring(0, 30));
    if (msg?.type === "RUN_QUERY") handleQuery(msg);
  };

  globalThis.addEventListener("message", onPageMessage);

  globalThis.__LOCAL_AI_BRIDGE_MAIN_CLEANUP__ = () => {
    stopObserver();
    clearTimeout(queryTimeoutTimer);
    clearInterval(focusKeepaliveTimer);
    try { globalThis.removeEventListener("message", onPageMessage); } catch (e) {}
  };

  const site = getSiteConfig();
  console.log("[LocalAI MAIN] Injected - ready for queries -", site.name || "Unknown site", "adapter loaded");
})();
