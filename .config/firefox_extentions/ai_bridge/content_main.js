// content_main.js - MAIN world - runs in page realm
// Site-adaptive bridge for Gemini, ChatGPT, Claude, Meta AI, and custom sites
//
// Dual-realm MV3: content_isolated.js forwards messages to/from this script via postMessage.
// Handles React 19, Lexical, ProseMirror, Angular rich-textarea, and contenteditable DOMs.
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

  // --- Messaging (strictly through postMessage bridge to isolated content script) ---
  function emit(payload) {
    try {
      globalThis.postMessage(
        { __bridge: BRIDGE, direction: "MAIN_TO_BG", payload },
        "*"
      );
    } catch (e) {}
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
    const THINKING_ANCESTOR_SEL =
      '.result-thinking, [data-testid*="thinking"], [data-testid*="thought"], [data-testid*="reasoning"], .thinking-indicator, details';

    // If el itself is a markdown/prose/content node, read it directly unless inside a thinking block
    if (
      el.matches?.(".markdown, .prose, message-content, .font-claude-message") &&
      !el.closest?.(THINKING_ANCESTOR_SEL)
    ) {
      const clone = el.cloneNode(true);
      clone.querySelectorAll?.(THINKING_ANCESTOR_SEL).forEach((n) => n.remove());
      return (clone.innerText || clone.textContent || "").replace(/\r\n/g, "\n").trim();
    }

    // Find all candidate content blocks inside the container, excluding thinking drawers
    const candidates = Array.from(
      el.querySelectorAll?.(".markdown, .prose, message-content, .font-claude-message") || []
    ).filter((node) => !node.closest?.(THINKING_ANCESTOR_SEL));

    if (candidates.length > 0) {
      const parts = candidates
        .map((node) => {
          const clone = node.cloneNode(true);
          clone.querySelectorAll?.(THINKING_ANCESTOR_SEL).forEach((n) => n.remove());
          clone.querySelectorAll?.('h4[data-conversation-role], h4.sr-only, h5.sr-only, h6.sr-only, .sr-only').forEach((n) => n.remove());
          return (clone.innerText || clone.textContent || "").replace(/\r\n/g, "\n").trim();
        })
        .filter(Boolean);
      return parts.join("\n\n").trim();
    }

    const clone = el.cloneNode(true);
    clone.querySelectorAll?.(THINKING_ANCESTOR_SEL).forEach((n) => n.remove());
    clone.querySelectorAll?.('h4[data-conversation-role], h4.sr-only, h5.sr-only, h6.sr-only, .sr-only').forEach((n) => n.remove());
    let txt = clone.innerText || clone.textContent || "";
    txt = txt.replace(/^ChatGPT said:\s*ChatGPT\s*/i, "").trim();
    if (/^(thought for\s+\d+|thinking\.\.\.)/i.test(txt)) return "";
    return txt.replace(/\r\n/g, "\n").trim();
  }

  // ============================================================
  //  SITE ADAPTER REGISTRY
  // ============================================================

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

    // 1. Try site-specific selector first (visible nodes prioritized)
    if (site.editorSelector) {
      for (const sel of site.editorSelector.split(",")) {
        const els = document.querySelectorAll(sel.trim());
        for (const el of els) {
          if (isEditableNode(el) && isVisible(el)) return el;
        }
      }
    }

    // 2. Generic ordered candidates (visible nodes prioritized)
    const candidates = [
      'div#prompt-textarea[contenteditable="true"]',
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
      for (const el of document.querySelectorAll(sel)) {
        if (isEditableNode(el) && isVisible(el)) return el;
      }
    }

    // 3. Fallback to non-visible editable if window is minimized/unrendered
    if (site.editorSelector) {
      for (const sel of site.editorSelector.split(",")) {
        const el = document.querySelector(sel.trim());
        if (isEditableNode(el)) return el;
      }
    }

    const allEditable = Array.from(document.querySelectorAll("*")).filter(
      (el) => isEditableNode(el) && isVisible(el)
    );
    return allEditable.length > 0 ? allEditable[0] : null;
  }

  function defaultSetInput(editor, text) {
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

    try { editor.dispatchEvent(new Event("input", { bubbles: true, composed: true })); } catch {}
    try { editor.dispatchEvent(new Event("change", { bubbles: true, composed: true })); } catch {}

    const sendBtn = getSendButton();
    if (sendBtn) {
      sendBtn.removeAttribute("disabled");
      sendBtn.disabled = false;
      sendBtn.removeAttribute("aria-disabled");
    }

    return true;
  }

  async function defaultClickSend() {
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

      try { el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, composed: true, inputType: "insertText", data: text })); } catch {}
      try { el.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, composed: true, inputType: "insertText", data: text })); } catch {}
      try { el.dispatchEvent(new Event("input", { bubbles: true, composed: true })); } catch {}
      try { el.dispatchEvent(new Event("change", { bubbles: true, composed: true })); } catch {}
      try { el.dispatchEvent(new KeyboardEvent("keydown", { key: "a", code: "KeyA", bubbles: true, cancelable: true, composed: true })); } catch {}
      try { el.dispatchEvent(new KeyboardEvent("keyup", { key: "a", code: "KeyA", bubbles: true, cancelable: true, composed: true })); } catch {}
    }

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

  async function geminiClickSend() {
    const focusEditor = () => {
      const ed = getEditor();
      if (!ed) return null;
      let el = ed;
      if (el.tagName === "RICH-TEXTAREA") {
        el = el.querySelector('div[contenteditable="true"], div.ql-editor, p') || el;
      }
      selectAndFocus(el);
      return el;
    };

    // Wait for any pending Gemini attachment upload spinners
    for (let w = 0; w < 40; w++) {
      const spinner = document.querySelector(
        'mat-progress-spinner, .uploader-spinner, [role="progressbar"][aria-busy="true"]'
      );
      if (!spinner || !isVisible(spinner)) break;
      await new Promise((r) => setTimeout(r, 200));
    }

    focusEditor();

    for (let i = 0; i < 20; i++) {
      const btn = getSendButton();
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
        try {
          btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, composed: true }));
          btn.click();
        } catch {}

        await new Promise((r) => setTimeout(r, 220));
        const currentEd = getEditor();
        if (!readEditorCleanText(currentEd) || isGenerating() || siteIsThinking()) {
          console.log("[LocalAI MAIN] Gemini: JS send button click succeeded without OS Return");
          emit({ type: "SUBMISSION_CONFIRMED", requestId: currentRequestId });
          return true;
        }
        break;
      }
      await new Promise((r) => setTimeout(r, 120));
    }

    console.log("[LocalAI MAIN] Gemini: requesting deterministic hardware Return via NEED_OS_RETURN");
    focusEditor();
    emit({ type: "NEED_OS_RETURN", requestId: currentRequestId });
    return true;
  }

  // ============================================================
  //  CHATGPT-SPECIFIC IMPLEMENTATIONS (ProseMirror / Lexical & React 19)
  // ============================================================

  function chatgptGetEditor() {
    const selectors = [
      'div#prompt-textarea[contenteditable="true"]',
      'div[data-lexical-editor="true"][contenteditable="true"]',
      'div.ProseMirror[contenteditable="true"]',
      '#prompt-textarea[contenteditable="true"]',
      'form div[contenteditable="true"]',
      "#prompt-textarea",
    ];
    for (const sel of selectors) {
      for (const el of document.querySelectorAll(sel)) {
        if (isEditableNode(el) && isVisible(el)) return el;
      }
    }
    return defaultGetEditor();
  }

  function readEditorCleanText(editor) {
    if (!editor) return "";
    if (editor.tagName === "TEXTAREA" || editor.tagName === "INPUT") {
      return (editor.value || "").replace(/\r\n/g, "\n").trim();
    }
    const raw = (editor.innerText || editor.textContent || "").replace(/\u200B/g, "");
    return raw.replace(/\r\n/g, "\n").trim();
  }

  let lastInjectedQueryText = "";

  function findProseMirrorView(editor) {
    if (!editor) return null;
    // 1. Direct or descendant pmViewDesc lookup
    const candidates = [editor, ...Array.from(editor.querySelectorAll("*"))];
    for (const node of candidates) {
      const view = node?.pmViewDesc?.view || node?.__proseMirrorView || node?.editorView;
      if (view && view.state && typeof view.dispatch === "function") {
        return view;
      }
    }
    // 2. React 19 Fiber traversal (up to 10 parent fibers)
    const fiberKey = Object.keys(editor).find(
      (k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$")
    );
    if (fiberKey) {
      let fiber = editor[fiberKey];
      for (let depth = 0; fiber && depth < 10; depth++, fiber = fiber.return) {
        const memo = fiber.memoizedState;
        for (let s = memo, step = 0; s && step < 15; s = s.next, step++) {
          const val = s.memoizedState;
          const view = val?.view || val?.current?.view || val?.editor?.view || val;
          if (view && view.state && typeof view.dispatch === "function" && view.dom) {
            return view;
          }
        }
      }
    }
    return null;
  }

  function chatgptSetInput(editor, text) {
    if (!editor || !isEditableNode(editor)) return false;
    const normalizedTarget = text.replace(/\r\n/g, "\n").trim();
    lastInjectedQueryText = text;

    selectAndFocus(editor);

    if (editor.tagName === "TEXTAREA" || editor.tagName === "INPUT") {
      try {
        const setter = Object.getOwnPropertyDescriptor(
          Object.getPrototypeOf(editor),
          "value"
        )?.set;
        if (setter) setter.call(editor, text);
        else editor.value = text;
        editor.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        editor.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
        return true;
      } catch (e) {}
    }

    // Stage A: Schema-valid ProseMirror EditorView Transaction (works 100% unfocused on Wayland)
    try {
      const pmView = findProseMirrorView(editor);
      if (pmView && pmView.state && pmView.dispatch) {
        const { state } = pmView;
        const schema = state.schema;
        const lines = text.replace(/\r\n/g, "\n").split("\n");
        const pType = schema.nodes.paragraph || schema.nodes.text_block;
        let tr;
        if (pType) {
          const nodes = lines.map((line) =>
            line.length > 0 ? pType.create(null, schema.text(line)) : pType.create()
          );
          tr = state.tr.replaceWith(0, state.doc.content.size, nodes);
        } else {
          const from = state.doc.content.size >= 2 ? 1 : 0;
          const to = Math.max(from, state.doc.content.size - 1);
          tr = state.tr.insertText(text, from, to);
        }
        pmView.dispatch(tr.scrollIntoView());
        editor.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
        if (readEditorCleanText(editor) === normalizedTarget) {
          return true;
        }
      }
    } catch (e) {
      console.warn("[LocalAI MAIN] ChatGPT Stage A ProseMirror transaction warning:", e);
    }

    // Stage B: Synthetic ClipboardEvent("paste") — Native ProseMirror & Lexical Paste Pipeline
    // ProseMirror's editHandlers.paste reads clipboardData.getData("text/plain") and dispatches
    // an internal transaction even when the browser window does not have OS focus.
    try {
      const p = editor.querySelector("p") || editor;
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.selectNodeContents(p);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      const dt = new DataTransfer();
      dt.setData("text/plain", text);
      const pasteEvent = new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        composed: true,
        clipboardData: dt,
      });
      editor.dispatchEvent(pasteEvent);
      if (readEditorCleanText(editor) === normalizedTarget) {
        return true;
      }
    } catch (e) {}

    // Stage C: Native execCommand("insertText") fallback
    try {
      const p = editor.querySelector("p") || editor;
      const sel = window.getSelection();
      if (sel) {
        const range = document.createRange();
        range.selectNodeContents(p);
        sel.removeAllRanges();
        sel.addRange(range);
      }
      document.execCommand("selectAll", false, null);
      document.execCommand("insertText", false, text);
      if (readEditorCleanText(editor) === normalizedTarget) {
        return true;
      }
    } catch (e) {}

    // Stage D: Non-destructive in-place paragraph mutation (preserves ProseMirror NodeViewDesc)
    try {
      let firstP = editor.querySelector("p");
      if (!firstP) {
        firstP = document.createElement("p");
        editor.appendChild(firstP);
      }
      firstP.classList.remove("placeholder");
      firstP.removeAttribute("data-empty-paragraph");
      while (firstP.firstChild) firstP.removeChild(firstP.firstChild);
      let sibling = firstP.nextSibling;
      while (sibling) {
        const next = sibling.nextSibling;
        editor.removeChild(sibling);
        sibling = next;
      }

      const lines = text.replace(/\r\n/g, "\n").split("\n");
      let lastNode = null;
      lines.forEach((line, idx) => {
        const targetP = idx === 0 ? firstP : document.createElement("p");
        if (idx > 0) editor.appendChild(targetP);
        if (line.length > 0) {
          const tn = document.createTextNode(line);
          targetP.appendChild(tn);
          lastNode = tn;
        } else {
          const br = document.createElement("br");
          targetP.appendChild(br);
          lastNode = br;
        }
      });

      const sel = window.getSelection();
      if (sel && lastNode) {
        const range = document.createRange();
        if (lastNode.nodeType === Node.TEXT_NODE) {
          range.setStart(lastNode, lastNode.length);
          range.setEnd(lastNode, lastNode.length);
        } else {
          range.selectNodeContents(editor);
          range.collapse(false);
        }
        sel.removeAllRanges();
        sel.addRange(range);
      }

      const pmView = findProseMirrorView(editor);
      if (pmView?.domObserver?.flush) {
        try { pmView.domObserver.flush(); } catch {}
      }

      editor.dispatchEvent(
        new InputEvent("input", {
          bubbles: true,
          cancelable: false,
          composed: true,
          inputType: "insertText",
          data: text,
        })
      );
      editor.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    } catch (e) {
      console.error("[LocalAI MAIN] ChatGPT Stage D input fallback error:", e);
    }

    try { editor.focus({ preventScroll: true }); } catch {}
    return readEditorCleanText(editor).length > 0;
  }

  function getChatGPTSendButtonStrict() {
    const strictSelectors = [
      'button[data-testid="send-button"]',
      'button[data-testid="fruitjuice-send-button"]',
      'button[data-testid*="send-button"]',
      'button[aria-label="Send prompt"]',
      'button[aria-label="Send message"]',
      'form button[type="submit"]:not([data-testid*="voice"]):not([aria-label*="voice" i])',
    ];
    for (const sel of strictSelectors) {
      const btn = document.querySelector(sel);
      if (btn && isVisible(btn)) return btn;
    }
    return null;
  }

  async function chatgptClickSend() {
    for (let i = 0; i < 30; i++) {
      const uploading = document.querySelector(
        '[data-testid*="attachment"] svg.animate-spin, form [role="progressbar"], form [aria-busy="true"]'
      );
      if (uploading && isVisible(uploading)) {
        await new Promise((r) => setTimeout(r, 200));
        continue;
      }

      const editor = getEditor();
      // Self-heal: if React/ProseMirror hydration wiped the editor before send, re-inject once
      if (editor && lastInjectedQueryText && !readEditorCleanText(editor) && !isGenerating() && !siteIsThinking()) {
        console.log("[LocalAI MAIN] ChatGPT: editor reverted before send, re-injecting text...");
        chatgptSetInput(editor, lastInjectedQueryText);
        await new Promise((r) => setTimeout(r, 120));
      }

      const btn = getChatGPTSendButtonStrict();
      if (btn && !btn.disabled && btn.getAttribute("aria-disabled") !== "true") {
        try {
          btn.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, cancelable: true, composed: true }));
          btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, composed: true }));
          btn.click();
        } catch {
          try { btn.click(); } catch {}
        }
        console.log("[LocalAI MAIN] ChatGPT: strict send button clicked");

        await new Promise((r) => setTimeout(r, 180));
        const currentEd = getEditor();
        if (!readEditorCleanText(currentEd) || isGenerating() || siteIsThinking()) {
          lastInjectedQueryText = "";
          emit({ type: "SUBMISSION_CONFIRMED", requestId: currentRequestId });
          return true;
        }
      }

      await new Promise((r) => setTimeout(r, 100));
    }

    const editor = getEditor();
    if (editor) {
      try {
        editor.focus({ preventScroll: true });
        const enterInit = {
          key: "Enter",
          code: "Enter",
          keyCode: 13,
          which: 13,
          bubbles: true,
          cancelable: true,
          composed: true,
        };
        editor.dispatchEvent(new KeyboardEvent("keydown", enterInit));
        editor.dispatchEvent(new KeyboardEvent("keypress", enterInit));
        editor.dispatchEvent(new KeyboardEvent("keyup", enterInit));
        const form = editor.closest("form");
        if (form && typeof form.requestSubmit === "function") {
          try { form.requestSubmit(); } catch {}
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 180));
      if (!readEditorCleanText(editor) || isGenerating() || siteIsThinking()) {
        lastInjectedQueryText = "";
        emit({ type: "SUBMISSION_CONFIRMED", requestId: currentRequestId });
        return true;
      }
      console.log("[LocalAI MAIN] ChatGPT: requesting hardware Return via NEED_OS_RETURN");
      emit({ type: "NEED_OS_RETURN", requestId: currentRequestId });
    }
    return true;
  }

  function getChatGPTResponseContainers() {
    // 1. Direct assistant containers in modern ChatGPT (2026: data-chatgpt-search-unit-key or data-conversation-role)
    const assistantKeys = Array.from(
      document.querySelectorAll('[data-chatgpt-search-unit-key*="assistant"], [data-content-search-unit-key*="assistant"]')
    );
    if (assistantKeys.length > 0) return assistantKeys;

    const convRoles = Array.from(
      document.querySelectorAll('[data-conversation-role="assistant"]')
    ).map(el => el.closest('[class*="block-"], [class*="group"], div') || el);
    if (convRoles.length > 0) return convRoles;

    const assistantRoles = Array.from(
      document.querySelectorAll('[data-message-author-role="assistant"]')
    );
    if (assistantRoles.length > 0) return assistantRoles;

    // 2. Turn containers (modern & legacy articles)
    const turns = Array.from(
      document.querySelectorAll('[data-talvt-turn-state], article[data-testid^="conversation-turn-"], article')
    );
    if (turns.length > 0) {
      const assistants = turns.filter((art) => {
        if (art.getAttribute("data-turn") === "user") return false;
        if (art.querySelector('[data-conversation-role="user"], [data-message-author-role="user"]')) return false;
        if (art.getAttribute("data-message-author-role") === "user") return false;
        if (
          art.getAttribute("data-turn") === "assistant" ||
          art.getAttribute("data-message-author-role") === "assistant" ||
          art.querySelector('[data-conversation-role="assistant"]') ||
          art.querySelector('[data-chatgpt-search-unit-key*="assistant"]') ||
          art.querySelector('[data-message-author-role="assistant"]') ||
          art.querySelector(".markdown, .prose")
        ) {
          return true;
        }
        return false;
      });
      if (assistants.length > 0) return assistants;
    }

    // 3. Standalone markdown
    const markdowns = Array.from(document.querySelectorAll("div.markdown, div.prose")).filter(
      (el) =>
        !el.closest(
          '.result-thinking, [data-testid*="thinking"], [data-testid*="thought"], [data-testid*="reasoning"]'
        )
    );
    if (markdowns.length > 0) return markdowns;

    return [];
  }

  // ============================================================
  //  SITE DEFINITIONS
  // ============================================================

  defineSite("gemini.google.com", {
    name: "Gemini",
    editorSelector: 'rich-textarea .ql-editor, rich-textarea [contenteditable], rich-textarea p',
    sendButtonSelector: 'button.send-button, button[aria-label*="Send prompt" i], button[aria-label*="Send message" i], button[aria-label*="Send" i]',
    stopButtonSelector: 'button.stop-button, .send-button-container button[aria-label*="Stop" i], rich-textarea ~ * button[aria-label*="Stop" i], button[aria-label*="Stop response" i], button[aria-label*="Stop generating" i]',
    responseContainerSelector: "model-response",
    thinkingSelector: '.thinking-indicator, [aria-label*="Thinking" i], [aria-label*="thought" i], thought-container, expandable-thought, .thought, [role="progressbar"], .loading-dots',
    getEditor: geminiGetEditor,
    setInput: geminiSetInput,
    clickSend: geminiClickSend,
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  defineSite("chatgpt.com", {
    name: "ChatGPT",
    editorSelector:
      'div#prompt-textarea[contenteditable="true"], div[data-lexical-editor="true"], #prompt-textarea, div[contenteditable="true"]',
    sendButtonSelector:
      'button[data-testid="send-button"], button[data-testid="fruitjuice-send-button"], button[data-testid*="send-button"], button[aria-label="Send prompt"], button[aria-label="Send message"]',
    stopButtonSelector:
      'button[data-testid="stop-button"], button[data-testid*="stop"], button[aria-label*="Stop generating"], button[aria-label*="Stop streaming"], button[aria-label*="Stop"]',
    responseContainerSelector: '[data-message-author-role="assistant"], div.markdown, div.prose',
    getEditor: chatgptGetEditor,
    getContainers: getChatGPTResponseContainers,
    thinkingSelector:
      '.result-thinking, [data-testid="thinking-animation"], [data-testid*="thought"], [data-testid*="reasoning"]',
    setInput: chatgptSetInput,
    clickSend: chatgptClickSend,
    completionIdleMs: 1200,
    completionFallbackMs: 2500,
    completionSafetyMs: 4000,
  });

  defineSite("chat.openai.com", {
    name: "ChatGPT (Legacy)",
    editorSelector:
      'div#prompt-textarea[contenteditable="true"], div[data-lexical-editor="true"], #prompt-textarea, div[contenteditable="true"]',
    sendButtonSelector:
      'button[data-testid="send-button"], button[data-testid="fruitjuice-send-button"], button[data-testid*="send-button"], button[aria-label="Send prompt"], button[aria-label="Send message"]',
    stopButtonSelector:
      'button[data-testid="stop-button"], button[data-testid*="stop"], button[aria-label*="Stop generating"], button[aria-label*="Stop streaming"], button[aria-label*="Stop"]',
    getEditor: chatgptGetEditor,
    getContainers: getChatGPTResponseContainers,
    thinkingSelector:
      '.result-thinking, [data-testid="thinking-animation"], [data-testid*="thought"], [data-testid*="reasoning"]',
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

    if (site.sendButtonSelector) {
      for (const sel of site.sendButtonSelector.split(",")) {
        const btn = document.querySelector(sel.trim());
        if (btn && isVisible(btn)) return btn;
      }
    }

    const rich = document.querySelector("rich-textarea");
    if (rich?.shadowRoot) {
      const sBtn = rich.shadowRoot.querySelector(
        'button[aria-label*="Send"], button.send-button'
      );
      if (sBtn && isVisible(sBtn)) return sBtn;
    }

    // Global fallback: strictly match buttons explicitly labeled as Send/Submit
    for (const btn of document.querySelectorAll("button")) {
      if (!isVisible(btn)) continue;
      const label = (btn.getAttribute("aria-label") || "").toLowerCase();
      const testid = (btn.getAttribute("data-testid") || "").toLowerCase();
      if (
        label.includes("send") ||
        label.includes("submit") ||
        testid.includes("send") ||
        btn.classList.contains("send-button")
      ) {
        return btn;
      }
    }
    return null;
  }

  function getStopButton() {
    const site = getSiteConfig();
    if (site.stopButtonSelector) {
      for (const sel of site.stopButtonSelector.split(",")) {
        const s = sel.trim();
        if (!s) continue;
        const stopBtn = document.querySelector(s);
        if (stopBtn && isVisible(stopBtn)) return stopBtn;
      }
    }
    const universalStops = [
      'button[data-testid*="stop" i]',
      'button[aria-label*="stop" i]',
      "button.stop-button",
      ".send-button-container button[aria-label*='stop' i]",
    ];
    for (const sel of universalStops) {
      const btn = document.querySelector(sel);
      if (btn && isVisible(btn)) return btn;
    }
    return null;
  }

  function clickStopButton() {
    const stopBtn = getStopButton();
    if (!stopBtn) return false;
    try {
      stopBtn.dispatchEvent(
        new PointerEvent("pointerdown", { bubbles: true, cancelable: true, composed: true })
      );
      stopBtn.dispatchEvent(
        new MouseEvent("mousedown", { bubbles: true, cancelable: true, composed: true })
      );
      stopBtn.click();
      stopBtn.dispatchEvent(
        new PointerEvent("pointerup", { bubbles: true, cancelable: true, composed: true })
      );
      stopBtn.dispatchEvent(
        new MouseEvent("mouseup", { bubbles: true, cancelable: true, composed: true })
      );
      console.log("[LocalAI MAIN] Stop button clicked successfully");
      return true;
    } catch (e) {
      try {
        stopBtn.click();
        return true;
      } catch {}
    }
    return false;
  }

  function getResponseContainers() {
    const site = getSiteConfig();
    if (site.getContainers) {
      const res = site.getContainers();
      if (res && res.length > 0) return res;
    }
    if (site.responseContainerSelector) {
      const result = Array.from(document.querySelectorAll(site.responseContainerSelector));
      if (result.length > 0) return result;
    }
    const fallbacks = [
      'div[data-message-author-role="assistant"]',
      "model-response",
      ".font-claude-message",
      '[data-is-streaming="true"]',
      'div[role="article"]',
      'div[data-testid*="message"]',
      'div[aria-label*="Meta AI"]',
      'div[class*="message-content"]',
      'div[class*="assistant-message"]',
    ];
    for (const sel of fallbacks) {
      const els = Array.from(document.querySelectorAll(sel));
      if (els.length > 0) return els;
    }
    return [];
  }

  // Baseline tracking state for virtualization resilience
  let baselineElements = new WeakSet();
  let baselineLastElement = null;
  let baselineLastText = "";

  function findActiveResponseContainer(currentContainers) {
    if (!currentContainers || currentContainers.length === 0) return null;
    const latest = currentContainers[currentContainers.length - 1];
    if (!latest) return null;

    // 1. Brand-new DOM element reference (works even when older turns were unmounted)
    if (!baselineElements.has(latest)) {
      return latest;
    }

    // 2. Container count strictly increased
    if (currentContainers.length > baselineCount) {
      return latest;
    }

    // 3. In-place mutation of the last container
    if (latest === baselineLastElement) {
      const currentTxt = readResponseText(latest);
      if (currentTxt && currentTxt !== baselineLastText) {
        return latest;
      }
    }

    return null;
  }

  function siteIsThinking() {
    const site = getSiteConfig();
    if (site.isThinking) return site.isThinking();

    const containers = getResponseContainers();
    const activeEl = findActiveResponseContainer(containers) || targetElement;

    if (site.thinkingSelector) {
      const searchRoot = activeEl?.closest?.("article") || activeEl || document.body;
      if (searchRoot) {
        for (const sel of site.thinkingSelector.split(",")) {
          const s = sel.trim();
          if (!s) continue;
          const el = searchRoot.querySelector(s);
          if (el && isVisible(el)) {
            const answerText = activeEl ? readResponseText(activeEl) : "";
            if (!answerText) return true;
          }
        }
      }
    }

    const stopBtn = getStopButton();
    if (stopBtn) {
      if (activeEl) {
        const text = readResponseText(activeEl);
        if (!text || text.trim().length === 0) return true;
      } else {
        return true;
      }
    }

    return false;
  }

  function isGenerating() {
    const site = getSiteConfig();
    if (site.isGenerating) return site.isGenerating();

    // 1. Stop button visible = unequivocally generating (MUST be checked BEFORE send button)
    if (getStopButton()) return true;

    // 2. Streaming attribute active = generating
    const streaming = document.querySelector(
      '[data-is-streaming="true"], .is-streaming, .result-streaming'
    );
    if (streaming && isVisible(streaming)) return true;

    // 3. Send button visible and enabled = NOT generating
    const sendBtn = getSendButton();
    if (sendBtn && !sendBtn.disabled && sendBtn.getAttribute("aria-disabled") !== "true") {
      return false;
    }

    return false;
  }

  // ============================================================
  //  OBSERVER & COMPLETION DETECTION
  // ============================================================

  function emitFinal(reason, overrideText) {
    const finalOut = overrideText !== undefined ? overrideText : lastFullText;
    if (!currentRequestId) return;
    const reqId = currentRequestId;
    currentRequestId = null;
    stopObserver();
    clearTimeout(queryTimeoutTimer);
    clearInterval(focusKeepaliveTimer);
    emit({
      type: "FINAL",
      full: finalOut || "",
      requestId: reqId,
      reason: reason || "complete",
    });
  }

  function scheduleCompletionCheck() {
    clearTimeout(finalTimer);
    const site = getSiteConfig();
    const idleMs = site.completionIdleMs || 1200;
    const fallbackMs = site.completionFallbackMs || 2500;
    const safetyMs = site.completionSafetyMs || 4000;

    const checkCompletion = () => {
      if (!currentRequestId) return;

      if (siteIsThinking()) {
        if (!thinkingEmitted) {
          emit({ type: "THINKING", requestId: currentRequestId });
          thinkingEmitted = true;
        }
        lastChangeAt = Date.now();
        finalTimer = setTimeout(checkCompletion, 500);
        return;
      }

      if (!hasStartedStreaming || !lastFullText) {
        finalTimer = setTimeout(checkCompletion, 300);
        return;
      }

      const idleFor = Date.now() - lastChangeAt;
      const generating = isGenerating();
      const openCode = hasUnclosedCodeBlock(lastFullText);

      if (idleFor >= idleMs && !generating && !openCode) {
        emitFinal("idle");
        return;
      }

      if (idleFor >= fallbackMs && !generating) {
        emitFinal("silence-fallback");
        return;
      }

      if (idleFor >= safetyMs && !generating) {
        emitFinal("absolute-safety");
        return;
      }

      finalTimer = setTimeout(checkCompletion, 200);
    };
    finalTimer = setTimeout(checkCompletion, 300);
  }

  function startObserver() {
    stopObserver();
    const containers = getResponseContainers();
    baselineCount = containers.length;
    baselineElements = new WeakSet(containers);
    baselineLastElement = containers.length > 0 ? containers[containers.length - 1] : null;
    baselineLastText = baselineLastElement ? readResponseText(baselineLastElement) : "";

    targetElement = null;
    lastObservedText = "";
    lastFullText = "";
    hasStartedStreaming = false;
    thinkingEmitted = false;
    lastChangeAt = Date.now();

    const onDomTick = () => {
      if (!currentRequestId) return;
      const currentContainers = getResponseContainers();
      const candidate = findActiveResponseContainer(currentContainers);

      if (candidate) {
        if (targetElement !== candidate) {
          targetElement = candidate;
          lastObservedText =
            candidate === baselineLastElement ? baselineLastText : "";
        }
      } else if (!targetElement || !document.contains(targetElement)) {
        return;
      }

      let full = readResponseText(targetElement);
      if (!full) return;

      // Token-Saver Countdown / Sequence Trip Check
      if (typeof evaluateTokenSaverTrip === "function") {
        const tripResult = evaluateTokenSaverTrip(full);
        if (tripResult?.tripped) {
          const guardedStop = () => {
            if (isGenerating()) {
              clickStopButton();
            }
          };
          guardedStop();
          setTimeout(guardedStop, 60);
          setTimeout(guardedStop, 180);
          const clean = tripResult.cleanText ?? full;
          if (clean && clean !== lastObservedText && clean.startsWith(lastObservedText)) {
            const finalDelta = clean.slice(lastObservedText.length);
            if (finalDelta) {
              emit({
                type: "STREAM_CHUNK",
                text: finalDelta,
                full: clean,
                requestId: currentRequestId,
              });
            }
          }
          lastFullText = clean;
          emitFinal("token-saver-trip", clean);
          return;
        }
      }

      if (full === lastObservedText) return;

      let chunk = "";
      if (full.startsWith(lastObservedText)) {
        chunk = full.slice(lastObservedText.length);
      } else if (lastObservedText && lastObservedText.startsWith(full)) {
        lastObservedText = full;
        lastFullText = full;
        lastChangeAt = Date.now();
        scheduleCompletionCheck();
        return;
      } else {
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
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
    pollTimer = setInterval(onDomTick, 250);
    scheduleCompletionCheck();
  }

  function stopObserver() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    clearTimeout(finalTimer);
  }

  // ============================================================
  //  FILE ATTACHMENT PIPELINE (ChatGPT, Gemini, Claude, Meta AI)
  // ============================================================

  function base64ToFile(fileSpec) {
    const binaryString = atob(fileSpec.data);
    const len = binaryString.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return new File([bytes], fileSpec.name, {
      type: fileSpec.mime || "application/octet-stream",
      lastModified: Date.now(),
    });
  }

  function findFileInput(siteName) {
    if (siteName === "Claude") {
      return (
        document.querySelector('input[data-testid="file-upload"][type="file"]') ||
        document.querySelector('fieldset input[type="file"]') ||
        document.querySelector('input[type="file"]')
      );
    }
    if (siteName.startsWith("ChatGPT")) {
      const formInputs = Array.from(document.querySelectorAll('form input[type="file"]'));
      if (formInputs.length > 0) {
        return formInputs.find((i) => !i.accept) || formInputs[formInputs.length - 1];
      }
      return document.querySelector('input[type="file"]');
    }
    if (siteName === "Gemini") {
      const rich = document.querySelector("rich-textarea");
      if (rich?.shadowRoot) {
        const sInput = rich.shadowRoot.querySelector('input[type="file"]');
        if (sInput) return sInput;
      }
      const existing = document.querySelector('input[type="file"]');
      if (existing) return existing;
      const addBtn = document.querySelector(
        'button[aria-label*="Add files" i], button[aria-label*="Upload files" i], button[aria-label*="Upload" i], button.upload-button, button[data-testid*="upload"]'
      );
      if (addBtn && isVisible(addBtn)) {
        addBtn.click();
        const popupInput = document.querySelector('input[type="file"]');
        if (popupInput) return popupInput;
      }
      return document.querySelector('input[type="file"]');
    }
    return document.querySelector('input[type="file"]');
  }

  function hasAttachmentPreview() {
    return Boolean(
      document.querySelector(
        '[data-testid*="attachment"], form [role="progressbar"], .file-preview, ' +
        'rich-textarea ~ * img, form [class*="attachment"], [aria-label*="Remove file" i], [aria-label*="Remove attachment" i]'
      )
    );
  }

  async function attachFilesToSite(editor, fileSpecs) {
    if (!fileSpecs || fileSpecs.length === 0) return true;
    const site = getSiteConfig();
    const siteName = site.name || "";

    let files;
    try {
      files = fileSpecs.map(base64ToFile);
    } catch (e) {
      console.error("[LocalAI MAIN] Failed to decode base64 file payload:", e);
      return false;
    }

    const dt = new DataTransfer();
    for (const f of files) {
      dt.items.add(f);
    }

    let attached = false;

    // Strategy 1: Native HTMLInputElement.prototype.files descriptor setter + input/change events
    const inputEl = findFileInput(siteName);
    if (inputEl) {
      try {
        const nativeFilesSetter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "files"
        )?.set;
        if (nativeFilesSetter) {
          nativeFilesSetter.call(inputEl, dt.files);
        } else {
          inputEl.files = dt.files;
        }
        inputEl.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        inputEl.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
        await new Promise((r) => setTimeout(r, 350));
        if (hasAttachmentPreview() || (inputEl.files && inputEl.files.length > 0)) {
          attached = true;
          console.log(`[LocalAI MAIN] Attached ${files.length} file(s) via input[type="file"]`);
        }
      } catch (e) {
        console.warn("[LocalAI MAIN] input[type=file] assignment failed:", e);
      }
    }

    // Strategy 2: Synthetic ClipboardEvent("paste") on editor (primary for Gemini, fallback for ChatGPT/Claude)
    if (!attached || siteName === "Gemini") {
      const target = editor || getEditor();
      if (target) {
        selectAndFocus(target);
        try {
          const pasteEv = new ClipboardEvent("paste", {
            bubbles: true,
            cancelable: true,
            composed: true,
            clipboardData: dt,
          });
          target.dispatchEvent(pasteEv);
          await new Promise((r) => setTimeout(r, 450));
          attached = true;
          console.log(`[LocalAI MAIN] Attached ${files.length} file(s) via ClipboardEvent("paste")`);
        } catch (e) {
          console.warn("[LocalAI MAIN] Paste attachment fallback error:", e);
        }
      }
    }

    // Strategy 3: DragEvent drop sequence on form/editor
    if (!attached) {
      const dropTarget = (editor && editor.closest("form")) || editor || document.body;
      try {
        for (const evType of ["dragenter", "dragover", "drop"]) {
          dropTarget.dispatchEvent(
            new DragEvent(evType, {
              bubbles: true,
              cancelable: true,
              composed: true,
              dataTransfer: dt,
            })
          );
        }
        await new Promise((r) => setTimeout(r, 450));
      } catch (e) {}
    }

    // Wait for upload progress indicators to settle completely before typing/submitting
    const uploadWaitStart = Date.now();
    await new Promise((r) => setTimeout(r, 400));
    while (Date.now() - uploadWaitStart < 15000) {
      const busySpinner = document.querySelector(
        '[data-testid*="attachment"] svg.animate-spin, form [role="progressbar"], ' +
        '.file-preview [aria-busy="true"], mat-progress-spinner, [data-testid*="upload"] svg.animate-spin'
      );
      if (!busySpinner || !isVisible(busySpinner)) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    await new Promise((r) => setTimeout(r, 250));
    return true;
  }

  // ============================================================
  //  TOKEN-SAVER COUNTDOWN / SEQUENCE TRIP ENGINE
  // ============================================================

  let activeTripConfig = {
    enabled: true,
    threshold: 5,
    tripLeading: false,
    tripTrailing: true,
  };

  function isSequentialRun(nums) {
    if (!nums || nums.length < 2) return false;
    const step = nums[1] - nums[0];
    if (step !== 1 && step !== -1) return false;
    for (let i = 1; i < nums.length; i++) {
      if (nums[i] - nums[i - 1] !== step) return false;
    }
    return true;
  }

  function evaluateTokenSaverTrip(fullText) {
    if (!activeTripConfig.enabled || !fullText) return null;
    const threshold = Math.max(3, activeTripConfig.threshold || 5);

    // 1. Check LEADING countdown / countup sequence (e.g. "1\n2\n3\n4\n5" or "500, 499, 498...")
    if (activeTripConfig.tripLeading) {
      const headMatch = fullText.match(/^\s*((?:\d{1,4}(?:\s*[\n, ]\s*|$)){3,})/);
      if (headMatch) {
        const rawSeq = headMatch[1];
        const nums = rawSeq
          .trim()
          .split(/[\s,]+/)
          .filter((t) => /^\d{1,4}$/.test(t))
          .map((t) => parseInt(t, 10));
        if (nums.length >= threshold && isSequentialRun(nums.slice(0, threshold))) {
          const cleanText = fullText.slice(headMatch[0].length).trimStart();
          console.log(
            `[LocalAI MAIN] Token-Saver TRIP (leading sequence halted at ${nums.slice(0, threshold).join(" -> ")})`
          );
          return { tripped: true, mode: "leading", cleanText };
        }
      }
    }

    // 2. Check TRAILING line-by-line or inline sequence (e.g., 1\n2\n3\n4\n5 or 500\n499\n498...)
    // Avoids false positives on numbered lists ("1. Step one") because periods/words on the line reject the match.
    if (activeTripConfig.tripTrailing) {
      const lines = fullText.split("\n");
      let endIdx = lines.length - 1;
      while (endIdx >= 0 && lines[endIdx].trim() === "") endIdx--;

      // 2a. Multi-line pure-integer sequence at the tail of the stream
      if (endIdx + 1 >= threshold) {
        const seqNums = [];
        let seqStartLineIdx = endIdx + 1;
        for (let i = endIdx; i >= 0; i--) {
          const trimmed = lines[i].trim();
          if (/^\d{1,4}$/.test(trimmed)) {
            seqNums.unshift(parseInt(trimmed, 10));
            seqStartLineIdx = i;
          } else {
            // Check if the boundary line ends with the first number of the run (e.g. "...done. 1")
            const tailNumMatch = trimmed.match(/^(.*?)\s+(\d{1,4})$/);
            if (tailNumMatch && seqNums.length > 0) {
              const candidateFirst = parseInt(tailNumMatch[2], 10);
              const expectedStep = seqNums.length >= 2 ? seqNums[1] - seqNums[0] : (seqNums[0] - candidateFirst);
              if (seqNums[0] - candidateFirst === expectedStep && (expectedStep === 1 || expectedStep === -1)) {
                seqNums.unshift(candidateFirst);
                lines[i] = tailNumMatch[1];
                break;
              }
            }
            break;
          }
        }

        if (seqNums.length >= threshold && isSequentialRun(seqNums)) {
          const cleanLines = lines.slice(0, seqStartLineIdx);
          const cleanText = cleanLines.join("\n").trimEnd();
          console.log(
            `[LocalAI MAIN] Token-Saver TRIP (trailing line sequence halted at ${seqNums[seqNums.length - 1]})`
          );
          return { tripped: true, mode: "trailing", cleanText };
        }
      }

      // 2b. Inline trailing sequence on the last line (e.g. "1, 2, 3, 4, 5" or "1 2 3 4 5")
      if (endIdx >= 0) {
        const lastLine = lines[endIdx];
        const inlineMatch = lastLine.match(/(?:^|\s)(\d{1,4}(?:\s*,\s*|\s+)\d{1,4}(?:(?:\s*,\s*|\s+)\d{1,4}){2,})\s*$/);
        if (inlineMatch) {
          const nums = inlineMatch[1].split(/[\s,]+/).filter(Boolean).map((n) => parseInt(n, 10));
          if (nums.length >= threshold && isSequentialRun(nums)) {
            const prefixOnLine = lastLine.slice(0, inlineMatch.index).trimEnd();
            const cleanLines = lines.slice(0, endIdx);
            if (prefixOnLine) cleanLines.push(prefixOnLine);
            const cleanText = cleanLines.join("\n").trimEnd();
            console.log(
              `[LocalAI MAIN] Token-Saver TRIP (inline trailing sequence halted at ${nums[nums.length - 1]})`
            );
            return { tripped: true, mode: "trailing-inline", cleanText };
          }
        }
      }
    }

    return null;
  }

  // ============================================================
  //  QUERY & STOP HANDLING
  // ============================================================

  const handleStopQuery = (msg) => {
    const reqId = msg?.requestId || currentRequestId;
    console.log(`[LocalAI MAIN] Received STOP_QUERY for ${reqId}`);
    const clicked = clickStopButton();
    if (currentRequestId && (!msg?.requestId || msg.requestId === currentRequestId)) {
      emitFinal("user-stopped", lastFullText);
    } else {
      emit({
        type: "STOP_ACK",
        requestId: reqId,
        stopped: clicked,
      });
    }
  };

  const handleQuery = async (msg) => {
    if (!msg || msg.type !== "RUN_QUERY") return;

    if (currentRequestId && (isGenerating() || siteIsThinking() || hasStartedStreaming)) {
      const oldReqId = currentRequestId;
      console.log(`[LocalAI MAIN] Busy guard: stopping ${oldReqId} before new query`);
      emit({
        type: "FINAL",
        full: lastFullText || "",
        requestId: oldReqId,
        reason: "interrupted",
      });
      stopObserver();
      clickStopButton();

      const settleStart = Date.now();
      while (Date.now() - settleStart < 8000) {
        await new Promise((r) => setTimeout(r, 250));
        if (!isGenerating() && !siteIsThinking()) break;
      }
      await new Promise((r) => setTimeout(r, 300));
    }

    currentRequestId = msg.requestId || crypto.randomUUID();
    lastFullText = "";
    lastObservedText = "";
    hasStartedStreaming = false;
    thinkingEmitted = false;

    activeTripConfig = {
      enabled: msg.tripEnabled !== false,
      threshold: typeof msg.tripThreshold === "number" ? msg.tripThreshold : 5,
      tripLeading: Boolean(msg.tripLeading),
      tripTrailing: msg.tripTrailing !== false,
    };

    const query = msg.query || "";
    const files = Array.isArray(msg.files) ? msg.files : [];

    let editor = getEditor();
    if (!editor) {
      for (let i = 0; i < 25; i++) {
        await new Promise((r) => setTimeout(r, 200));
        editor = getEditor();
        if (editor) break;
      }
    }

    if (!editor) {
      emit({
        type: "ERROR",
        error: "Editor not found - is AI page loaded?",
        requestId: currentRequestId,
      });
      currentRequestId = null;
      return;
    }

    startObserver();

    if (files.length > 0 && typeof attachFilesToSite === "function") {
      const attachedOk = await attachFilesToSite(editor, files);
      if (!attachedOk) {
        emit({
          type: "ERROR",
          error: "Failed to attach one or more files to the AI composer",
          requestId: currentRequestId,
        });
        currentRequestId = null;
        stopObserver();
        return;
      }
      editor = getEditor() || editor;
    }

    if (query) {
      const ok = setInput(editor, query);
      if (!ok) {
        emit({ type: "ERROR", error: "Failed to set input", requestId: currentRequestId });
        currentRequestId = null;
        stopObserver();
        return;
      }
    }

    const reqIdAtLaunch = currentRequestId;
    setTimeout(async () => {
      if (currentRequestId !== reqIdAtLaunch) return;
      await clickSend();
    }, 200);

    clearTimeout(queryTimeoutTimer);
    let quietSeconds = 0;
    const maxThinkingSeconds = 1800;
    let totalSeconds = 0;

    const checkQueryTimeout = () => {
      if (!currentRequestId || currentRequestId !== reqIdAtLaunch) return;
      if (hasStartedStreaming) return;

      totalSeconds++;

      if (siteIsThinking() || isGenerating()) {
        quietSeconds = 0;
        if (totalSeconds % 5 === 0) {
          emit({ type: "THINKING", requestId: currentRequestId });
        }
        if (totalSeconds < maxThinkingSeconds) {
          queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);
          return;
        }
        emit({
          type: "ERROR",
          error: "Exceeded maximum thinking time (30 min)",
          requestId: currentRequestId,
        });
        currentRequestId = null;
        stopObserver();
        return;
      }

      const currentContainers = getResponseContainers();
      const activeContainer = findActiveResponseContainer(currentContainers);
      if (activeContainer) {
        const text = readResponseText(activeContainer);
        if (text && text.length > 0) {
          hasStartedStreaming = true;
          lastFullText = text;
          lastObservedText = text;
          emit({
            type: "STREAM_CHUNK",
            text: text,
            full: text,
            requestId: currentRequestId,
          });
          if (!isGenerating()) {
            emitFinal("completed-fast", text);
            return;
          }
          scheduleCompletionCheck();
          return;
        }
      }

      quietSeconds++;
      if (quietSeconds > 90) {
        emit({
          type: "ERROR",
          error: "Timeout waiting for AI response to start (90s)",
          requestId: currentRequestId,
        });
        currentRequestId = null;
        stopObserver();
        return;
      }

      queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);
    };
    queryTimeoutTimer = setTimeout(checkQueryTimeout, 1000);

    if (focusKeepaliveTimer) {
      clearInterval(focusKeepaliveTimer);
      focusKeepaliveTimer = null;
    }
    let focusKeepaliveCount = 0;
    focusKeepaliveTimer = setInterval(() => {
      focusKeepaliveCount++;
      if (
        focusKeepaliveCount > 12 ||
        !currentRequestId ||
        hasStartedStreaming ||
        isGenerating() ||
        siteIsThinking()
      ) {
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
  //  MESSAGE LISTENER (MAIN Page Realm via postMessage)
  // ============================================================

  const handleIncomingMessage = (msg) => {
    if (!msg || typeof msg !== "object") return;
    const site = getSiteConfig();
    console.log(
      "[LocalAI MAIN]",
      site.name || "Unknown",
      "- Received:",
      msg.type,
      msg.query?.substring(0, 30) || ""
    );
    if (msg.type === "RUN_QUERY") handleQuery(msg);
    if (msg.type === "STOP_QUERY") handleStopQuery(msg);
    if (msg.type === "DOM_STATUS") {
      const ed = getEditor();
      const btn = getSendButton();
      const stopBtn = getStopButton();
      emit({
        type: "DOM_STATUS_REPLY",
        editor: ed
          ? {
              tag: ed.tagName,
              id: ed.id,
              text: readEditorCleanText(ed),
              innerHTML: ed.innerHTML.slice(0, 300),
              isFocused: document.activeElement === ed,
            }
          : null,
        sendButton: btn
          ? {
              tag: btn.tagName,
              testid: btn.getAttribute("data-testid"),
              disabled: btn.disabled,
              ariaDisabled: btn.getAttribute("aria-disabled"),
              text: btn.textContent.trim(),
            }
          : null,
        stopButton: stopBtn
          ? {
              tag: stopBtn.tagName,
              testid: stopBtn.getAttribute("data-testid"),
              ariaLabel: stopBtn.getAttribute("aria-label"),
            }
          : null,
        containers: getResponseContainers().length,
        url: window.location.href,
      });
    }
  };

  const onPageMessage = (e) => {
    if (e.source !== window) return;
    const data = e?.data;
    if (!data || data.__bridge !== BRIDGE) return;
    if (data.direction !== "BG_TO_MAIN") return;
    handleIncomingMessage(data.payload);
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
