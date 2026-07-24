/**
 * Dusky Template Generator & Visual Element Theme Picker v5.1 (Production Bulletproof Edition)
 * Stress-Tested & Hardened Logic:
 * - Scroll & Window Resize SVG Mask Repositioning
 * - Input/Textarea typing protection for Alt+Shift+P hotkeys
 * - Dynamic SPA DOM Unmount Protection (document.body.contains check)
 * - Safe BoundingClientRect Fallbacks for Shadow DOM & iframes
 * - Scoped Ctrl+Z / Ctrl+Y shortcut handling
 */

(function () {
  if (window.__duskyGeneratorInjected) return;
  window.__duskyGeneratorInjected = true;

  // ─── State Variables ───
  let pickerActive = false;
  let selectionLocked = false;
  let currentTargetEl = null;
  let ancestorStack = [];
  let currentStackIndex = 0;

  let customRules = []; // Array of { id, selector, prop, val, meta, cssText }
  let undoStack = [];
  let redoStack = [];

  let lastDialogLeft = null;
  let lastDialogTop = null;

  // ─── Soft Warm Eye-Friendly Palette Tokens ───
  const THEME = {
    bgDark: "rgba(25, 22, 20, 0.96)",
    bgSurface: "#191614",
    bgCard: "#25201c",
    bgButton: "#2d2722",
    accentWarm: "#e6c280",
    accentGold: "#d4a359",
    textCream: "#f5ebe0",
    textMuted: "#c4b8aa",
    borderWarm: "#3d342c",
    borderAccent: "#d4a359",
    dangerSoft: "#b8545e"
  };

  // ─── Matugen Theme Tokens List ───
  const MATUGEN_TOKENS = [
    { label: "🏠 Surface", val: "var(--surface)", meta: "Surface Background" },
    { label: "📦 Surface Container", val: "var(--surface_container)", meta: "Surface Container" },
    { label: "📦 Surface Container High", val: "var(--surface_container_high)", meta: "Surface Container High" },
    { label: "📦 Surface Container Low", val: "var(--surface_container_low)", meta: "Surface Container Low" },
    { label: "🌟 Primary Accent", val: "var(--primary)", meta: "Primary Accent" },
    { label: "💡 Primary Container", val: "var(--primary_container)", meta: "Primary Container" },
    { label: "🌿 Secondary Accent", val: "var(--secondary)", meta: "Secondary Accent" },
    { label: "🍃 Secondary Container", val: "var(--secondary_container)", meta: "Secondary Container" },
    { label: "🌺 Tertiary Accent", val: "var(--tertiary)", meta: "Tertiary Accent" },
    { label: "🌸 Tertiary Container", val: "var(--tertiary_container)", meta: "Tertiary Container" },
    { label: "✏️ On Surface (Text)", val: "var(--on_surface)", meta: "Text / Foreground" },
    { label: "✏️ On Surface Variant", val: "var(--on_surface_variant)", meta: "Muted Text" },
    { label: "🖼️ Outline (Border)", val: "var(--outline)", meta: "Border / Outline" },
    { label: "🖼️ Outline Variant", val: "var(--outline_variant)", meta: "Soft Divider" },
    { label: "🚨 Error", val: "var(--error)", meta: "Error Accent" },
    { label: "🚨 Error Container", val: "var(--error_container)", meta: "Error Container" }
  ];

  // ─── Ignore List for Variable Scanner ───
  const IGNORE_PATTERNS = [
    /^--darkreader/,
    /^--tw-/,
    /^--fa-/,
    /^--wp-/,
    /^--bi-/,
    /^--chakra-/,
    /^--dusky-/,
    /^--surface$/,
    /^--surface_/,
    /^--background$/,
    /^--primary$/,
    /^--on_/,
    /^--outline$/,
    /^--error$/,
    /^--[a-z0-9]$/,
    /gradient|shadow|transition|bezier|easing|transform|font-family|font-weight|font-size|line-height|radius|spacing|margin|padding|opacity|z-index|aspect|order|flex|grid-template/i
  ];

  function getCleanDomain() {
    return window.location.hostname.replace(/^www\./, "");
  }

  function shouldIgnoreVariable(prop) {
    return IGNORE_PATTERNS.some(pattern => pattern.test(prop));
  }

  // ─── LocalStorage Persistence ───
  function loadSavedCustomRules() {
    const domain = getCleanDomain();
    try {
      if (typeof browser !== "undefined" && browser.storage && browser.storage.local) {
        browser.storage.local.get(`dusky_rules_${domain}`).then(data => {
          if (data[`dusky_rules_${domain}`]) {
            customRules = data[`dusky_rules_${domain}`];
            applyLiveCustomCss();
            updateBarInfo();
          }
        });
      }
    } catch (e) {}
  }

  function saveCustomRulesLocally() {
    const domain = getCleanDomain();
    try {
      if (typeof browser !== "undefined" && browser.storage && browser.storage.local) {
        const obj = {};
        obj[`dusky_rules_${domain}`] = customRules;
        browser.storage.local.set(obj);
      }
    } catch (e) {}
  }

  loadSavedCustomRules();

  // ─── CSS Variable Scanner ───
  function collectCleanCssVariables() {
    const vars = new Set();
    const elementsToScan = [document.documentElement, document.body].filter(Boolean);

    elementsToScan.forEach(el => {
      const styles = getComputedStyle(el);
      for (let i = 0; i < styles.length; i++) {
        const prop = styles[i];
        if (prop.startsWith("--") && !shouldIgnoreVariable(prop)) {
          vars.add(prop);
        }
      }
    });

    try {
      Array.from(document.styleSheets).forEach(sheet => {
        try {
          const rules = sheet.cssRules || sheet.rules;
          if (!rules) return;
          Array.from(rules).forEach(rule => {
            if (rule.style) {
              for (let i = 0; i < rule.style.length; i++) {
                const prop = rule.style[i];
                if (prop.startsWith("--") && !shouldIgnoreVariable(prop)) {
                  vars.add(prop);
                }
              }
            }
          });
        } catch (e) {}
      });
    } catch (e) {}

    return Array.from(vars);
  }

  function autoCategorizeVariables(vars) {
    const categories = {
      surfaceHex: [], surfaceRgb: [],
      containerHex: [], containerRgb: [],
      primaryHex: [], primaryRgb: [],
      onSurfaceHex: [], onSurfaceRgb: [],
      outlineHex: [], outlineRgb: []
    };

    vars.forEach(v => {
      const lower = v.toLowerCase();
      const isRgb = lower.endsWith("_rgb") || lower.includes("rgb") || lower.includes("-rgb");

      if (/border|divider|stroke|outline|separator|grid-color/.test(lower)) {
        if (isRgb) categories.outlineRgb.push(v);
        else categories.outlineHex.push(v);
      } else if (/primary|brand|accent|active|selected|link|highlight|focus|action/.test(lower)) {
        if (isRgb) categories.primaryRgb.push(v);
        else categories.primaryHex.push(v);
      } else if (/fg|text|font|foreground|label|heading|title|color-default|color-muted|on-/.test(lower)) {
        if (isRgb) categories.onSurfaceRgb.push(v);
        else categories.onSurfaceHex.push(v);
      } else if (/card|container|dialog|sidebar|panel|modal|popover|dropdown|box|header|nav|menu|surface-1|surface-2/.test(lower)) {
        if (isRgb) categories.containerRgb.push(v);
        else categories.containerHex.push(v);
      } else if (/bg|surface|background|canvas|page|base|layer|body|app-/.test(lower)) {
        if (isRgb) categories.surfaceRgb.push(v);
        else categories.surfaceHex.push(v);
      }
    });

    return categories;
  }

  function generateFullCssPayload() {
    const domain = getCleanDomain();
    const vars = collectCleanCssVariables();
    const cat = autoCategorizeVariables(vars);

    let css = `@-moz-document domain("${domain}") {\n`;
    css += `    :root, body {\n`;

    let mappedCount = 0;
    const sections = [
      { name: "Surfaces & Backgrounds (Hex)", items: cat.surfaceHex, token: "var(--surface)" },
      { name: "Surfaces & Backgrounds (RGB Components)", items: cat.surfaceRgb, token: "var(--surface_rgb)" },
      { name: "Containers & Cards (Hex)", items: cat.containerHex, token: "var(--surface_container)" },
      { name: "Containers & Cards (RGB Components)", items: cat.containerRgb, token: "var(--surface_container_rgb)" },
      { name: "Primary Accents (Hex)", items: cat.primaryHex, token: "var(--primary)" },
      { name: "Primary Accents (RGB Components)", items: cat.primaryRgb, token: "var(--primary_rgb)" },
      { name: "Text & Foreground (Hex)", items: cat.onSurfaceHex, token: "var(--on_surface)" },
      { name: "Text & Foreground (RGB Components)", items: cat.onSurfaceRgb, token: "var(--on_surface_rgb)" },
      { name: "Borders & Outlines (Hex)", items: cat.outlineHex, token: "var(--outline)" },
      { name: "Borders & Outlines (RGB Components)", items: cat.outlineRgb, token: "var(--outline_rgb)" },
    ];

    sections.forEach(sec => {
      if (sec.items.length > 0) {
        css += `        /* ${sec.name} */\n`;
        sec.items.forEach(v => {
          css += `        ${v}: ${sec.token} !important;\n`;
          mappedCount++;
        });
      }
    });

    if (mappedCount === 0) {
      css += `        background-color: var(--surface) !important;\n`;
      css += `        color: var(--on_surface) !important;\n`;
    }
    css += `    }\n`;

    if (customRules.length > 0) {
      css += `\n    /* Visually Picked Element & Theme Rules */\n`;
      customRules.forEach(r => {
        css += `    ${r.selector} {\n`;
        css += `        /* Role: ${r.meta} */\n`;
        if (r.cssText) {
          css += `        ${r.cssText}\n`;
        } else {
          css += `        ${r.prop}: ${r.val} !important;\n`;
        }
        css += `    }\n`;
      });
    }

    css += `}\n`;
    return { domain, css, totalVars: vars.length, mappedCount, customRuleCount: customRules.length };
  }

  // ─── Live In-Page Preview Style Tag ───
  function applyLiveCustomCss() {
    let styleTag = document.getElementById("dusky-live-custom-css");
    if (!styleTag) {
      styleTag = document.createElement("style");
      styleTag.id = "dusky-live-custom-css";
      (document.head || document.documentElement).appendChild(styleTag);
    }

    let css = "";
    customRules.forEach(r => {
      css += `${r.selector} {\n`;
      if (r.cssText) {
        css += `  ${r.cssText}\n`;
      } else {
        css += `  ${r.prop}: ${r.val} !important;\n`;
      }
      css += `}\n`;
    });
    styleTag.textContent = css;
  }

  // ─── Temporary Real-Time Hover Preview ───
  function showTempLivePreview(selector, actionType, customText = "") {
    let styleTag = document.getElementById("dusky-temp-preview-css");
    if (!styleTag) {
      styleTag = document.createElement("style");
      styleTag.id = "dusky-temp-preview-css";
      (document.head || document.documentElement).appendChild(styleTag);
    }

    let cssText = "";
    if (actionType === "surface") cssText = "background-color: var(--surface) !important;";
    else if (actionType === "container") cssText = "background-color: var(--surface_container) !important;";
    else if (actionType === "primary") cssText = "background-color: var(--primary) !important; color: var(--on_primary) !important;";
    else if (actionType === "primary_container") cssText = "background-color: var(--primary_container) !important; color: var(--on_primary_container) !important;";
    else if (actionType === "text") cssText = "color: var(--on_surface) !important;";
    else if (actionType === "outline") cssText = "border-color: var(--outline) !important;";
    else if (actionType === "transparent") cssText = "background: transparent !important; border: none !important; box-shadow: none !important;";
    else if (actionType === "hide") cssText = "display: none !important;";
    else if (actionType === "matugen_token") cssText = `background-color: ${customText} !important;`;

    styleTag.textContent = `${selector} { ${cssText} }\n`;
  }

  function removeTempLivePreview() {
    const styleTag = document.getElementById("dusky-temp-preview-css");
    if (styleTag) styleTag.remove();
  }

  // ─── Generic Drag Helper for Dialogs and Floating Bars ───
  function makeDraggable(element, handle) {
    let isDragging = false;
    let startX, startY, initialLeft, initialTop;

    handle.style.cursor = "grab";

    handle.addEventListener("mousedown", (e) => {
      if (e.target.tagName === "BUTTON" || e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      isDragging = true;
      handle.style.cursor = "grabbing";
      startX = e.clientX;
      startY = e.clientY;

      const rect = element.getBoundingClientRect();
      initialLeft = rect.left;
      initialTop = rect.top;

      element.style.transform = "none";
      element.style.left = `${initialLeft}px`;
      element.style.top = `${initialTop}px`;

      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const newLeft = Math.max(10, Math.min(window.innerWidth - element.offsetWidth - 10, initialLeft + dx));
      const newTop = Math.max(10, Math.min(window.innerHeight - element.offsetHeight - 10, initialTop + dy));

      element.style.left = `${newLeft}px`;
      element.style.top = `${newTop}px`;

      if (element.id === "dusky-picker-dialog") {
        lastDialogLeft = newLeft;
        lastDialogTop = newTop;
      }
    });

    document.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        handle.style.cursor = "grab";
      }
    });
  }

  // ─── Soft Warm Eye-Friendly SVG Island Cutout Mask Overlay ───
  function createSvgSeaMask() {
    let svg = document.getElementById("dusky-picker-sea");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.id = "dusky-picker-sea";
      Object.assign(svg.style, {
        position: "fixed",
        top: "0",
        left: "0",
        width: "100vw",
        height: "100vh",
        pointerEvents: "none",
        zIndex: "2147483645",
        display: "none"
      });

      const pathOcean = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pathOcean.setAttribute("fill", "rgba(18, 15, 12, 0.48)");
      pathOcean.setAttribute("fill-rule", "evenodd");

      const pathBorder = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pathBorder.setAttribute("fill", "none");
      pathBorder.setAttribute("stroke", THEME.accentWarm);
      pathBorder.setAttribute("stroke-width", "2");
      pathBorder.setAttribute("stroke-dasharray", "4 4");

      svg.appendChild(pathOcean);
      svg.appendChild(pathBorder);
      (document.body || document.documentElement).appendChild(svg);
    }
    return svg;
  }

  function updateSvgSeaMask(targetEl) {
    const svg = createSvgSeaMask();
    if (!targetEl || !pickerActive || !document.body.contains(targetEl)) {
      svg.style.display = "none";
      return;
    }

    try {
      const rect = targetEl.getBoundingClientRect();
      const vw = window.innerWidth;
      const vh = window.innerHeight;

      const oceanPath = `M0 0h${vw}v${vh}h-${vw}z M${rect.left} ${rect.top}h${rect.width}v${rect.height}h-${rect.width}z`;
      const borderPath = `M${rect.left} ${rect.top}h${rect.width}v${rect.height}h-${rect.width}z`;

      svg.children[0].setAttribute("d", oceanPath);
      svg.children[1].setAttribute("d", borderPath);
      svg.style.display = "block";
    } catch (e) {
      svg.style.display = "none";
    }
  }

  // ─── Scroll & Window Resize SVG Listener ───
  function onScrollOrResize() {
    if (pickerActive && currentTargetEl) {
      updateSvgSeaMask(currentTargetEl);
    }
  }

  // ─── Selector Candidate Generator ───
  function generateCandidateSelectors(el) {
    if (!el || el === document.body || el === document.documentElement || !document.body.contains(el)) {
      return [{ label: "body", selector: "body", count: 1 }];
    }

    const candidates = [];
    const tag = el.tagName.toLowerCase();
    const id = el.id ? `#${CSS.escape(el.id)}` : null;
    const role = el.getAttribute("role");
    const ariaLabel = el.getAttribute("aria-label");
    const ariaCurrent = el.getAttribute("aria-current");

    let classes = [];
    if (el.className && typeof el.className === "string") {
      classes = el.className
        .trim()
        .split(/\s+/)
        .filter(c => c && !c.startsWith("dusky-"));
    }

    let specSel = tag;
    if (classes.length > 0) {
      specSel += "." + classes.slice(0, 3).map(c => CSS.escape(c)).join(".");
    }

    if (id) {
      candidates.push({ label: `ID Target (${id})`, selector: id });
    }

    if (role && ariaSelected(el)) {
      candidates.push({ label: `Role Selected (${tag}[role="${role}"][aria-selected="true"])`, selector: `${tag}[role="${CSS.escape(role)}"][aria-selected="true"]` });
    } else if (role) {
      candidates.push({ label: `Role Target (${tag}[role="${role}"])`, selector: `${tag}[role="${CSS.escape(role)}"]` });
    }

    if (ariaCurrent) {
      candidates.push({ label: `Active Page (${tag}[aria-current="${ariaCurrent}"])`, selector: `${tag}[aria-current="${CSS.escape(ariaCurrent)}"]` });
    } else if (ariaLabel) {
      candidates.push({ label: `Aria Label (${tag}[aria-label="${ariaLabel}"])`, selector: `${tag}[aria-label="${CSS.escape(ariaLabel)}"]` });
    }

    candidates.push({ label: `Tag & Class (${specSel})`, selector: specSel });

    const fullPath = getUniquePathSelector(el);
    if (fullPath !== specSel && fullPath !== id) {
      candidates.push({ label: `Hierarchy Path (${fullPath})`, selector: fullPath });
    }

    candidates.forEach(c => {
      try {
        c.count = document.querySelectorAll(c.selector).length;
      } catch (e) {
        c.count = 1;
      }
    });

    return candidates;
  }

  function ariaSelected(el) {
    return el.getAttribute("aria-selected") === "true" || el.classList.contains("active") || el.classList.contains("selected");
  }

  function getUniquePathSelector(el) {
    const parts = [];
    let curr = el;
    while (curr && curr.nodeType === Node.ELEMENT_NODE && curr !== document.body) {
      let sel = curr.tagName.toLowerCase();
      if (curr.id) {
        parts.unshift(`#${CSS.escape(curr.id)}`);
        break;
      }
      if (curr.className && typeof curr.className === "string") {
        const classes = curr.className.trim().split(/\s+/).filter(c => c && !c.startsWith("dusky-"));
        if (classes.length > 0) sel += "." + CSS.escape(classes[0]);
      }
      parts.unshift(sel);
      if (parts.length >= 3) break;
      curr = curr.parentElement;
    }
    return parts.join(" > ");
  }

  // ─── Draggable Top Control Bar ───
  function createControlBar() {
    let bar = document.getElementById("dusky-picker-bar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "dusky-picker-bar";
      bar.innerHTML = `
        <div id="dusky-bar-drag-handle" style="display:flex; align-items:center; gap:8px; cursor:grab; flex:1;">
          <span style="opacity:0.5; font-size:12px; color:${THEME.textMuted};">::</span>
          <span style="font-weight:bold; color:${THEME.accentWarm}; font-size:13px;">🎯 Dusky Theme Picker</span>
          <span id="dusky-bar-info" style="font-size:11px; color:${THEME.textMuted}; max-width:240px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Hover & click. Shift+Click Zap. ↑↓ Depth</span>
        </div>
        <div style="display:flex; align-items:center; gap:6px;">
          <button id="dusky-bar-undo" title="Undo Rule (Ctrl+Z)" style="background:${THEME.bgButton}; color:${THEME.textCream}; border:1px solid ${THEME.borderWarm}; padding:3px 8px; border-radius:4px; font-size:11px; cursor:pointer;">↩️ Undo</button>
          <button id="dusky-bar-redo" title="Redo Rule (Ctrl+Y)" style="background:${THEME.bgButton}; color:${THEME.textCream}; border:1px solid ${THEME.borderWarm}; padding:3px 8px; border-radius:4px; font-size:11px; cursor:pointer;">↪️ Redo</button>
          <button id="dusky-bar-rules" title="View Active Custom Rules" style="background:${THEME.bgButton}; color:${THEME.accentWarm}; border:1px solid ${THEME.borderAccent}; padding:3px 8px; border-radius:4px; font-size:11px; cursor:pointer;">📋 Rules (${customRules.length})</button>
          <button id="dusky-bar-close" title="Exit Picker (Esc / Alt+Shift+P)" style="background:${THEME.dangerSoft}; color:#fff; border:none; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold; cursor:pointer;">✕ Exit</button>
        </div>
      `;
      Object.assign(bar.style, {
        position: "fixed",
        top: "12px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: "2147483647",
        background: THEME.bgDark,
        color: THEME.textCream,
        padding: "8px 16px",
        borderRadius: "10px",
        border: `1px solid ${THEME.borderAccent}`,
        boxShadow: "0 8px 32px rgba(0,0,0,0.65)",
        fontFamily: "system-ui, -apple-system, sans-serif",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        minWidth: "520px"
      });
      (document.body || document.documentElement).appendChild(bar);

      makeDraggable(bar, document.getElementById("dusky-bar-drag-handle"));

      document.getElementById("dusky-bar-undo").addEventListener("click", undoLastRule);
      document.getElementById("dusky-bar-redo").addEventListener("click", redoRule);
      document.getElementById("dusky-bar-rules").addEventListener("click", toggleRulesDrawer);
      document.getElementById("dusky-bar-close").addEventListener("click", () => togglePicker(false));
    }
  }

  function removeControlBar() {
    const bar = document.getElementById("dusky-picker-bar");
    if (bar) bar.remove();
  }

  function updateBarInfo() {
    const info = document.getElementById("dusky-bar-info");
    const rulesBtn = document.getElementById("dusky-bar-rules");
    if (info && currentTargetEl) {
      info.textContent = `<${currentTargetEl.tagName.toLowerCase()}> ${getUniquePathSelector(currentTargetEl)}`;
    }
    if (rulesBtn) {
      rulesBtn.textContent = `📋 Rules (${customRules.length})`;
    }
  }

  // ─── Ancestor Hierarchy Stack ───
  function buildAncestorStack(el) {
    ancestorStack = [];
    let curr = el;
    while (curr && curr.nodeType === Node.ELEMENT_NODE && curr !== document.body) {
      ancestorStack.push(curr);
      curr = curr.parentElement;
    }
    if (ancestorStack.length === 0 && el) ancestorStack = [el];
    currentStackIndex = 0;
    currentTargetEl = ancestorStack[0];
  }

  // ─── Mouse & Global Keyboard Handlers ───
  function onMouseOver(e) {
    if (!pickerActive || selectionLocked) return;
    if (e.target.closest("#dusky-picker-bar") || e.target.closest("#dusky-picker-dialog") || e.target.closest("#dusky-rules-drawer") || e.target.closest("#dusky-picker-sea")) return;

    buildAncestorStack(e.target);
    updateSvgSeaMask(currentTargetEl);
    updateBarInfo();
  }

  function onClick(e) {
    if (!pickerActive) return;
    if (e.target.closest("#dusky-picker-bar") || e.target.closest("#dusky-picker-dialog") || e.target.closest("#dusky-rules-drawer") || e.target.closest("#dusky-picker-sea")) return;

    e.preventDefault();
    e.stopPropagation();

    selectionLocked = true;

    if (e.shiftKey && currentTargetEl) {
      const candidates = generateCandidateSelectors(currentTargetEl);
      const sel = candidates[0].selector;
      addCustomRule(sel, "hide");
      selectionLocked = false;
      return;
    }

    if (currentTargetEl) {
      showActionDialog(currentTargetEl);
    }
  }

  // Global Keyboard Shortcuts (Input/Textarea Safe)
  document.addEventListener("keydown", (e) => {
    // Ignore global hotkey if typing in input/textarea/contenteditable
    const isEditing = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable;

    if (!isEditing && e.altKey && (e.key.toLowerCase() === "p" || e.code === "KeyP")) {
      e.preventDefault();
      togglePicker(!pickerActive);
      return;
    }

    if (!pickerActive) return;

    if (e.key === "Escape") {
      removeTempLivePreview();
      closeDialog();
    } else if (!isEditing && e.key === "ArrowUp") {
      e.preventDefault();
      stepStackDepth(1);
    } else if (!isEditing && e.key === "ArrowDown") {
      e.preventDefault();
      stepStackDepth(-1);
    } else if (!isEditing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      undoLastRule();
    } else if (!isEditing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
      e.preventDefault();
      redoRule();
    }
  }, true);

  function closeDialog() {
    selectionLocked = false;
    removeTempLivePreview();
    const dialog = document.getElementById("dusky-picker-dialog");
    if (dialog) dialog.remove();
  }

  function stepStackDepth(delta) {
    if (ancestorStack.length === 0) return;
    currentStackIndex = Math.max(0, Math.min(ancestorStack.length - 1, currentStackIndex + delta));
    currentTargetEl = ancestorStack[currentStackIndex];
    updateSvgSeaMask(currentTargetEl);
    updateBarInfo();

    const slider = document.getElementById("dusky-depth-slider");
    if (slider) {
      slider.value = currentStackIndex;
      updateDialogForNewTarget();
    }
  }

  // ─── Interactive Action Dialog ───
  function showActionDialog(targetEl) {
    const existing = document.getElementById("dusky-picker-dialog");
    if (existing) existing.remove();

    buildAncestorStack(targetEl);
    const candidates = generateCandidateSelectors(currentTargetEl);

    const dialog = document.createElement("div");
    dialog.id = "dusky-picker-dialog";
    dialog.innerHTML = `
      <!-- Draggable Header Bar -->
      <div id="dusky-dialog-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; cursor:grab; user-select:none; padding-bottom:6px; border-bottom:1px solid ${THEME.borderWarm};">
        <div style="display:flex; align-items:center; gap:6px;">
          <span style="opacity:0.5; font-size:12px; color:${THEME.textMuted};">::</span>
          <span style="font-weight:bold; color:${THEME.accentWarm}; font-size:13px;">🎨 Theme Element Inspector</span>
        </div>
        <div style="display:flex; align-items:center; gap:4px;">
          <button id="dusky-dialog-opacity-btn" title="Toggle See-Through Translucency" style="background:${THEME.bgButton}; border:1px solid ${THEME.borderWarm}; color:${THEME.accentWarm}; padding:2px 6px; border-radius:4px; font-size:11px; cursor:pointer;">👁️</button>
          <button id="dusky-dialog-minimize-btn" title="Minimize Window" style="background:${THEME.bgButton}; border:1px solid ${THEME.borderWarm}; color:${THEME.textCream}; padding:2px 6px; border-radius:4px; font-size:11px; cursor:pointer;">➖</button>
          <button id="dusky-dialog-close-x" title="Close Dialog" style="background:none; border:none; color:${THEME.textMuted}; font-size:14px; cursor:pointer; padding:0 4px;">✕</button>
        </div>
      </div>

      <!-- Minimizable Body Content -->
      <div id="dusky-dialog-body-content">
        <!-- uBlock-Style DOM Depth Slider -->
        <div style="background:${THEME.bgCard}; padding:8px 10px; border-radius:8px; border:1px solid ${THEME.borderWarm}; margin-bottom:10px;">
          <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;">
            <span style="color:${THEME.textMuted};">DOM Tree Depth Level:</span>
            <span id="dusky-depth-label" style="color:${THEME.accentWarm}; font-weight:bold;">0 / ${ancestorStack.length - 1} (&lt;${currentTargetEl.tagName.toLowerCase()}&gt;)</span>
          </div>
          <input id="dusky-depth-slider" type="range" min="0" max="${ancestorStack.length - 1}" value="0" style="width:100%; accent-color:${THEME.accentWarm}; cursor:pointer;">
        </div>

        <!-- Selector Candidate Dropdown & Match Count -->
        <div style="margin-bottom:10px;">
          <div style="display:flex; justify-content:space-between; font-size:11px; color:${THEME.textMuted}; margin-bottom:4px;">
            <span>Target CSS Selector:</span>
            <span id="dusky-match-badge" style="color:${THEME.accentWarm}; font-weight:bold;">Matches: ${candidates[0].count}</span>
          </div>
          <select id="dusky-candidate-select" style="width:100%; background:${THEME.bgCard}; color:${THEME.accentWarm}; border:1px solid ${THEME.borderAccent}; padding:5px 6px; border-radius:6px; font-family:monospace; font-size:11px; cursor:pointer;">
            ${candidates.map((c, i) => `<option value="${i}">${c.label} (${c.count} match)</option>`).join("")}
          </select>
        </div>

        <!-- Quick Action Role Buttons -->
        <div style="display:flex; flex-direction:column; gap:5px; max-height:160px; overflow-y:auto; padding-right:4px; margin-bottom:8px;">
          <button data-action="surface" style="background:${THEME.bgCard}; color:${THEME.textCream}; border:1px solid ${THEME.borderWarm}; padding:6px 9px; border-radius:6px; font-size:11px; text-align:left; cursor:pointer;">🏠 Main Surface (background: var(--surface))</button>
          <button data-action="container" style="background:${THEME.bgCard}; color:${THEME.textCream}; border:1px solid ${THEME.borderWarm}; padding:6px 9px; border-radius:6px; font-size:11px; text-align:left; cursor:pointer;">📦 Surface Container (var(--surface_container))</button>
          <button data-action="primary" style="background:${THEME.accentGold}; color:#191614; border:none; padding:6px 9px; border-radius:6px; font-size:11px; font-weight:bold; text-align:left; cursor:pointer;">🌟 Primary Accent (var(--primary) & var(--on_primary))</button>
          <button data-action="primary_container" style="background:${THEME.accentWarm}; color:#191614; border:none; padding:6px 9px; border-radius:6px; font-size:11px; font-weight:bold; text-align:left; cursor:pointer;">💡 Accent Container / Pill (var(--primary_container))</button>
          <button data-action="transparent" style="background:${THEME.bgButton}; color:${THEME.accentWarm}; border:1px dashed ${THEME.borderAccent}; padding:6px 9px; border-radius:6px; font-size:11px; font-weight:bold; text-align:left; cursor:pointer;">👻 Make Transparent (background: transparent)</button>
          <button data-action="hide" style="background:${THEME.dangerSoft}; color:#fff; border:none; padding:6px 9px; border-radius:6px; font-size:11px; font-weight:bold; text-align:left; cursor:pointer;">🙈 Hide Element Completely (display: none)</button>
        </div>

        <!-- Full Matugen Token Selector Dropdown -->
        <div style="margin-bottom:8px;">
          <div style="font-size:10px; color:${THEME.textMuted}; margin-bottom:4px;">Pick Any Matugen Token:</div>
          <div style="display:flex; gap:6px;">
            <select id="dusky-matugen-select" style="flex:1; background:${THEME.bgCard}; color:${THEME.accentWarm}; border:1px solid ${THEME.borderWarm}; padding:5px 6px; border-radius:4px; font-size:11px; cursor:pointer;">
              ${MATUGEN_TOKENS.map((t, idx) => `<option value="${idx}">${t.label}</option>`).join("")}
            </select>
            <button id="dusky-matugen-apply" style="background:${THEME.accentWarm}; color:#191614; border:none; padding:5px 10px; border-radius:4px; font-weight:bold; font-size:11px; cursor:pointer;">Apply Token</button>
          </div>
        </div>

        <!-- Custom CSS Rule Input -->
        <div style="padding-top:6px; border-top:1px solid ${THEME.borderWarm};">
          <div style="font-size:10px; color:${THEME.textMuted}; margin-bottom:4px;">Custom CSS (Property: Value):</div>
          <div style="display:flex; gap:6px;">
            <input id="dusky-custom-input" type="text" placeholder="e.g. opacity: 0.8; filter: blur(2px);" style="flex:1; background:${THEME.bgCard}; color:${THEME.textCream}; border:1px solid ${THEME.borderWarm}; padding:5px; border-radius:4px; font-size:11px;">
            <button id="dusky-custom-apply" style="background:${THEME.accentWarm}; color:#191614; border:none; padding:5px 10px; border-radius:4px; font-weight:bold; font-size:11px; cursor:pointer;">Apply</button>
          </div>
        </div>
      </div>
    `;

    const styleObj = {
      position: "fixed",
      zIndex: "2147483647",
      background: THEME.bgSurface,
      color: THEME.textCream,
      padding: "14px",
      borderRadius: "12px",
      border: `1px solid ${THEME.borderAccent}`,
      boxShadow: "0 12px 40px rgba(0,0,0,0.85)",
      fontFamily: "system-ui, -apple-system, sans-serif",
      width: "370px",
      transition: "opacity 0.2s ease"
    };

    if (lastDialogLeft !== null && lastDialogTop !== null) {
      styleObj.left = `${lastDialogLeft}px`;
      styleObj.top = `${lastDialogTop}px`;
      styleObj.transform = "none";
    } else {
      styleObj.top = "80px";
      styleObj.right = "24px";
      styleObj.transform = "none";
    }

    Object.assign(dialog.style, styleObj);

    (document.body || document.documentElement).appendChild(dialog);

    makeDraggable(dialog, document.getElementById("dusky-dialog-header"));

    let isMinimized = false;
    let isTranslucent = false;
    const bodyContent = document.getElementById("dusky-dialog-body-content");
    const minBtn = document.getElementById("dusky-dialog-minimize-btn");
    const opacBtn = document.getElementById("dusky-dialog-opacity-btn");

    minBtn.addEventListener("click", () => {
      isMinimized = !isMinimized;
      bodyContent.style.display = isMinimized ? "none" : "block";
      minBtn.textContent = isMinimized ? "➕" : "➖";
    });

    opacBtn.addEventListener("click", () => {
      isTranslucent = !isTranslucent;
      dialog.style.opacity = isTranslucent ? "0.35" : "1.0";
    });

    const slider = document.getElementById("dusky-depth-slider");
    slider.addEventListener("input", (e) => {
      removeTempLivePreview();
      currentStackIndex = parseInt(e.target.value, 10);
      currentTargetEl = ancestorStack[currentStackIndex];
      updateSvgSeaMask(currentTargetEl);
      updateDialogForNewTarget();
    });

    const candidateSelect = document.getElementById("dusky-candidate-select");
    candidateSelect.addEventListener("change", () => {
      updateSvgSeaMask(currentTargetEl);
    });

    document.getElementById("dusky-dialog-close-x").addEventListener("click", closeDialog);

    dialog.querySelectorAll("button[data-action]").forEach(btn => {
      btn.addEventListener("mouseenter", () => {
        const action = btn.getAttribute("data-action");
        const selectedSel = getSelectedCandidateSelector();
        showTempLivePreview(selectedSel, action);
      });

      btn.addEventListener("mouseleave", () => {
        removeTempLivePreview();
      });

      btn.addEventListener("click", () => {
        const action = btn.getAttribute("data-action");
        const selectedSel = getSelectedCandidateSelector();
        addCustomRule(selectedSel, action);
        closeDialog();
      });
    });

    document.getElementById("dusky-matugen-apply").addEventListener("click", () => {
      const idx = parseInt(document.getElementById("dusky-matugen-select").value, 10);
      const token = MATUGEN_TOKENS[idx];
      if (token) {
        const selectedSel = getSelectedCandidateSelector();
        addCustomRule(selectedSel, "matugen_token", token.val, token.meta);
        closeDialog();
      }
    });

    document.getElementById("dusky-custom-apply").addEventListener("click", () => {
      const val = document.getElementById("dusky-custom-input").value.trim();
      if (val) {
        const selectedSel = getSelectedCandidateSelector();
        addCustomRule(selectedSel, "custom", val);
        closeDialog();
      }
    });
  }

  function getSelectedCandidateSelector() {
    const candidates = generateCandidateSelectors(currentTargetEl);
    const selectEl = document.getElementById("dusky-candidate-select");
    if (selectEl && selectEl.value) {
      const idx = parseInt(selectEl.value, 10);
      return candidates[idx] ? candidates[idx].selector : candidates[0].selector;
    }
    return candidates[0].selector;
  }

  function updateDialogForNewTarget() {
    const candidates = generateCandidateSelectors(currentTargetEl);
    const depthLabel = document.getElementById("dusky-depth-label");
    const matchBadge = document.getElementById("dusky-match-badge");
    const selectEl = document.getElementById("dusky-candidate-select");

    if (depthLabel) {
      depthLabel.textContent = `${currentStackIndex} / ${ancestorStack.length - 1} (<${currentTargetEl.tagName.toLowerCase()}>)`;
    }
    if (matchBadge) {
      matchBadge.textContent = `Matches: ${candidates[0].count}`;
    }
    if (selectEl) {
      selectEl.innerHTML = candidates.map((c, i) => `<option value="${i}">${c.label} (${c.count} match)</option>`).join("");
    }
    updateBarInfo();
  }

  // ─── Rule Management ───
  function addCustomRule(selector, actionType, customVal = "", customMeta = "") {
    let prop = "background-color";
    let val = "var(--surface)";
    let meta = "Main Surface";
    let cssText = "";

    if (actionType === "container") {
      prop = "background-color"; val = "var(--surface_container)"; meta = "Surface Container";
    } else if (actionType === "primary") {
      cssText = "background-color: var(--primary) !important; color: var(--on_primary) !important;"; meta = "Primary Accent";
    } else if (actionType === "primary_container") {
      cssText = "background-color: var(--primary_container) !important; color: var(--on_primary_container) !important;"; meta = "Accent Container";
    } else if (actionType === "text") {
      prop = "color"; val = "var(--on_surface)"; meta = "Text Color";
    } else if (actionType === "outline") {
      prop = "border-color"; val = "var(--outline)"; meta = "Border Color";
    } else if (actionType === "transparent") {
      cssText = "background: transparent !important; border: none !important; box-shadow: none !important;"; meta = "Transparent Element";
    } else if (actionType === "hide") {
      prop = "display"; val = "none"; meta = "Hidden Element";
    } else if (actionType === "matugen_token") {
      prop = "background-color"; val = customVal; meta = customMeta || "Matugen Token";
    } else if (actionType === "custom") {
      cssText = customVal.endsWith(";") ? customVal : customVal + ";"; meta = "Custom CSS Rule";
    }

    const ruleObj = { id: Date.now(), selector, prop, val, meta, cssText };
    customRules.push(ruleObj);
    undoStack.push(ruleObj);
    redoStack = [];

    applyLiveCustomCss();
    updateBarInfo();
    saveCustomRulesLocally();
    notifyHostToSave();
  }

  function undoLastRule() {
    if (customRules.length === 0) return;
    const removed = customRules.pop();
    redoStack.push(removed);

    applyLiveCustomCss();
    updateBarInfo();
    saveCustomRulesLocally();
    notifyHostToSave();
  }

  function redoRule() {
    if (redoStack.length === 0) return;
    const restored = redoStack.pop();
    customRules.push(restored);

    applyLiveCustomCss();
    updateBarInfo();
    saveCustomRulesLocally();
    notifyHostToSave();
  }

  function deleteRuleById(id) {
    customRules = customRules.filter(r => r.id !== id);
    applyLiveCustomCss();
    updateBarInfo();
    renderRulesDrawerList();
    saveCustomRulesLocally();
    notifyHostToSave();
  }

  function clearAllRules() {
    customRules = [];
    undoStack = [];
    redoStack = [];
    applyLiveCustomCss();
    updateBarInfo();
    renderRulesDrawerList();
    saveCustomRulesLocally();
    notifyHostToSave();
  }

  // ─── Active Rules Drawer ───
  function toggleRulesDrawer() {
    let drawer = document.getElementById("dusky-rules-drawer");
    if (drawer) {
      drawer.remove();
      return;
    }

    drawer = document.createElement("div");
    drawer.id = "dusky-rules-drawer";
    drawer.innerHTML = `
      <div id="dusky-drawer-drag-handle" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; cursor:grab; user-select:none; padding-bottom:6px; border-bottom:1px solid ${THEME.borderWarm};">
        <div style="display:flex; align-items:center; gap:6px;">
          <span style="opacity:0.5; font-size:12px; color:${THEME.textMuted};">::</span>
          <span style="font-weight:bold; color:${THEME.accentWarm}; font-size:13px;">📋 Active Custom Rules</span>
        </div>
        <div style="display:flex; align-items:center; gap:4px;">
          <button id="dusky-drawer-clear-all" title="Clear All Custom Rules" style="background:${THEME.dangerSoft}; color:#fff; border:none; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold; cursor:pointer;">🗑️ Clear All</button>
          <button id="dusky-drawer-close" style="background:none; border:none; color:${THEME.textMuted}; font-size:14px; cursor:pointer;">✕</button>
        </div>
      </div>
      <div id="dusky-drawer-rules-list" style="display:flex; flex-direction:column; gap:6px; max-height:300px; overflow-y:auto; padding-right:4px;"></div>
    `;

    Object.assign(drawer.style, {
      position: "fixed",
      top: "60px",
      right: "20px",
      zIndex: "2147483647",
      background: THEME.bgSurface,
      color: THEME.textCream,
      padding: "14px",
      borderRadius: "10px",
      border: `1px solid ${THEME.borderAccent}`,
      boxShadow: "0 10px 30px rgba(0,0,0,0.85)",
      fontFamily: "system-ui, -apple-system, sans-serif",
      width: "330px"
    });

    (document.body || document.documentElement).appendChild(drawer);

    makeDraggable(drawer, document.getElementById("dusky-drawer-drag-handle"));
    document.getElementById("dusky-drawer-close").addEventListener("click", () => drawer.remove());
    document.getElementById("dusky-drawer-clear-all").addEventListener("click", () => {
      if (confirm("Are you sure you want to clear all custom rules for this site?")) {
        clearAllRules();
      }
    });

    renderRulesDrawerList();
  }

  function renderRulesDrawerList() {
    const listEl = document.getElementById("dusky-drawer-rules-list");
    if (!listEl) return;

    if (customRules.length === 0) {
      listEl.innerHTML = `<div style="font-size:11px; color:${THEME.textMuted}; text-align:center; padding:12px;">No custom rules picked yet. Click any element on page to add rules!</div>`;
      return;
    }

    listEl.innerHTML = "";
    customRules.forEach(r => {
      const item = document.createElement("div");
      item.style.cssText = `display:flex; justify-content:space-between; align-items:center; background:${THEME.bgCard}; padding:6px 8px; border-radius:6px; border:1px solid ${THEME.borderWarm}; font-size:11px;`;
      item.innerHTML = `
        <div style="overflow:hidden; text-overflow:ellipsis; padding-right:6px;">
          <div style="font-weight:bold; color:${THEME.accentWarm};">${r.meta}</div>
          <div style="font-family:monospace; color:${THEME.textMuted}; font-size:10px; word-break:break-all;">${r.selector}</div>
        </div>
        <button data-delete-id="${r.id}" style="background:${THEME.dangerSoft}; color:#fff; border:none; padding:2px 6px; border-radius:4px; font-size:10px; font-weight:bold; cursor:pointer;">Delete</button>
      `;
      listEl.appendChild(item);
    });

    listEl.querySelectorAll("button[data-delete-id]").forEach(btn => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.getAttribute("data-delete-id"), 10);
        deleteRuleById(id);
      });
    });
  }

  // ─── Auto-Save to Disk via Native Host ───
  function notifyHostToSave() {
    const payload = generateFullCssPayload();
    if (typeof browser !== "undefined" && browser.runtime) {
      browser.runtime.sendMessage({
        type: "SAVE_TEMPLATE",
        domain: payload.domain,
        css: payload.css
      }).catch(() => {});
    }
  }

  // ─── Toggle Picker On/Off ───
  function togglePicker(enable) {
    pickerActive = enable;
    if (pickerActive) {
      createControlBar();
      createSvgSeaMask();
      document.addEventListener("mouseover", onMouseOver, true);
      document.addEventListener("click", onClick, true);
      window.addEventListener("scroll", onScrollOrResize, { passive: true });
      window.addEventListener("resize", onScrollOrResize, { passive: true });
    } else {
      removeControlBar();
      closeDialog();
      updateSvgSeaMask(null);
      const drawer = document.getElementById("dusky-rules-drawer");
      if (drawer) drawer.remove();

      document.removeEventListener("mouseover", onMouseOver, true);
      document.removeEventListener("click", onClick, true);
      window.removeEventListener("scroll", onScrollOrResize);
      window.removeEventListener("resize", onScrollOrResize);
      currentTargetEl = null;
      ancestorStack = [];
      selectionLocked = false;
    }
  }

  // ─── Extension Message Listener ───
  if (typeof browser !== "undefined" && browser.runtime) {
    browser.runtime.onMessage.addListener((msg, sender, sendResponse) => {
      if (msg.type === "SCAN_PAGE") {
        const res = generateFullCssPayload();
        sendResponse(res);
      } else if (msg.type === "TOGGLE_PICKER") {
        togglePicker(msg.enable);
        sendResponse({ pickerActive });
      }
    });
  }
})();
