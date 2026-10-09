/*
 * Dusky Template Generator — content.js (Gecko 157.0.1+)
 *
 * Two jobs, one file:
 *   1. scan()  — colour-token scanner behind the popup's Auto-map button;
 *   2. picker  — an in-page, closed-shadow-DOM element picker that owns the
 *                "picks" region of $XDG_CONFIG_HOME/dusky_sites/<domain>.css.
 *
 * CASCADE CONTRACT (v4 — this is what makes previews and themes reliable):
 *   All injected CSS lives in ONE constructable stylesheet adopted by the
 *   document, structured as
 *       @layer dusky.preview, dusky.live;
 *   CSS Cascade 5 reverses layer order for !important declarations: the
 *   EARLIEST layer wins, and layered !important beats unlayered !important.
 *   Therefore preview beats live, and both beat unlayered author rules.
 *   Inline !important and important rules in earlier site layers can still win.
 *   Pick and edit mode share the same preview engine.
 *
 * Reliability contract:
 *   · nothing is written unless the picks region was read first (hydrated);
 *   · every write carries base_rev; a conflict re-reads, merges (local edits win
 *     per key) and retries with backoff;
 *   · writes are serialised and coalesced by generation — a burst is one write.
 *
 * Rule identity (unchanged wire protocol): one rule per line, trailed by a
 * "dusky key=<strict-urlencoded-key> | <meta>" CSS comment. Key shapes:
 *   sel\u001F<selector>\u001F<group>   group: bg|text|border|fill|display|custom:<props>
 *   var\u001F<scope>\u001F<--name>
 *   raw\u001F<css>                     hand-written line we could not parse
 */
"use strict";
(() => {
  if (globalThis.__duskyTemplateGenerator) return;
  globalThis.__duskyTemplateGenerator = true;

  /* ══ Material 3 palette contract ════════════════════════════════════ */
  const TOKENS = [
    ["background", "Background (Page canvas)"],
    ["on_background", "On background"],
    ["surface", "Surface (Base)"],
    ["surface_container_lowest", "Surface container lowest"],
    ["surface_container_low", "Surface container low"],
    ["surface_container", "Surface container (Cards)"],
    ["surface_container_high", "Surface container high (Modals)"],
    ["surface_container_highest", "Surface container highest"],
    ["surface_bright", "Surface bright"],
    ["surface_dim", "Surface dim"],
    ["surface_variant", "Surface variant"],
    ["on_surface", "On surface (Main text)"],
    ["on_surface_variant", "On surface variant (Muted)"],
    ["inverse_surface", "Inverse surface"],
    ["inverse_on_surface", "Inverse on surface"],
    ["primary", "Primary (Brand accent)"],
    ["on_primary", "On primary"],
    ["primary_container", "Primary container"],
    ["on_primary_container", "On primary container"],
    ["primary_fixed", "Primary fixed (Hover)"],
    ["primary_fixed_dim", "Primary fixed dim (Active)"],
    ["secondary", "Secondary"],
    ["on_secondary", "On secondary"],
    ["secondary_container", "Secondary container"],
    ["on_secondary_container", "On secondary container"],
    ["secondary_fixed_dim", "Secondary fixed dim"],
    ["on_secondary_fixed_variant", "On secondary fixed variant"],
    ["tertiary", "Tertiary"],
    ["on_tertiary", "On tertiary"],
    ["tertiary_container", "Tertiary container"],
    ["on_tertiary_container", "On tertiary container"],
    ["outline", "Outline (Borders)"],
    ["outline_variant", "Outline variant (Dividers)"],
    ["error", "Error"],
    ["on_error", "On error"],
    ["error_container", "Error container"],
    ["on_error_container", "On error container"],
  ];
  const TOKEN_NAMES = new Set(TOKENS.map(([t]) => t));

  /* Everything Matugen itself injects. Exact names only: a prefix regex also
   * swallows real site tokens such as --error_bg, --surface_alt, --primary_hover. */
  const PALETTE_OWNED = new Set([
    ...TOKEN_NAMES,
    "error_container", "on_error_container", "inverse_primary",
    "on_primary_fixed", "on_primary_fixed_variant",
    "secondary_fixed", "on_secondary_fixed",
    "tertiary_fixed", "tertiary_fixed_dim", "on_tertiary_fixed", "on_tertiary_fixed_variant",
    "scrim", "shadow", "source_color", "surface_tint",
  ]);
  const PALETTE_SUFFIX = /_(rgb_comma|rgb|rgba|hex|hsl|raw|strip)$/;
  /* Framework internals that are never site design tokens. --mui-* and --wp--*
   * are deliberately NOT here: those are mappable palettes. */
  const NOISE_RE = /^--(tw-|fa-|dusky|darkreader|chakra-emotion)/i;

  const skipVar = (name) => {
    if (typeof name !== "string" || !name.startsWith("--")) return true;
    if (NOISE_RE.test(name)) return true;
    return PALETTE_OWNED.has(name.slice(2).replace(PALETTE_SUFFIX, ""));
  };

  const paletteLoaded = () =>
    getComputedStyle(document.documentElement).getPropertyValue("--surface").trim() !== "";

  /* ══ Shadow host + closed root (also hosts the colour probe) ═════════ */
  const hostEl = document.createElement("dusky-picker");
  for (const [p, v] of Object.entries({
    all: "initial", display: "block", position: "fixed", top: "0", left: "0",
    width: "0", height: "0", overflow: "visible", "z-index": "2147483647",
    "pointer-events": "none", isolation: "isolate",
  })) hostEl.style.setProperty(p, v, "important");
  const root = hostEl.attachShadow({ mode: "closed" });

  /* ══ Perceptual colour engine ═══════════════════════════════════════ */
  /* A detached style object handles ordinary colours; a reusable shadow node
   * resolves context-dependent colours and a one-pixel canvas converts to sRGB. */
  let colorProbe = null, colorCanvas = null;
  const getProbe = () => (colorProbe ??= document.createElement("span"));
  const dropProbe = () => { colorProbe = null; };

  const RGB_OUT = /^(?:rgb|rgba)\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.%]+))?\s*\)$/i;
  const IS_COLOR_SYNTAX =
    /^(#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$|(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\()/i;
  const HSL_TRIPLET = /^(-?[\d.]+)(?:deg)?\s+([\d.]+)%\s+([\d.]+)%$/;
  const RGB_TRIPLET = /^(\d{1,3})[,\s]+(\d{1,3})[,\s]+(\d{1,3})$/;
  const KEYWORDISH = /^(inherit|initial|unset|revert|revert-layer|transparent|currentcolor|none)$/i;

  function parseCssColor(raw, context = document.documentElement) {
    if (typeof raw !== "string") return null;
    const v = raw.trim();
    if (!v || KEYWORDISH.test(v)) return null;

    const hsl = v.match(HSL_TRIPLET);
    const triplet = !hsl && v.match(RGB_TRIPLET);
    if (triplet) {
      const [, r, g, b] = triplet.map(Number);
      return r <= 255 && g <= 255 && b <= 255 ? { r, g, b, a: 1, shape: v.includes(",") ? "rgb-comma" : "rgb-triplet" } : null;
    }
    /* Reject non-colours early so font/spacing variables never leak through. */
    if (!(hsl || IS_COLOR_SYNTAX.test(v) || CSS.supports("color", v))) return null;

    const probe = getProbe();
    probe.style.color = "";
    probe.style.color = hsl ? `hsl(${v})` : v;
    if (!probe.style.color) return null;                /* the parser refused it */

    /* Detached elements have no computed style, so resolve through the CSSOM:
     * style.color is already serialised by the parser into a canonical form. */
    const serialised = probe.style.color;
    const match = RGB_OUT.exec(serialised);
    if (!match) {
      const ctx = (colorCanvas ??= new OffscreenCanvas(1, 1).getContext("2d", {
        colorSpace: "srgb", willReadFrequently: true,
      }));
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = resolveViaRoot(serialised, context);
      ctx.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
      return { r, g, b, a: a / 255, shape: hsl ? "hsl-triplet" : "color" };
    }
    const [, rs, gs, bs, as] = match;
    const alpha = as === undefined ? 1 : (as.endsWith("%") ? parseFloat(as) / 100 : parseFloat(as));
    return {
      r: Math.round(+rs), g: Math.round(+gs), b: Math.round(+bs), a: alpha,
      shape: hsl ? "hsl-triplet" : "color",
    };
  }

  /* Resolve context-dependent colours before canvas converts their colour space. */
  let resolver = null;
  function resolveViaRoot(value, context) {
    if (!resolver) {
      resolver = document.createElement("i");
      resolver.style.cssText = "display:none!important";
      root.append(resolver);
    }
    const detached = !hostEl.isConnected;
    if (detached) document.documentElement.append(hostEl);
    try {
      const cs = getComputedStyle(context);
      resolver.style.color = cs.color;
      resolver.style.colorScheme = cs.colorScheme;
      resolver.style.backgroundColor = "";
      resolver.style.backgroundColor = value;
      return getComputedStyle(resolver).backgroundColor;
    } finally {
      if (detached) hostEl.remove();
    }
  }

  const srgb = (c) => ((c /= 255), c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const luminance = (r, g, b) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);

  function chromaHue(r, g, b) {
    const rf = r / 255, gf = g / 255, bf = b / 255;
    const max = Math.max(rf, gf, bf), min = Math.min(rf, gf, bf), d = max - min;
    let h = 0;
    if (d !== 0) {
      if (max === rf) h = ((gf - bf) / d) % 6;
      else if (max === gf) h = (bf - rf) / d + 2;
      else h = (rf - gf) / d + 4;
      h = Math.round(h * 60);
      if (h < 0) h += 360;
    }
    return { chroma: d, hue: h };
  }

  /* ══ Framework signature table ══════════════════════════════════════ */
  const SHADCN = {
    "--background": "background", "--foreground": "on_background",
    "--card": "surface_container", "--card-foreground": "on_surface",
    "--popover": "surface_container_high", "--popover-foreground": "on_surface",
    "--primary": "primary", "--primary-foreground": "on_primary",
    "--secondary": "secondary_container", "--secondary-foreground": "on_secondary_container",
    "--muted": "surface_container_low", "--muted-foreground": "on_surface_variant",
    "--accent": "surface_container_high", "--accent-foreground": "on_surface",
    "--destructive": "error", "--destructive-foreground": "on_error",
    "--border": "outline_variant", "--input": "outline", "--ring": "primary",
    "--sidebar": "surface_container", "--sidebar-background": "surface_container",
    "--sidebar-foreground": "on_surface", "--sidebar-primary": "primary",
    "--sidebar-accent": "surface_container_high", "--sidebar-border": "outline_variant",
    "--sidebar-ring": "primary",
  };

  const MATERIAL = {
    "primary": "primary", "on-primary": "on_primary",
    "primary-container": "primary_container", "on-primary-container": "on_primary_container",
    "secondary": "secondary", "on-secondary": "on_secondary",
    "secondary-container": "secondary_container", "on-secondary-container": "on_secondary_container",
    "tertiary": "tertiary", "on-tertiary": "on_tertiary",
    "surface": "surface", "surface-bright": "surface_bright", "surface-dim": "surface_dim",
    "surface-container": "surface_container", "surface-container-high": "surface_container_high",
    "surface-container-highest": "surface_container_highest",
    "surface-container-low": "surface_container_low",
    "surface-container-lowest": "surface_container_lowest",
    "on-surface": "on_surface", "on-surface-variant": "on_surface_variant",
    "outline": "outline", "outline-variant": "outline_variant",
    "error": "error", "on-error": "on_error",
    "background-default": "background", "background-paper": "surface_container",
    "text-primary": "on_surface", "text-secondary": "on_surface_variant",
    "divider": "outline_variant",
  };

  function matchKnownFramework(name) {
    const n = name.toLowerCase();

    /* YouTube / Polymer */
    if (n.startsWith("--yt-")) {
      if (n.includes("base-background") || n.includes("general-background-a")) return "background";
      if (n.includes("raised-background") || n.includes("menu-background")) return "surface_container";
      if (n.includes("general-background-b")) return "surface_container_low";
      if (n.includes("general-background-c")) return "surface_container_lowest";
      if (n.includes("text-primary")) return "on_surface";
      if (n.includes("text-secondary")) return "on_surface_variant";
      if (n.includes("icon-inactive")) return "outline";
      if (n.includes("icon-color") || n.includes("icon-active")) return "on_surface";
      if (n.includes("brand-background-solid") || n.includes("call-to-action") ||
          n.includes("static-brand-red") || n.includes("red-indicator") ||
          n.includes("brand-icon-active")) return "primary";
      if (n.includes("badge-chip-background")) return "surface_container_high";
      if (n.includes("button-chip-background-hover")) return "surface_bright";
      if (n.includes("10-percent-layer")) return "surface_variant";
    }

    /* Google Material / Gemini / MUI */
    if (n.startsWith("--gem-sys-color--") || n.startsWith("--mat-") ||
        n.startsWith("--bard-color-") || n.startsWith("--mui-palette-")) {
      const tail = n.replace(/^--(gem-sys-color--|mat-|bard-color-|mui-palette-)/, "").replaceAll("-main", "");
      if (MATERIAL[tail]) return MATERIAL[tail];
      if (tail.includes("app-text-color")) return "on_surface";
      if (tail.includes("background-color")) return "surface";
    }

    /* Tailwind v4 / shadcn / Radix */
    if (SHADCN[n]) return SHADCN[n];

    /* Discord */
    if (n.startsWith("--neutral-")) {
      const step = Number.parseInt(n.slice(10), 10);
      if (Number.isFinite(step)) {
        if (step >= 90) return "surface";
        if (step >= 84) return "surface_container_low";
        if (step >= 78) return "surface_container";
        if (step >= 70) return "surface_container_high";
        if (step <= 10) return "on_surface";
        if (step <= 30) return "on_surface_variant";
      }
    }
    if (n.startsWith("--brand-") || n.startsWith("--blurple-")) {
      if (n.includes("560")) return "on_primary_container";
      if (n.includes("10a") || n.includes("highlight")) return "inverse_on_surface";
      if (n.includes("60")) return "secondary_container";
      if (n.includes("50") || n.includes("65")) return "primary_container";
      return "primary";
    }

    /* Instagram */
    if (n.startsWith("--ig-")) {
      if (n.includes("primary-background")) return "surface";
      if (n.includes("elevated-background")) return "surface_container_low";
      if (n.includes("separator")) return "outline_variant";
      if (n.includes("-text") || n.includes("-icon")) return "on_surface";
    }

    /* Chess.com */
    if (n.startsWith("--color-gray-") || n.startsWith("--gray-")) {
      const step = Number.parseInt(n.replace(/\D+/g, ""), 10);
      if (step >= 800) return "surface";
      if (step >= 700) return "surface_container";
      if (step >= 600) return "surface_container_high";
      if (step >= 500) return "surface_container_highest";
      if (step >= 400) return "surface_bright";
    }
    if (n.includes("green-200")) return "primary_fixed";
    if (n.includes("green-300")) return "primary";
    if (n.includes("green-400") || n.includes("green-500")) return "primary_container";
    if (n.includes("neutrals-white")) return "on_surface";

    /* Telegram */
    if (n.includes("chat-hover")) return "surface_container_high";
    if (n.includes("chat-active")) return "primary_container";
    if (n.includes("bg-color-secondary")) return "surface_container_lowest";
    if (n.includes("compact-menu")) return "surface_container_low";
    if (n === "--theme-background-color" || n.includes("theme-bg")) return "surface";
    if (n === "--color-text" || n.includes("color-text-secondary")) return "on_surface_variant";

    /* Monkeytype */
    if (n === "--bg-color") return "surface";
    if (n === "--main-color" || n === "--text-color") return "primary";
    if (n === "--caret-color") return "primary_fixed";
    if (n === "--sub-color") return "primary_container";
    if (n === "--sub-alt-color") return "surface_container_low";

    return "";
  }

  function classifyByValueAndName(name, col) {
    const n = name.slice(2).replace(/([a-z\d])([A-Z])/g, "$1-$2").toLowerCase().replaceAll(/[_.]/g, "-");
    const lum = luminance(col.r, col.g, col.b);
    const { chroma, hue } = chromaHue(col.r, col.g, col.b);
    const fg = /(?:^|-)(?:fg|foreground|text|ink)(?:-|$)/.test(n);
    const bg = /(?:^|-)(?:bg|background|canvas|surface)(?:-|$)/.test(n);
    const muted = /(?:muted|secondary|dim|subtle|caption|hint|disabled|placeholder)/.test(n);

    // Material has no success/warning roles. Preserve those status colours automatically.
    if (/(?:^|-)(?:success|positive|warning|attention)(?:-|$)/.test(n)) return "";
    if (/(error|danger|destructive|critical|invalid)/.test(n)) return "error";
    if (/(border|divider|separator|rule|stroke)/.test(n)) return lum > 0.3 ? "outline" : "outline_variant";
    if (/outline/.test(n)) return "outline";
    if (fg) {
      if (/(?:^|-)on-(?:primary|accent|emphasis)(?:-|$)/.test(n) ||
          (/(button|btn|badge|control)/.test(n) && /primary/.test(n))) return "on_primary";
      if (/(link|accent|brand)/.test(n)) return "primary";
      return muted ? "on_surface_variant" : "on_surface";
    }
    if (bg) {
      if (/(primary|brand|accent)/.test(n)) return muted || /container/.test(n) ? "primary_container" : "primary";
      if (/(inset|sunken|lowest)/.test(n)) return "surface_container_lowest";
      if (/(hover|active|bright|emphasis)/.test(n)) return "surface_bright";
      if (/(modal|dialog|popover|dropdown|elevated|overlay)/.test(n)) return "surface_container_high";
      if (muted) return "surface_container_low";
      if (/(card|panel|container|sidebar|menu)/.test(n)) return "surface_container";
      return "background";
    }
    if (/(link|primary|brand|accent|cta)/.test(n) && chroma >= 0.1) return "primary";

    if (chroma >= 0.15) {
      if (hue >= 340 || hue <= 25) return /(btn|action|brand|primary)/.test(n) ? "primary" : "error";
      if (hue <= 65) return "tertiary";
      if (hue <= 170) return "secondary";
      if (hue <= 280) return "primary";
      return "tertiary";
    }

    if (lum <= 0.02) return /(body|canvas|bg-base|root|background|black)/.test(n)
      ? "background" : "surface_container_lowest";
    if (lum <= 0.06) return /(input|field|inset|sunken|deep)/.test(n) ? "surface_container_low" : "surface";
    if (lum <= 0.12) return /(card|panel|box|container|sidebar|nav)/.test(n)
      ? "surface_container" : "surface_container_low";
    if (lum <= 0.22) return /(modal|dialog|popover|dropdown|menu|toast|elevated)/.test(n)
      ? "surface_container_high" : "surface_container";
    if (lum <= 0.38) return /(hover|active|bright)/.test(n) ? "surface_bright" : "surface_container_highest";
    if (lum >= 0.70) return /(muted|secondary|dim|subtle|caption|hint|disabled|placeholder)/.test(n)
      ? "on_surface_variant" : "on_surface";
    return "on_surface_variant";
  }

  /* ══ Stylesheet traversal ═══════════════════════════════════════════ */
  function walkRules(list, onStyleRule) {
    for (const rule of list) {
      if (rule.styleSheet) {
        try { walkRules(rule.styleSheet.cssRules, onStyleRule); } catch { /* cross-origin */ }
        continue;
      }
      if (rule.cssRules) {
        try { walkRules(rule.cssRules, onStyleRule); } catch { /* opaque */ }
      }
      if (rule.style && (rule.selectorText || rule instanceof CSSNestedDeclarations)) onStyleRule(rule);
    }
  }

  const COLOR_PROP = /^(?:background(?:-color|-image)?|color|border(?:-(?:top|right|bottom|left|block(?:-start|-end)?|inline(?:-start|-end)?))?(?:-color)?|fill|stroke|box-shadow|text-shadow|outline(?:-color)?|caret-color|accent-color|text-decoration(?:-color)?|column-rule(?:-color)?)$/;
  const VAR_NAME = /^--(?:[-\w\u0080-\u{10FFFF}]|\\(?:[\da-fA-F]{1,6}[\t\n\f\r ]?|[^\n\r\f]))+/u;
  function variableReferences(value) {
    const refs = [];
    for (let i = 0; i < value.length; i++) {
      if (value.startsWith("/*", i)) {
        const end = value.indexOf("*/", i + 2);
        if (end < 0) break;
        i = end + 1;
        continue;
      }
      if (value[i] === '"' || value[i] === "'") {
        const quote = value[i++];
        for (; i < value.length; i++) {
          if (value[i] === "\\") i++;
          else if (value[i] === quote) break;
        }
        continue;
      }
      if (value[i] === "\\") { i++; continue; }
      if (value.slice(i, i + 4).toLowerCase() !== "var(" ||
          (i && /[-\w\u0080-\uFFFF]/u.test(value[i - 1]))) continue;
      let start = i + 4;
      while (start < value.length) {
        if (/[\t\n\f\r ]/.test(value[start])) { start++; continue; }
        if (!value.startsWith("/*", start)) break;
        const end = value.indexOf("*/", start + 2);
        if (end < 0) break;
        start = end + 2;
      }
      const raw = VAR_NAME.exec(value.slice(start))?.[0];
      if (!raw) continue;
      const name = raw.replace(/\\([\da-fA-F]{1,6})[\t\n\f\r ]?|\\(.)/gu, (_, hex, char) => {
        if (!hex) return char;
        const cp = Number.parseInt(hex, 16);
        return String.fromCodePoint(cp && cp <= 0x10FFFF && !(cp >= 0xD800 && cp <= 0xDFFF) ? cp : 0xFFFD);
      });
      refs.push({ name, start, end: start + raw.length });
      i = start + raw.length - 1;
    }
    return refs;
  }
  const varRefs = (value) => variableReferences(value).map((ref) => ref.name);

  /* Pending-substitution shorthands can enumerate empty longhands in CSSOM.
   * Read their authored shorthand values as well. */
  const COLOR_SHORTHANDS = ["background", "border", "border-color", "border-top",
    "border-right", "border-bottom", "border-left", "border-block", "border-inline",
    "border-block-start", "border-block-end", "border-inline-start", "border-inline-end",
    "outline", "text-decoration", "column-rule"];
  const INHERITED_COLOR_PROP = /^(?:color|fill|stroke|caret-color|text-shadow)$/;
  function colorReferences(style) {
    const refs = new Set(), inheritedRefs = new Set(), pending = new Set();
    const collect = (p, value) => {
      for (const name of varRefs(value)) {
        refs.add(name);
        if (INHERITED_COLOR_PROP.test(p)) inheritedRefs.add(name);
      }
    };
    for (const p of style) {
      if (!COLOR_PROP.test(p)) continue;
      const value = style.getPropertyValue(p);
      if (value) collect(p, value);
      else {
        // Only inspect shorthands when CSSOM exposes an unresolved longhand.
        const family = p.split("-")[0];
        for (const shorthand of COLOR_SHORTHANDS) if (shorthand.startsWith(family)) pending.add(shorthand);
      }
    }
    for (const p of pending) collect(p, style.getPropertyValue(p));
    return { refs: [...refs], inheritedRefs: [...inheritedRefs] };
  }
  const colorRefs = (style, inheritedOnly = false) => colorReferences(style)[inheritedOnly ? "inheritedRefs" : "refs"];

  let customPropIndex = null;
  let indexWatermark = -1;

  function expandNesting(selector, outer) {
    let result = "", quote = "", nested = false;
    for (let i = 0; i < selector.length; i++) {
      const c = selector[i];
      if (c === "\\") { result += c + (selector[++i] ?? ""); continue; }
      if (quote) {
        result += c;
        if (c === quote) quote = "";
      } else if (c === '"' || c === "'") {
        quote = c; result += c;
      } else if (selector.startsWith("/*", i)) {
        const end = selector.indexOf("*/", i + 2);
        if (end < 0) return selector;
        result += selector.slice(i, end + 2); i = end + 1;
      } else if (c === "&") {
        result += outer; nested = true;
      } else result += c;
    }
    return nested ? result : outer + " :is(" + result + ")";
  }

  function ruleSelector(rule) {
    if (rule instanceof CSSNestedDeclarations) {
      for (let parent = rule.parentRule; parent; parent = parent.parentRule) {
        if (parent.selectorText) return ruleSelector(parent);
      }
      return "";
    }
    let sel = rule.selectorText;
    for (let parent = rule.parentRule; parent; parent = parent.parentRule) {
      if (!parent.selectorText) continue;
      const outer = ":is(" + parent.selectorText + ")";
      sel = expandNesting(sel, outer);
    }
    return sel;
  }

  function ruleActive(rule) {
    for (let sheet = rule.parentStyleSheet; sheet; sheet = sheet.parentStyleSheet) {
      if (sheet.disabled || (sheet.media.length && !matchMedia(sheet.media.mediaText).matches)) return false;
      const imported = sheet.ownerRule;
      if (imported instanceof CSSImportRule &&
          ((imported.media.length && !matchMedia(imported.media.mediaText).matches) ||
           (imported.supportsText && !CSS.supports(`(${imported.supportsText})`)))) return false;
    }
    for (let parent = rule.parentRule; parent; parent = parent.parentRule) {
      if (parent instanceof CSSMediaRule && !matchMedia(parent.conditionText).matches) return false;
      if (parent instanceof CSSSupportsRule && !CSS.supports(parent.conditionText)) return false;
      // There is no CSSOM API to evaluate container conditions for an element.
      // Keep computed-token discovery, without inventing a declaration owner.
      if (parent instanceof CSSContainerRule || parent instanceof CSSStartingStyleRule) return false;
    }
    return true;
  }

  function buildCustomPropIndex() {
    const index = [];
    for (const sheet of [...document.styleSheets, ...document.adoptedStyleSheets]) {
      if (sheet === SHEET) continue; // our own live mappings are not site definitions
      try {
        walkRules(sheet.cssRules, (rule) => {
          const props = [];
          for (const p of rule.style) if (p.startsWith("--")) props.push(p);
          const { refs, inheritedRefs } = colorReferences(rule.style);
          if (props.length || refs.length) index.push({ sel: ruleSelector(rule), props, refs,
            inheritedRefs, rule });
        });
      } catch { /* cross-origin */ }
    }
    return index;
  }

  /* Invalidate when the document gains or loses a stylesheet (SPA chunks). */
  function propIndex() {
    const count = document.styleSheets.length + document.adoptedStyleSheets.filter((sheet) => sheet !== SHEET).length;
    if (customPropIndex === null || indexWatermark !== count) {
      customPropIndex = buildCustomPropIndex();
      indexWatermark = count;
    }
    return customPropIndex.filter((entry) => ruleActive(entry.rule));
  }
  const dropIndex = () => { customPropIndex = null; indexWatermark = -1; };

  function ruleMatches(node, entry) {
    try {
      for (let parent = entry.rule.parentRule; parent; parent = parent.parentRule) {
        if (!(parent instanceof CSSScopeRule)) continue;
        const scope = parent.start ? node.closest(parent.start) : entry.rule.parentStyleSheet.ownerNode?.parentElement;
        if (!scope) return false;
        for (let n = node; n && n !== scope; n = n.parentElement) {
          if (parent.end && n.matches(parent.end)) return false;
        }
        // Relative scoping selectors need scope-aware matching, not Element.matches().
        if (entry.sel.includes(":scope") || entry.sel.includes("&")) return false;
      }
      return node.matches(entry.sel);
    } catch { return false; }
  }

  function closestRule(node, entry) {
    // A nearer selector match can be outside @scope's limit while a further
    // ancestor still owns the inherited token. Keep searching after that miss.
    for (let match = node?.closest(entry.sel); match;
         match = match.parentElement?.closest(entry.sel)) {
      if (ruleMatches(match, entry)) return match;
    }
    return null;
  }

  const componentSuffix = (col) => col.shape === "rgb-comma" ? "_rgb_comma"
    : col.shape === "rgb-triplet" ? "_rgb" : col.shape === "hsl-triplet" ? "_hsl" : "";
  function paletteValue(token, col) {
    const value = `var(--${token}${componentSuffix(col)})`;
    return !componentSuffix(col) && col.a < 1
      ? `color-mix(in srgb, ${value} ${+(col.a * 100).toFixed(4)}%, transparent)` : value;
  }

  /* Escape for use inside a quoted CSS attribute value. CSS.escape() is the
   * wrong tool here — it escapes identifiers, not string contents. */
  const cssString = (v) => `"${String(v).replaceAll(/["\\]/g, "\\$&")
    .replaceAll(/[\n\r\f]/g, (c) => `\\${c.charCodeAt(0).toString(16)} `)}"`;

  function bodyTokenNames() {
    const names = new Set();
    const body = document.body;
    if (!body) return names;
    const collect = (style, props) => {
      for (const name of props) if (name.startsWith("--") &&
          !/^(?:inherit|unset)$/i.test(style.getPropertyValue(name).trim())) names.add(name);
    };
    collect(body.style, body.style);
    for (const r of propIndex()) if (r.props.length && ruleMatches(body, r)) collect(r.rule.style, r.props);
    return names;
  }

  function collectVariables() {
    const names = new Set();
    const rootCs = getComputedStyle(document.documentElement);
    const bodyCs = document.body ? getComputedStyle(document.body) : rootCs;
    for (const cs of new Set([rootCs, bodyCs])) {
      for (const p of cs) if (p.startsWith("--")) names.add(p);
    }
    const rootish = /(^|,)\s*(?::root|html|body|\[dark\]|\.dark|\[data-theme)/i;
    for (const { sel, props } of propIndex()) {
      if (rootish.test(sel)) for (const p of props) names.add(p);
    }
    const out = new Map();
    for (const n of names) {
      const rootValue = rootCs.getPropertyValue(n).trim();
      const value = rootValue || bodyCs.getPropertyValue(n).trim();
      if (value) out.set(n, { value, context: rootValue ? document.documentElement : document.body });
    }
    return out;
  }

  /* Broad structural repaint, used only when there are no mappable tokens. */
  function structuralFallback() {
    return [
      "/* Structural theme — this page exposes no usable design tokens. */",
      "html, body {",
      "    background-color: var(--surface) !important;",
      "    color: var(--on_surface) !important;",
      "    color-scheme: dark !important;",
      "}",
      ":where(:not(a):not(button):not(input):not(select):not(textarea):not(code):not(pre)" +
        ":not(table):not(svg):not(img):not(video):not([class*='icon']):not([class*='badge']):not([class*='btn'])) {",
      "    background-color: transparent !important;",
      "    color: inherit !important;",
      "}",
      "header, nav, aside, footer, main > section, article,",
      "[class*='card'], [class*='panel'], [class*='sidebar'], dialog, [role='dialog'] {",
      "    background-color: var(--surface_container) !important;",
      "    border-color: var(--outline_variant) !important;",
      "    color: var(--on_surface) !important;",
      "}",
      "code, pre, kbd, samp {",
      "    background-color: var(--surface_container_high) !important;",
      "    color: var(--on_surface) !important;",
      "}",
      "a { color: var(--primary) !important; }",
      "a:hover { color: var(--primary_fixed) !important; }",
      "a:visited { color: var(--tertiary) !important; }",
      "input, select, textarea {",
      "    background-color: var(--surface_container_low) !important;",
      "    color: var(--on_surface) !important;",
      "    border-color: var(--outline) !important;",
      "}",
      "button, [type='submit'], [role='button'] {",
      "    background-color: var(--primary) !important;",
      "    color: var(--on_primary) !important;",
      "    border-color: var(--outline_variant) !important;",
      "}",
      "table, th, td { border-color: var(--outline_variant) !important; }",
      "th { background-color: var(--surface_container_high) !important; }",
      "hr { border-color: var(--outline_variant) !important; }",
      "::selection {",
      "    background-color: var(--primary_container) !important;",
      "    color: var(--on_primary_container) !important;",
      "}",
      "::placeholder { color: var(--on_surface_variant) !important; }",
      "* { scrollbar-color: var(--outline) var(--surface_container_low) !important; }",
    ].join("\n");
  }

  const indent = (lines) => lines.map((l) => (l ? `    ${l}` : "")).join("\n");

  /* Raw swatch dumps (pink-100 … pink-900) are not semantic theme tokens. */
  const SWATCH_DUMP =
    /^--(pink|orange|yellow|purple|cyan|teal|lime|amber|violet|fuchsia|rose|emerald|sky)-[0-9]+[a-z]?$/;
  const isSemanticThemeVar = (name) => !SWATCH_DUMP.test(name.toLowerCase());

  function scan() {
    dropIndex();
    const groups = new Map();
    const unmapped = [];
    const variables = [];
    let found = 0;

    for (const [name, { value, context }] of collectVariables()) {
      if (skipVar(name) || !isSemanticThemeVar(name)) continue;
      const col = parseCssColor(value, context);
      if (!col || col.a === 0) continue;
      found++;
      const token = matchKnownFramework(name) || classifyByValueAndName(name, col);
      if (!token || !TOKEN_NAMES.has(token)) { unmapped.push(name); continue; }
      if (`--${token}` === name) continue;
      variables.push({ name, col, token, global: true });
    }
    verifyVariableInheritance(variables, () => "");
    for (const { name, col, token, local } of variables) {
      if (local) { unmapped.push(name); continue; }
      const key = col.shape === "rgb-comma" ? `${token}\u0000rgb_comma`
        : col.shape === "rgb-triplet" ? `${token}\u0000rgb`
        : col.shape === "hsl-triplet" ? `${token}\u0000hsl` : token;
      (groups.get(key) ?? groups.set(key, []).get(key)).push({ name, col });
    }

    const body = [];
    let mapped = 0;
    let variableCount = 0;
    for (const list of groups.values()) variableCount += list.length;

    const kind = variableCount ? "tokens" : "structural";

    if (kind === "tokens") {
      body.push(":root {", "    color-scheme: dark !important;");
      for (const [token] of TOKENS) {
        for (const suffix of ["", "\u0000rgb", "\u0000rgb_comma", "\u0000hsl"]) {
          const names = groups.get(token + suffix);
          if (!names) continue;
          const label = suffix === "\u0000rgb" ? `${token} (rgb components)`
            : suffix === "\u0000hsl" ? `${token} (hsl components)` : token;
          body.push(`    /* ${label} */`);
          for (const { name, col } of names.toSorted((a, b) => a.name.localeCompare(b.name))) {
            body.push(`    ${CSS.escape(name)}: ${paletteValue(token, col)} !important;`);
            mapped++;
          }
        }
      }
      body.push("}");
      // Inheritance cannot override a declaration on body. Override only its
      // own tokens there; root tokens continue to resolve at the page root.
      const bodyNames = bodyTokenNames();
      const bodyCs = document.body && getComputedStyle(document.body);
      const bodyMappings = [];
      for (const { name, token, local } of variables) {
        if (local || !bodyNames.has(name)) continue;
        const col = parseCssColor(bodyCs.getPropertyValue(name), document.body);
        if (col && col.a > 0) bodyMappings.push(`    ${CSS.escape(name)}: ${paletteValue(token, col)} !important;`);
      }
      if (bodyMappings.length) body.push("", "body {", ...bodyMappings, "}");
    } else {
      body.push(structuralFallback());
      mapped = variableCount || 1;
    }

    if (unmapped.length) {
      const sorted = unmapped.toSorted();
      const shown = sorted.slice(0, 30).join(", ") +
        (sorted.length > 30 ? `, +${sorted.length - 30} more` : "");
      body.push("", `/* unmapped: ${shown} */`);
    }

    dropProbe();
    return {
      ok: true, kind, found, mapped, palette: paletteLoaded(),
      body: indent(body.join("\n").split("\n")),
    };
  }

  /* ══ VISUAL PICKER ══════════════════════════════════════════════════ */
  const LIMITS = { RULES: 600, DECL: 4096, SEL: 512, BODY: 512 * 1024 };

  const S = {
    active: false, hydrated: false, note: "", rev: 0, warnings: [],
    rules: Object.freeze([]), undo: [], redo: [], stack: [], depth: 0,
    locked: false, targetMode: "selector", group: "bg",
    elementVars: [], raf: 0, editKey: null, generation: 0,
  };

  const US = "\u001F";
  const ROOT_ARMOR = ":root:root:root";
  const ORIGIN = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

  /* ── Rule model ────────────────────────────────────────────────────── */
  /* encodeURIComponent leaves !'()* alone and KEY_RE stops at '*', so a
   * selector like [class*="icon"] would round-trip TRUNCATED. Escape the full
   * RFC 3986 sub-delims set. */
  const enc = (s) => encodeURIComponent(String(s))
    .replaceAll(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
  const dec = (s) => { try { return decodeURIComponent(s); } catch { return s; } };
  const KEY_RE = /\/\*\s*dusky\s+key=([^\s*|]+)\s*(?:\|\s*(.*?)\s*)?\*\/\s*$/;

  const oneLine = (s) => String(s ?? "").replaceAll(/\s*[\r\n]+\s*/g, " ").trim();
  const safeMeta = (s) => oneLine(s).replaceAll("*/", "* /").slice(0, 160);

  const selKey = (sel, group) => `sel${US}${sel}${US}${group}`;
  // Earlier picker versions wrote the same root target with repeated specificity.
  const varKey = (scope, name) => `var${US}${scope === ROOT_ARMOR ? ":root" : scope}${US}${name}`;
  const rawKey = (css) => `raw${US}${css}`;

  /* One parser for every key shape — no more positional indexing. */
  function decodeKey(key) {
    const [kind, a, b] = String(key ?? "").split(US);
    if (kind === "var" && a && b?.startsWith("--")) return { kind: "var", scope: a, name: b };
    if (kind === "raw" && a !== undefined) return { kind: "raw", css: a };
    if (kind === "sel" && a && b) return { kind: "sel", sel: a, group: b };
    return { kind: "invalid" };
  }

  const declStyle = document.createElement("div").style;
  const propsOf = (decl) => {
    declStyle.cssText = decl;
    return [...declStyle].toSorted();
  };

  const PROP_GROUP = [
    [/^background(-color|-image)?$/, "bg"], [/^color$/, "text"],
    [/^border(-[a-z]+)?-color$/, "border"], [/^fill$/, "fill"], [/^display$/, "display"],
  ];
  function groupOfDecl(decl) {
    const props = propsOf(decl);
    if (props.length === 1 && props[0].startsWith("--")) return "var";
    if (props.some((p) => !COLOR_PROP.test(p) && p !== "display")) return `custom:${props.join(",")}`;
    if (props.includes("fill")) return "fill";
    for (const [re, g] of PROP_GROUP) if (props.some((p) => re.test(p))) return g;
    return `custom:${props.join(",")}`;
  }
  const tokenOf = (decl) => varRefs(String(decl ?? "")).find((name) =>
    PALETTE_OWNED.has(name.slice(2).replace(PALETTE_SUFFIX, "")))?.slice(2) ?? "";
  function remapPalette(decl, token) {
    let result = String(decl ?? "");
    for (const { name, start, end } of variableReferences(result).reverse()) {
      if (!PALETTE_OWNED.has(name.slice(2).replace(PALETTE_SUFFIX, ""))) continue;
      result = result.slice(0, start) + `--${token}${name.match(/_(?:rgb_comma|rgb|hsl)$/)?.[0] ?? ""}` + result.slice(end);
    }
    return result;
  }
  function remapVariable(name, decl, token) {
    if (tokenOf(decl)) return remapPalette(decl, token);
    declStyle.cssText = decl;
    const col = parseCssColor(declStyle.getPropertyValue(name));
    return `${CSS.escape(name)}: ${paletteValue(token, col ?? { shape: "color", a: 1 })} !important;`;
  }

  function splitRule(text) {
    let depth = 0, inStr = 0, selEnd = -1, bodyStart = -1, bodyEnd = -1;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inStr) { if (c === "\\") i++; else if (c === inStr) inStr = 0; continue; }
      if (c === "\\") { i++; continue; }
      if (text.startsWith("/*", i)) {
        const end = text.indexOf("*/", i + 2);
        if (end < 0) return null;
        i = end + 1;
        continue;
      }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === "{") { if (depth++ === 0) { selEnd = i; bodyStart = i + 1; } continue; }
      if (c === "}") { if (--depth === 0) { bodyEnd = i; break; } }
    }
    if (selEnd < 0 || bodyEnd < 0 || text.slice(bodyEnd + 1).trim()) return null;
    return { sel: text.slice(0, selEnd).trim(), decl: text.slice(bodyStart, bodyEnd).trim() };
  }

  let ruleProbe = null;
  function parseRule(line) {
    const trimmed = line.trim();
    if (!trimmed) return null;
    const km = KEY_RE.exec(trimmed);
    const storedKey = km ? dec(km[1]) : "";
    const meta0 = km ? (km[2] ?? "").trim() : "";
    const css = km ? trimmed.slice(0, km.index).trim() : trimmed;
    if (!css) return null;

    const parts = css.includes("\n") ? null : splitRule(css);
    if (!parts?.sel || !parts?.decl || parts.sel.startsWith("@") || !propsOf(parts.decl).length) {
      return { raw: css, meta: meta0 || "manual", key: rawKey(css) };
    }
    if (parts.decl.includes("{")) {
      // Braces may introduce nested rules or simply belong to a string/custom
      // property. Let Gecko distinguish them; nested CSS needs the raw editor.
      (ruleProbe ??= new CSSStyleSheet()).replaceSync(css);
      if (ruleProbe.cssRules.length !== 1 || !(ruleProbe.cssRules[0] instanceof CSSStyleRule) ||
          ruleProbe.cssRules[0].cssRules.length) {
        return { raw: css, meta: meta0 || "manual", key: rawKey(css) };
      }
    }
    const decl = oneLine(parts.decl);
    const group = groupOfDecl(decl);
    const derived = group === "var" ? varKey(parts.sel, propsOf(decl)[0]) : selKey(parts.sel, group);
    /* A stored key is honoured only when it parses AND its shape matches the
     * CSS it labels; otherwise the CSS is authoritative (self-healing files). */
    const stored = decodeKey(storedKey);
    const keep = stored.kind === "var"
      ? group === "var" && stored.scope === parts.sel && stored.name === propsOf(decl)[0]
      : stored.kind === "sel" && group !== "var" && stored.sel === parts.sel && stored.group === group;
    return { sel: parts.sel, decl, meta: meta0 || "restored",
      key: keep && stored.kind === "var" ? varKey(stored.scope, stored.name) : keep ? storedKey : derived };
  }
  /* Split only at top-level newlines. A rule inside a multiline comment must
   * never acquire metadata that would prematurely close the comment. */
  function parseLines(text) {
    const source = String(text ?? "");
    const rules = [];
    let start = 0, depth = 0, quote = "", comment = false;
    const push = (end) => {
      const rule = parseRule(source.slice(start, end));
      if (rule) rules.push(rule);
      start = end + 1;
    };
    for (let i = 0; i < source.length; i++) {
      const c = source[i];
      if (comment) {
        if (source.startsWith("*/", i)) { comment = false; i++; }
        continue;
      }
      if (c === "\\") { i++; continue; }
      if (quote) {
        if (c === quote) quote = "";
        continue;
      }
      if (source.startsWith("/*", i)) { comment = true; i++; continue; }
      if (c === '"' || c === "'") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") depth = Math.max(0, depth - 1);
      else if (c === "\n" && depth === 0) push(i);
    }
    push(source.length);
    return rules;
  }

  const ruleCss = (r) => (r.raw !== undefined ? r.raw : `${r.sel} { ${r.decl} }`);
  const ruleLine = (r) =>
    r.raw !== undefined ? r.raw : `${ruleCss(r)} /* dusky key=${enc(r.key)}${r.meta ? ` | ${safeMeta(r.meta)}` : ""} */`;
  const serialise = () => S.rules.map((r) => `    ${ruleLine(r)}`).join("\n");

  const target = () => S.stack.at(S.depth) ?? null;

  const GROUPS = {
    bg: { label: "Background", extra: { label: "👻 Transparent", css: "background: transparent !important; box-shadow: none !important;", meta: "bg: transparent" } },
    text: { label: "Text", extra: { label: "↩ Inherit colour", css: "color: inherit !important;", meta: "text: inherit" } },
    border: { label: "Border", extra: { label: "⊘ No border", css: "border-color: transparent !important;", meta: "border: none" } },
    fill: { label: "Fill", extra: { label: "🎨 currentColor", css: "fill: currentColor !important;", meta: "fill: currentColor" } },
  };

  const PAIRED_ON = {
    primary: "on_primary", primary_container: "on_primary_container",
    secondary: "on_secondary", secondary_container: "on_secondary_container",
    tertiary: "on_tertiary", tertiary_container: "on_tertiary_container",
    error: "on_error", error_container: "on_error_container", background: "on_background",
  };

  function declFor(group, token) {
    if (group === "text") return `color: var(--${token}) !important;`;
    if (group === "border") return `border-color: var(--${token}) !important;`;
    if (group === "fill") return `fill: var(--${token}) !important; color: var(--${token}) !important;`;
    const on = PAIRED_ON[token] ?? (token.startsWith("surface") ? "on_surface" : "");
    return `background-color: var(--${token}) !important;` +
      (on ? ` color: var(--${on}) !important; border-color: var(--outline_variant) !important;` : "");
  }
  const colourGroup = (g) => (GROUPS[g] ? g : "bg");

  const important = (text) => {
    const style = document.createElement("div").style;
    style.cssText = text;
    for (const name of style) style.setProperty(name, style.getPropertyValue(name), "important");
    return style.cssText;
  };

  function validSelector(sel) {
    if (typeof sel !== "string" || !sel || sel.length > LIMITS.SEL) return false;
    try { document.querySelector(sel); return true; } catch { return false; }
  }

  /* Validate a declaration list by parsing it into a detached style object. */
  function normaliseDecl(text) {
    const src = oneLine(text);
    if (!src || src.length > LIMITS.DECL) return "";
    const probe = document.createElement("div");
    try { probe.style.cssText = src; } catch { return ""; }
    if (!probe.style.length) return "";
    return important(probe.style.cssText);
  }

  function verifyVariableInheritance(variables, localScope) {
    const candidates = variables.filter((v) => !v.local);
    if (!candidates.length) return;
    // Let Gecko resolve inheritance, including properties registered from page JS.
    // Two values distinguish inheritance from a matching registered initial value.
    const parent = document.createElement("span");
    const child = document.createElement("span");
    parent.style.setProperty("display", "none", "important");
    parent.append(child);
    root.append(parent);
    const detached = !hostEl.isConnected;
    if (detached) document.documentElement.append(hostEl);
    const marker = (v, second) => {
      if (v.col.shape === "rgb-comma") return second ? "4, 5, 6" : "1, 2, 3";
      if (v.col.shape === "rgb-triplet") return second ? "4 5 6" : "1 2 3";
      if (v.col.shape === "hsl-triplet") return second ? "120 50% 40%" : "240 50% 40%";
      return second ? "rgb(4, 5, 6)" : "rgb(1, 2, 3)";
    };
    try {
      for (const v of candidates) parent.style.setProperty(v.name, marker(v, false));
      const firstParent = getComputedStyle(parent), firstChild = getComputedStyle(child);
      const first = candidates.map((v) => [firstParent.getPropertyValue(v.name), firstChild.getPropertyValue(v.name)]);
      for (const v of candidates) parent.style.setProperty(v.name, marker(v, true));
      const secondParent = getComputedStyle(parent), secondChild = getComputedStyle(child);
      candidates.forEach((v, i) => {
        const value = secondParent.getPropertyValue(v.name);
        if (value !== first[i][0] && first[i][0] === first[i][1] && value === secondChild.getPropertyValue(v.name)) return;
        v.global = false;
        v.local = true;
        v.scope = localScope();
      });
    } finally {
      parent.remove();
      if (detached) hostEl.remove();
    }
  }

  function getElementVars(elm) {
    if (elm?.nodeType !== 1) return [];
    const cs = getComputedStyle(elm);
    const rootCs = getComputedStyle(document.documentElement);
    const out = new Map();
    const index = propIndex();
    const owners = new Map();
    const scopes = new Map();
    const directRefs = new Set(colorRefs(elm.style));
    const inheritedRefs = new Set();
    const depth = new Map();
    for (let node = elm; node; node = node.parentElement) {
      depth.set(node, depth.size);
      const own = node !== document.documentElement;
      for (const name of node.style) {
        if (own && name.startsWith("--") && !/^(?:inherit|unset)$/i.test(node.style.getPropertyValue(name).trim()) && !owners.has(name)) owners.set(name, node);
      }
      if (node !== elm) for (const name of colorRefs(node.style, true)) inheritedRefs.add(name);
    }
    for (const r of index) {
      if (r.refs.length && ruleMatches(elm, r)) for (const name of r.refs) directRefs.add(name);
      // closest() performs the selector search in Gecko; scope checks can skip
      // a nearer match excluded by a limit without losing the inherited owner.
      let owner, ancestor;
      try {
        if (r.props.length) owner = closestRule(elm, r);
        if (r.inheritedRefs.length) ancestor = closestRule(elm.parentElement, r);
      } catch { continue; }
      if (ancestor) for (const name of r.inheritedRefs) inheritedRefs.add(name);
      if (!owner || owner === document.documentElement) continue;
      for (const name of r.props) {
        if (/^(?:inherit|unset)$/i.test(r.rule.style.getPropertyValue(name).trim())) continue;
        if (!owners.has(name) || depth.get(owner) < depth.get(owners.get(name))) owners.set(name, owner);
      }
    }
    const rootScope = ":root";
    const scopeFor = (node) => {
      if (!scopes.has(node)) scopes.set(node, candidates(node).at(0)?.sel || selected());
      return scopes.get(node);
    };
    const add = (name, used = false) => {
      if (skipVar(name)) return;
      if (out.has(name)) { out.get(name).used ||= used; return; }
      const value = cs.getPropertyValue(name).trim();
      const col = parseCssColor(value, elm);
      if (!col || col.a === 0) return;
      /* A child declaration beats an inherited root declaration, regardless
       * of the root selector's specificity. Suggest the nearest owner. */
      const owner = owners.get(name);
      const global = !owner && rootCs.getPropertyValue(name).trim() === value;
      const scope = global ? rootScope : scopeFor(owner ?? elm);
      out.set(name, { name, value: value.length > 44 ? value.slice(0, 41) + "…" : value,
        used, scope: scope || selected(), global, local: owner === elm,
        col });
    };
    /* Read authored declarations: computed colours have lost their var() references. */
    for (const name of directRefs) add(name, true);
    // Text and SVG colours can be inherited without a declaration on the clicked node.
    for (const name of inheritedRefs) add(name, true);
    for (const cls of elm.classList) {
      const name = /^[a-z-]+-\((--[\w-]+)\)$/.exec(cls)?.[1];
      if (name) add(name, true);
      const tok = /^[a-z-]+-token-([\w-]+)$/.exec(cls)?.[1];
      if (tok) add("--" + tok, true);
    }
    /* Computed custom properties remain available even for opaque CDN sheets.
     * Offer their colour tokens after directly referenced ones. */
    for (const name of cs) if (name.startsWith("--")) add(name);
    verifyVariableInheritance([...out.values()], () => scopeFor(elm));
    dropProbe();
    return [...out.values()].sort((a, b) => Number(b.used) - Number(a.used) ||
      Number(isSemanticThemeVar(b.name)) - Number(isSemanticThemeVar(a.name)) || a.name.localeCompare(b.name));
  }

  function resetVarSelection() {
    const variable = q("pvar");
    const scope = q("pvarscope");
    if (variable) variable.value = "";
    if (scope) scope.dataset.variable = "";
  }

  function refreshVarScope() {
    const variable = S.elementVars.find((v) => v.name === q("pvar")?.value);
    const box = q("pvarscope");
    const previous = box.dataset.variable === variable?.name ? box.value : "";
    box.dataset.variable = variable?.name ?? "";
    box.textContent = "";
    if (!variable) return;
    const scopes = new Map([[variable.scope, variable.global ? "Page theme (root)" : "Variable owner"]]);
    scopes.set(":root", "Page theme (root)");
    const sel = selected();
    if (sel && !scopes.has(sel)) scopes.set(sel, "Selected elements");
    for (const [scope, label] of scopes) {
      box.append(el("option", { value: scope, text: label }));
    }
    box.value = scopes.has(previous) ? previous : variable.scope;
  }

  const variableScope = () => q("pvarscope")?.value || ":root";
  const variablePalette = (token) => paletteValue(token,
    S.elementVars.find((v) => v.name === q("pvar")?.value)?.col ?? { shape: "color", a: 1 });

  /* ══ STYLE ENGINE — one adopted sheet, two cascade layers ═══════════ */
  /* @layer dusky.preview, dusky.live;  ⇒ for !important declarations the
   * EARLIEST layer wins (CSS Cascade 5 §layer ordering), so the preview always
   * beats the live rule, and both beat the site's unlayered !important CSS.
   * Inline important and earlier page layers retain their CSS-defined priority. */
  const SHEET = new CSSStyleSheet();
  let liveCss = "";
  let previewRules = [];
  let appliedLive = null, appliedPreview = null;
  let previewLayer = null;

  function flushSheet() {
    if (!S.active) return;
    const previewCss = previewRules.join("\n");
    if (liveCss !== appliedLive) {
      SHEET.replaceSync(
        "@layer dusky.preview, dusky.live;\n" +
        `@layer dusky.live{\n${liveCss}\n}\n`,
      );
      // Parse manual live CSS first. Its EOF recovery (an unfinished comment
      // or brace) must never consume the independently inserted preview layer.
      const index = SHEET.insertRule("@layer dusky.preview {}", SHEET.cssRules.length);
      previewLayer = SHEET.cssRules[index];
      appliedLive = liveCss;
      appliedPreview = null;
    }
    if (previewCss !== appliedPreview) {
      // Hover changes at most an outline and a mapping. Keep the live rules
      // parsed instead of rebuilding the entire picked stylesheet each time.
      while (previewLayer.cssRules.length) previewLayer.deleteRule(previewLayer.cssRules.length - 1);
      for (const css of previewRules) previewLayer.insertRule(css, previewLayer.cssRules.length);
      appliedPreview = previewCss;
    }
    if (!document.adoptedStyleSheets.includes(SHEET)) {
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, SHEET];
    }
  }
  function detachSheet() {
    liveCss = ""; previewRules = [];
    appliedLive = appliedPreview = null;
    previewLayer = null;
    document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== SHEET);
  }

  const renderLive = () => { liveCss = S.rules.map(ruleCss).join("\n"); flushSheet(); };

  const OUTLINE = "outline:2px dashed #e6c280 !important;outline-offset:-2px !important";

  /* THE single preview entry point — pick mode, edit mode, chips, drawer rows
   * and the extra buttons all go through here, so they cannot diverge. */
  const Preview = {
    show(spec) {
      if (!spec) return Preview.clear();
      const chunks = [];
      if (spec.highlight && validSelector(spec.highlight)) {
        chunks.push(`${spec.highlight}{${OUTLINE}}`);
      }
      if (spec.css) chunks.push(spec.css);
      previewRules = chunks;
      flushSheet();
    },
    clear() { previewRules = []; flushSheet(); },
  };

  /* Build a preview spec for ANY rule key + token. Used by edit mode. */
  function previewForKey(key, sel, decl, token) {
    const k = decodeKey(key);
    if (k.kind === "var") {
      const scope = sel || k.scope || ROOT_ARMOR;
      if (!validSelector(scope)) return { highlight: "", css: "" };
      return token
        ? { highlight: scope === ROOT_ARMOR ? "" : scope, css: `${scope}{${remapVariable(k.name, decl, token)}}` }
        : { highlight: scope === ROOT_ARMOR ? "" : scope, css: "" };
    }
    if (k.kind === "raw") return { highlight: "", css: "" };
    const useSel = sel || k.sel;
    if (!validSelector(useSel)) return { highlight: "", css: "" };
    if (!token) return { highlight: useSel, css: "" };
    if (k.group === "display") {
      /* A hidden element cannot show a colour: preview the un-hide instead. */
      return { highlight: useSel, css: `${useSel}{display:revert !important;${OUTLINE}}` };
    }
    if (k.group?.startsWith("custom:")) {
      /* Substitute the token into the existing var() if the rule has one,
       * otherwise just highlight — never lie with an unrelated background. */
      const swapped = remapPalette(decl, token);
      return { highlight: useSel, css: swapped === decl ? "" : `${useSel}{${swapped}}` };
    }
    return { highlight: useSel, css: `${useSel}{${declFor(colourGroup(k.group), token)}}` };
  }

  /* ══ Shadow UI ══════════════════════════════════════════════════════ */
  const UI_CSS = `
:host{all:initial!important;display:block!important;position:fixed!important;inset:0 auto auto 0!important;width:0!important;height:0!important;overflow:visible!important;z-index:2147483647!important;pointer-events:none!important;isolation:isolate!important}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
button,input,select,textarea{font:inherit;color:inherit;background:none;border:0}
.mask{position:fixed;display:none;pointer-events:none;outline:2px dashed #e6c280;outline-offset:-2px;background:rgba(230,194,128,.07);border-radius:2px}
.panel{position:fixed;pointer-events:auto;background:#191614;color:#f5ebe0;border:1px solid #3d342c;border-radius:10px;box-shadow:0 14px 44px rgba(0,0,0,.65);font:13px/1.45 system-ui,sans-serif;display:flex;flex-direction:column;gap:6px;padding:8px;transition:opacity .12s ease}
.panel.ghost{opacity:.25}
.panel.ghost:hover,.panel.ghost:focus-within{opacity:1}
.bar{top:10px;left:50%;transform:translateX(-50%);flex-direction:row;align-items:center;gap:6px;padding:6px 8px;max-width:min(96vw,980px)}
.dlg{top:var(--dusky-dialog-top,64px);right:16px;width:440px;max-width:calc(100vw - 32px);max-height:calc(100vh - var(--dusky-dialog-top,64px) - 24px);overflow:auto}
.drawer{right:16px;bottom:16px;width:420px;max-width:calc(100vw - 32px);max-height:62vh;overflow:hidden}
.head{display:flex;align-items:center;gap:6px;cursor:grab;user-select:none}
.head:active{cursor:grabbing}
.grip{color:#6d645a;font-size:12px}
.title{flex:1;font-weight:700;color:#e6c280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.info{color:#c4b8aa;font:11.5px ui-monospace,monospace;max-width:38ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.state{font-size:11px;color:#c4b8aa;max-width:34ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.state.ok{color:#81c784}.state.err{color:#e57373}.state.warn{color:#e6c280}
.panel button{cursor:pointer;background:#2d2722;border:1px solid #3d342c;border-radius:7px;padding:4px 8px;color:#f5ebe0;white-space:nowrap}
.panel button:hover:not(:disabled){border-color:#d4a359}
.panel button:disabled{opacity:.4;cursor:default}
.panel button:focus-visible,.panel select:focus-visible,.panel input:focus-visible,.panel textarea:focus-visible{outline:2px solid #e6c280;outline-offset:1px}
.panel button.x{border-color:#6d3b40;color:#ffb4ab}
.panel button.grow{flex:1;justify-content:center;text-align:center}
.row{display:flex;align-items:center;gap:6px}
.row[hidden]{display:none!important}
.lbl{flex:0 0 68px;color:#8f857a;font-size:11.5px}
.tag{flex:1;font:11.5px ui-monospace,monospace;color:#e6c280;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.panel select,.panel input[type=text],.panel textarea{flex:1;min-width:0;background:#0f0d0c;border:1px solid #3d342c;border-radius:7px;padding:4px 6px;font:11.5px ui-monospace,monospace;color:#f5ebe0}
.panel textarea{min-height:64px;resize:vertical}
.panel input[type=range]{flex:1;accent-color:#e6c280}
.seg{display:flex;flex:1;gap:2px;background:#0f0d0c;border:1px solid #3d342c;border-radius:7px;padding:2px}
.seg button{flex:1;border:0;background:transparent;padding:3px 4px;font-size:11.5px;border-radius:5px}
.seg button[aria-pressed="true"]{background:#e6c280;color:#191614;font-weight:700}
.panel button[aria-pressed="true"]{background:#e6c280;color:#191614;border-color:#e6c280}
.swatches{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px;max-height:260px;overflow:auto;padding:2px 0}
.swatch{display:flex;align-items:center;gap:8px;width:100%;text-align:left;padding:5px 8px}
.swatch[aria-pressed="true"]{border-color:#e6c280}
.chip,.dot{width:14px;height:14px;border-radius:4px;border:1px solid #3d342c;flex:0 0 auto;display:inline-block;background:#333}
.swatch-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11.5px}
.hint,.lead{color:#8f857a;font-size:11.5px;line-height:1.4}
.lead{color:#c4b8aa}
.list{overflow:auto;max-height:48vh;display:flex;flex-direction:column;gap:4px}
.item{display:flex;gap:4px;align-items:stretch}
.item .open{flex:1;display:flex;align-items:center;gap:8px;min-width:0;text-align:left}
.item .sel{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:11.5px ui-monospace,monospace;color:#e6c280}
.item .meta{color:#8f857a;font-size:10.5px;white-space:nowrap}
.item .del{flex:0 0 auto}
.bar{flex-wrap:wrap}
.seg{flex-wrap:wrap}
`;

  const uiStyle = document.createElement("style");
  uiStyle.textContent = UI_CSS;
  root.append(uiStyle);

  const maskEl = document.createElement("div");
  maskEl.className = "mask";
  root.append(maskEl);

  /* Sentence-level chrome. Hover previews, click writes. */
  const BAR_HTML = `
<div class="head" id="bhead">
  <span class="grip" aria-hidden="true">⠿</span>
  <span class="title">Dusky picker</span>
</div>
<span class="info" id="binfo">Click any element to theme it</span>
<span class="state" id="bstate" role="status"></span>
<button type="button" id="bundo" title="Undo the last rule change (Ctrl+Z)">Undo</button>
<button type="button" id="bredo" title="Redo (Ctrl+Shift+Z / Ctrl+Y)">Redo</button>
<button type="button" id="bup" title="Theme the parent container instead (Arrow Up)">Parent</button>
<button type="button" id="bdn" title="Theme a nested child instead (Arrow Down)">Child</button>
<button type="button" id="block" title="Keep this element selected while you move the mouse">Lock</button>
<button type="button" id="brules" title="List, edit or delete every picked rule for this site">Rules (0)</button>
<button type="button" id="bsync" title="Re-read disk and keep any local picks the file does not have">⟲ Resync</button>
<button type="button" id="bstop" class="x" title="Close the picker (Esc)">Stop</button>`;

  const PICK_HTML = `
<div class="head" id="phead">
  <span class="grip" aria-hidden="true">⠿</span>
  <span class="title" id="ptitle">Theme this element</span>
  <button type="button" id="pclose" class="x" title="Close this panel, keep picking">✕</button>
</div>
<p class="lead" id="pinfo">Choose what the rule matches, which part to paint, then a palette colour. Hover previews; click saves.</p>
<div class="row" id="pstack-row">
  <span class="lbl">Target</span>
  <div class="seg" id="pstack"></div>
</div>
<div class="row" id="pdepth-row">
  <span class="lbl">Depth</span>
  <input type="range" id="pslider" min="0" max="0" value="0" aria-label="DOM depth">
  <span class="tag" id="pdepth" style="flex:0 0 40px;text-align:right"></span>
</div>
<div class="row" id="psel-row">
  <span class="lbl">Matches</span>
  <select id="psel" title="CSS selector written into the template. Prefer rows that say 'this element only'."></select>
</div>
<div class="row">
  <span class="lbl">Mode</span>
  <div class="seg">
    <button type="button" id="pmode-sel" aria-pressed="true" title="Paint this element's background, text, border or fill">Element</button>
    <button type="button" id="pvarbtn" aria-pressed="false" title="Point one of this element's CSS variables at a Matugen token">CSS variable</button>
  </div>
</div>
<div class="row" id="pvar-row" hidden>
  <span class="lbl">Variable</span>
  <select id="pvar" title="Custom properties found on this element"></select>
</div>
<div class="row" id="pvarscope-row" hidden>
  <span class="lbl">Scope</span>
  <select id="pvarscope" title="Override this token for the page theme, its owner, or the selected elements"></select>
</div>
<div class="row" id="pseg-row">
  <span class="lbl">Part</span>
  <div class="seg" id="pseg">
    <button type="button" data-group="bg" aria-pressed="true" title="background-color plus a readable text colour">Background</button>
    <button type="button" data-group="text" title="color">Text</button>
    <button type="button" data-group="border" title="border-color">Border</button>
    <button type="button" data-group="fill" title="SVG fill">Fill</button>
  </div>
</div>
<div class="row">
<button type="button" id="pextra" class="grow"></button>
<button type="button" id="phide" class="grow" title="Same as Shift+click — writes display:none for the selector">Hide this element</button>
</div>
<p class="hint" id="phint"></p>
<div id="pgrid"></div>
<div class="row" id="pcustom-row">
  <span class="lbl">Custom</span>
  <input id="pcustom" type="text" placeholder="or type CSS, then Enter — e.g. opacity: 0.8" title="Any declaration list. Saved as a picked rule.">
</div>`;

  const EDIT_HTML = `
<div class="head" id="ehead">
  <span class="grip" aria-hidden="true">⠿</span>
  <span class="title">Edit saved rule</span>
  <button type="button" id="edel" class="x" title="Remove this rule from the template">Delete</button>
  <button type="button" id="eclose" title="Close">✕</button>
</div>
<div class="row" id="esel-row">
  <span class="lbl" id="esel-lbl">Selector</span>
  <input id="esel" type="text" title="What this rule matches. Press Enter to save.">
</div>
<div class="row" id="evar-row" hidden>
  <span class="lbl">Variable</span>
  <span class="tag" id="evar"></span>
</div>
<div class="row" id="eprop-row">
  <span class="lbl">Property</span>
  <div class="seg" id="eseg">
    <button type="button" data-group="bg">Background</button>
    <button type="button" data-group="text">Text</button>
    <button type="button" data-group="border">Border</button>
    <button type="button" data-group="fill">Fill</button>
  </div>
</div>
<div class="row" id="eraw-row" hidden>
  <span class="lbl">CSS</span>
  <textarea id="eraw" title="Hand-written rule. Tab or Enter saves."></textarea>
</div>
<div class="row" id="ecustom-row">
  <span class="lbl">Decl</span>
  <input id="ecustom" type="text" title="Declaration list. Enter saves.">
</div>
<p class="hint" id="ehint"></p>
<div id="egrid"></div>`;

  const DRAWER_HTML = `
<div class="head" id="rhead">
  <span class="grip" aria-hidden="true">⠿</span>
  <span class="title">Rules for this site</span>
  <button type="button" id="rclear" title="Remove every picked rule (Auto-map tokens are untouched)">Clear picks</button>
  <button type="button" id="rclose" class="x">✕</button>
</div>
<p class="hint">Click a row to edit it. Hover previews. These lines live in the picks region of the template.</p>
<div class="list" id="rlist"></div>`;

  let bar = null, panel = null, drawer = null, panelKind = "";
  let pickGrid = null, editGrid = null, flashTimer = 0;

  const q = (id) => root.getElementById(id);

  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs ?? {})) {
      if (k === "text") n.textContent = v;
      else if (k === "class") n.className = v;
      else if (k === "style" && v && typeof v === "object") Object.assign(n.style, v);
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (v === false || v == null) { /* skip */ }
      else if (v === true) n.setAttribute(k, "");
      else n.setAttribute(k, String(v));
    }
    for (const c of kids) if (c) n.append(c);
    return n;
  }

  function isOurs(e) {
    const path = e.composedPath?.() ?? [];
    return path.includes(hostEl);
  }

  function setState(text, kind = "") {
    const n = q("bstate");
    if (!n) return;
    n.textContent = text;
    n.className = `state ${kind}`;
  }

  function baseState() {
    const t = target();
    const label = t
      ? `${t.localName}${t.id ? "#" + t.id : ""}`
      : "click an element";
    setState(S.note || (S.hydrated ? label : "host unreachable"), S.note ? "err" : "");
  }

  function flash(text, kind = "ok") {
    setState(text, kind);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flashTimer = 0; baseState(); }, 2400);
  }

  function drag(node, handle) {
    if (!handle || !node) return;
    let ox = 0, oy = 0, dragging = false;
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button,input,select,textarea,a,[contenteditable]")) return;
      dragging = true;
      const r = node.getBoundingClientRect();
      ox = e.clientX - r.left;
      oy = e.clientY - r.top;
      handle.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      const x = Math.min(window.innerWidth - 40, Math.max(0, e.clientX - ox));
      const y = Math.min(window.innerHeight - 40, Math.max(0, e.clientY - oy));
      node.style.left = `${x}px`;
      node.style.top = `${y}px`;
      node.style.right = "auto";
      node.style.bottom = "auto";
      node.style.transform = "none";
    });
    handle.addEventListener("pointerup", () => { dragging = false; });
  }

  function drawMask() {
    const t = target();
    if (!t || !S.active) { maskEl.style.display = "none"; return; }
    const r = t.getBoundingClientRect();
    Object.assign(maskEl.style, {
      display: "block",
      top: `${r.top}px`, left: `${r.left}px`,
      width: `${r.width}px`, height: `${r.height}px`,
    });
  }

  function scheduleMask() {
    if (S.raf) return;
    S.raf = requestAnimationFrame(() => { S.raf = 0; drawMask(); });
  }

  const noisyClass = (c) =>
    !c || c.length > 28 || /\d{5,}/.test(c) ||
    /^(is-|has-|js-|css-|active|open|show|selected|focus|hover|scrolled|sticky)$/i.test(c);

  function nthPath(elm) {
    const parts = [];
    for (let n = elm; n && n.nodeType === 1 && parts.length < 5; n = n.parentElement) {
      if (n.id && /^[A-Za-z][\w-]*$/.test(n.id)) {
        parts.unshift(`#${CSS.escape(n.id)}`);
        break;
      }
      const tag = n.localName;
      const parent = n.parentElement;
      if (!parent) { parts.unshift(tag); break; }
      const sibs = [...parent.children].filter((c) => c.localName === tag);
      const idx = sibs.indexOf(n) + 1;
      parts.unshift(sibs.length > 1 ? `${tag}:nth-of-type(${idx})` : tag);
    }
    return parts.join(" > ");
  }

  function candidates(elm) {
    const out = [];
    const seen = new Set();
    if (!elm || elm.nodeType !== 1) return out;
    const add = (sel) => {
      if (!sel || seen.has(sel) || sel.length > LIMITS.SEL || !validSelector(sel)) return;
      let n = 0;
      try { n = document.querySelectorAll(sel).length; } catch { return; }
      if (!n || !elm.matches(sel)) return;
      seen.add(sel);
      out.push({ sel, n });
    };
    const tag = elm.localName;
    if (elm.id) add(`#${CSS.escape(elm.id)}`);
    const classes = [...elm.classList].filter((c) => !noisyClass(c)).slice(0, 4);
    if (classes.length) add(`${tag}.${classes.map((c) => CSS.escape(c)).join(".")}`);
    for (const c of classes.slice(0, 2)) add(`.${CSS.escape(c)}`);
    const role = elm.getAttribute("role");
    if (role) add(`${tag}[role=${cssString(role)}]`);
    for (const attr of ["data-testid", "data-test"]) {
      const value = elm.getAttribute(attr);
      if (value) add(`[${attr}=${cssString(value)}]`);
    }
    const name = elm.getAttribute("name");
    if (name) add(`${tag}[name=${cssString(name)}]`);
    const aria = elm.getAttribute("aria-label");
    if (aria && aria.length < 80) add(`${tag}[aria-label=${cssString(aria)}]`);
    const type = elm.getAttribute("type");
    if (type) add(`${tag}[type=${cssString(type)}]`);
    add(tag);
    add(nthPath(elm));
    out.sort((a, b) => a.n - b.n || a.sel.length - b.sel.length);
    return out;
  }

  function selected() {
    const box = q("psel");
    if (box?.value) return box.value.trim();
    return candidates(target()).at(0)?.sel ?? "";
  }

  function describe(node) {
    if (!node) return "Click anything on the page.";
    const id = node.id ? `#${node.id}` : "";
    const cls = [...node.classList].slice(0, 3).map((c) => `.${c}`).join("");
    return `${node.localName}${id}${cls}`;
  }

  function previewOf(token) {
    if (panelKind === "edit") {
      const r = currentEdit();
      if (!r) return { highlight: "", css: "" };
      const sel = q("esel")?.value?.trim() || r.sel;
      return previewForKey(r.key, sel, r.decl, token);
    }
    if (panelKind !== "pick") {
      const tsel = selected();
      return { highlight: tsel, css: "" };
    }
    if (S.targetMode === "variable") {
      const name = q("pvar")?.value;
      return token && name
        ? { highlight: "", css: `${variableScope()}{${CSS.escape(name)}: ${variablePalette(token)} !important}` }
        : { highlight: selected(), css: "" };
    }
    const sel = selected();
    if (!sel) return null;
    if (!token) return { highlight: sel, css: "" };
    return { highlight: sel, css: `${sel}{${declFor(colourGroup(S.group), token)}}` };
  }

  class PaletteGrid {
    constructor(mount, { onHover, onLeave, onPick }) {
      this.mount = mount;
      this.onHover = onHover;
      this.onLeave = onLeave;
      this.onPick = onPick;
      this.active = "";
      this.buttons = [];
      this.build();
    }
    build() {
      this.mount.textContent = "";
      const wrap = el("div", { class: "swatches" });
      for (const [token, label] of TOKENS) {
        const b = el("button", {
          class: "swatch", type: "button",
          title: `${label}  —  var(--${token})`,
          "data-token": token, "aria-label": label,
        });
        b.append(
          el("i", { class: "chip", style: { background: `var(--${token}, #333)` } }),
          el("span", { class: "swatch-name", text: label }),
        );
        b.addEventListener("pointerenter", () => this.onHover?.(token));
        b.addEventListener("pointerleave", () => this.onLeave?.());
        b.addEventListener("focus", () => this.onHover?.(token));
        b.addEventListener("blur", () => this.onLeave?.());
        b.addEventListener("click", () => this.onPick?.(token));
        wrap.append(b);
      }
      this.mount.append(wrap);
      this.buttons = [...wrap.children];
    }
    setActive(token) {
      this.active = token || "";
      for (const b of this.buttons) {
        b.setAttribute("aria-pressed", String(b.dataset.token === this.active));
      }
    }
  }

  function refreshBar() {
    if (!bar) return;
    const t = target();
    q("binfo").textContent = t ? describe(t) : "Click any element to theme it";
    q("bundo").disabled = !S.undo.length;
    q("bredo").disabled = !S.redo.length;
    q("bup").disabled = !S.stack.length || S.depth >= S.stack.length - 1;
    q("bdn").disabled = !S.stack.length || S.depth <= 0;
    const lock = q("block");
    lock.setAttribute("aria-pressed", String(!!S.locked));
    lock.textContent = S.locked ? "Locked" : "Lock";
    q("brules").textContent = `Rules (${S.rules.length})`;
    if (!flashTimer) baseState();
  }

  function buildBar() {
    bar = el("div", { class: "panel bar", id: "bar", role: "toolbar", "aria-label": "Dusky picker toolbar" });
    bar.innerHTML = BAR_HTML;
    root.append(bar);
    drag(bar, q("bhead"));
    q("bundo").addEventListener("click", undo);
    q("bredo").addEventListener("click", redo);
    q("bup").addEventListener("click", () => step(1));
    q("bdn").addEventListener("click", () => step(-1));
    q("block").addEventListener("click", () => { S.locked = !S.locked; refreshBar(); });
    q("brules").addEventListener("click", toggleDrawer);
    q("bsync").addEventListener("click", () => { void recover(true); });
    q("bstop").addEventListener("click", () => { void setActive(false); });
    refreshBar();
  }

  function currentEdit() {
    if (!S.editKey) return null;
    return S.rules.find((r) => r.key === S.editKey) ?? null;
  }

  function closePanel() {
    panel?.remove();
    panel = null;
    panelKind = "";
    pickGrid = editGrid = null;
    S.editKey = null;
    S.locked = false;
    Preview.clear();
    refreshBar();
  }

  function refreshPanel() {
    if (panelKind === "pick") refreshPick(false);
    else if (panelKind === "edit") refreshEdit();
  }

  function positionDialog() {
    if (panel && bar) panel.style.setProperty("--dusky-dialog-top", `${Math.max(64, bar.getBoundingClientRect().bottom + 8)}px`);
  }

  function refreshPick(rebuildSels = false) {
    if (panelKind !== "pick" || !panel) return;
    const t = target();
    q("ptitle").textContent = t ? `Theme <${t.localName}>` : "Theme this element";
    q("pinfo").textContent = t
      ? `${describe(t)} — choose a colour variable or theme this element directly.`
      : "Click anything on the page.";

    const stackBox = q("pstack");
    stackBox.textContent = "";
    S.stack.slice(0, 6).forEach((node, i) => {
      const label = i === 0 ? `this <${node.localName}>` : `<${node.localName}>`;
      const b = el("button", { type: "button", text: label });
      b.setAttribute("aria-pressed", String(i === S.depth));
      b.title = i === 0 ? "The element you clicked" : `Ancestor ${i} — theme this container instead`;
      b.addEventListener("click", () => { S.depth = i; retarget(); });
      stackBox.append(b);
    });
    const sl = q("pslider");
    if (sl) {
      sl.max = String(Math.max(0, S.stack.length - 1));
      sl.value = String(S.depth);
      sl.disabled = S.stack.length < 2;
    }
    const dt = q("pdepth");
    if (dt) dt.textContent = S.stack.length > 1 ? `${S.depth}/${S.stack.length - 1}` : "";

    if (rebuildSels) {
      const box = q("psel");
      const prev = box.value;
      box.textContent = "";
      for (const c of candidates(t)) {
        const opt = document.createElement("option");
        opt.value = c.sel;
        opt.textContent = c.n === 1 ? `${c.sel}  (this element only)` : `${c.sel}  (matches ${c.n})`;
        box.append(opt);
      }
      if ([...box.options].some((o) => o.value === prev)) box.value = prev;
    }

    const varMode = S.targetMode === "variable";
    q("pmode-sel").setAttribute("aria-pressed", String(!varMode));
    q("pvarbtn").setAttribute("aria-pressed", String(varMode));
    q("pvarbtn").disabled = S.elementVars.length === 0;
    q("pvar-row").hidden = !varMode;
    q("pvarscope-row").hidden = !varMode;
    q("pseg-row").hidden = varMode;
    q("psel-row").hidden = varMode;

    if (varMode) {
      const box = q("pvar");
      const prev = box.value;
      box.textContent = "";
      for (const v of S.elementVars) {
        const opt = document.createElement("option");
        opt.value = v.name;
        opt.textContent = `${v.used ? "Referenced: " : ""}${v.name}  =  ${v.value}`;
        box.append(opt);
      }
      if ([...box.options].some((o) => o.value === prev)) box.value = prev;
      refreshVarScope();
    }

    for (const b of q("pseg").children) {
      b.setAttribute("aria-pressed", String(b.dataset.group === S.group));
    }
    const extra = GROUPS[S.group]?.extra;
    q("pextra").textContent = extra?.label ?? "";
    q("pextra").hidden = varMode || !extra;
    q("phint").textContent = varMode
      ? "Choose page theme or local scope. Hover previews; click saves the variable mapping."
      : "Hover previews · click saves · Shift+click hides.";
    pickGrid?.setActive("");
    positionDialog();
  }

  function applyPick(token) {
    if (S.targetMode === "variable") {
      const name = q("pvar")?.value;
      if (!name) { flash("pick a CSS variable first", "err"); return; }
      const scope = variableScope();
      upsert({
        sel: scope,
        decl: `${CSS.escape(name)}: ${variablePalette(token)} !important;`,
        meta: `var ${name} → ${token}`,
        key: varKey(scope, name),
      });
      flash(`✓ ${name} → ${token}`);
      return;
    }
    const sel = selected();
    if (!validSelector(sel)) { flash("no usable selector here", "err"); return; }
    const group = colourGroup(S.group);
    upsert({
      sel,
      decl: declFor(group, token),
      meta: `${group}: ${token}`,
      key: selKey(sel, group),
    });
    flash(`✓ ${group}: ${token} on ${sel}`);
  }

  function applyExtra() {
    const extra = GROUPS[S.group]?.extra;
    const sel = selected();
    if (!extra || !validSelector(sel)) return;
    upsert({
      sel,
      decl: important(extra.css),
      meta: extra.meta,
      key: selKey(sel, S.group),
    });
    flash(`✓ ${extra.meta}`);
  }

  /* Light depth-chrome sync for slider drags: no querySelectorAll storms, so the
   * thumb never sticks on heavy pages. Full rebuild happens on release. */
  function syncDepthChrome() {
    drawMask();
    refreshBar();
    if (panelKind !== "pick" || !panel) return;
    const t = target();
    q("ptitle").textContent = t ? `Theme <${t.localName}>` : "Theme this element";
    q("pinfo").textContent = t
      ? `${describe(t)} — pick a unique selector, a part, then a colour.`
      : "Click anything on the page.";
    for (const [i, b] of [...q("pstack").children].entries()) {
      b.setAttribute("aria-pressed", String(i === S.depth));
    }
    const dt = q("pdepth");
    if (dt) dt.textContent = S.stack.length > 1 ? `${S.depth}/${S.stack.length - 1}` : "";
    Preview.show(previewOf(null));
  }

  function bindPick() {
    q("pclose").addEventListener("click", closePanel);
    q("pslider").addEventListener("input", (e) => {
      S.depth = Number(e.target.value);
      e.target.value = String(S.depth);
      syncDepthChrome();
    });
    q("pslider").addEventListener("change", () => { retarget(); });
    q("psel").addEventListener("change", () => Preview.show(previewOf(null)));
    q("pvar").addEventListener("change", () => { refreshVarScope(); Preview.show(previewOf(null)); });
    q("pvarscope").addEventListener("change", () => Preview.show(previewOf(null)));
    q("pmode-sel").addEventListener("click", () => {
      S.targetMode = "selector";
      refreshPick(false);
      Preview.show(previewOf(null));
    });
    q("pvarbtn").addEventListener("click", () => {
      if (!S.elementVars.length) return;
      S.targetMode = "variable";
      refreshPick(false);
      Preview.show(previewOf(null));
    });
    q("pseg").addEventListener("click", (e) => {
      const b = e.target.closest("[data-group]");
      if (!b) return;
      S.group = b.dataset.group;
      refreshPick(false);
      Preview.show(previewOf(null));
    });
    q("pextra").addEventListener("click", applyExtra);
    const hoverExtra = (css) => {
      if (S.targetMode !== "selector") return;
      const sel = selected();
      Preview.show({ highlight: sel, css: validSelector(sel) ? `${sel}{${css}}` : "" });
    };
    q("pextra").addEventListener("pointerenter", () => {
      const extra = GROUPS[S.group]?.extra;
      if (extra) hoverExtra(important(extra.css));
    });
    q("pextra").addEventListener("focus", () => {
      const extra = GROUPS[S.group]?.extra;
      if (extra) hoverExtra(important(extra.css));
    });
    q("pextra").addEventListener("pointerleave", () => Preview.show(previewOf(null)));
    q("pextra").addEventListener("blur", () => Preview.show(previewOf(null)));
    q("phide").addEventListener("pointerenter", () => hoverExtra("display:none !important;"));
    q("phide").addEventListener("focus", () => hoverExtra("display:none !important;"));
    q("phide").addEventListener("pointerleave", () => Preview.show(previewOf(null)));
    q("phide").addEventListener("blur", () => Preview.show(previewOf(null)));
    q("phide").addEventListener("click", () => {
      const sel = selected();
      if (!validSelector(sel)) { flash("no usable selector here", "err"); return; }
      if (/^(html|body|:root)$/i.test(sel.trim())) { flash("refusing to hide the whole page", "err"); return; }
      upsert({ sel, decl: "display: none !important;", meta: "hidden", key: selKey(sel, "display") });
      flash(`✓ hidden ${sel}`);
    });
    q("pcustom").addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const sel = selected();
      const decl = normaliseDecl(q("pcustom").value);
      if (!validSelector(sel) || !decl) { flash("need a selector and some CSS", "err"); return; }
      const group = groupOfDecl(decl);
      const name = propsOf(decl)[0];
      upsert({
        sel, decl, meta: "custom",
        key: group === "var" ? varKey(sel, name) : selKey(sel, group),
      });
      q("pcustom").value = "";
      flash("✓ custom rule saved");
    });
    pickGrid = new PaletteGrid(q("pgrid"), {
      onHover: (tok) => Preview.show(previewOf(tok)),
      onLeave: () => Preview.show(previewOf(null)),
      onPick: applyPick,
    });
  }

  function openPick() {
    S.locked = true;
    dropIndex();
    S.elementVars = getElementVars(target());
    resetVarSelection();
    S.targetMode = S.elementVars.length ? "variable" : "selector";
    if (panelKind !== "pick" || !panel) {
      panel?.remove();
      editGrid = null;
      panelKind = "pick";
      panel = el("section", { class: "panel dlg ghost", role: "dialog", "aria-label": "Theme this element" });
      panel.innerHTML = PICK_HTML;
      root.append(panel);
      drag(panel, q("phead"));
      bindPick();
    }
    refreshPick(true);
    Preview.show(previewOf(null));
    refreshBar();
  }

  function bindEdit() {
    q("eclose").addEventListener("click", closePanel);
    q("edel").addEventListener("click", () => {
      if (S.editKey) removeRule(S.editKey);
      closePanel();
    });
    q("esel").addEventListener("change", commitSelector);
    q("esel").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commitSelector(); }
    });
    q("ecustom").addEventListener("change", commitDecl);
    q("ecustom").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); commitDecl(); }
    });
    q("eraw").addEventListener("change", commitRaw);
    q("eraw").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commitRaw(); }
    });
    q("eseg").addEventListener("click", (e) => {
      const b = e.target.closest("[data-group]");
      if (!b) return;
      const r = currentEdit();
      if (!r || r.raw !== undefined) return;
      const k = decodeKey(r.key);
      if (k.kind === "var") return;
      const group = b.dataset.group;
      const tok = tokenOf(r.decl);
      writeRule(r.key, {
        sel: r.sel,
        decl: tok ? declFor(group, tok) : r.decl,
        meta: `${group}: ${tok || "custom"}`,
        key: selKey(r.sel, group),
      });
    });
    editGrid = new PaletteGrid(q("egrid"), {
      onHover: (tok) => Preview.show(previewOf(tok)),
      onLeave: () => Preview.show(previewOf(null)),
      onPick: applyEdit,
    });
  }

  function openEdit(r) {
    if (!r) return;
    S.editKey = r.key;
    S.locked = true;
    if (panelKind !== "edit" || !panel) {
      panel?.remove();
      pickGrid = null;
      panelKind = "edit";
      panel = el("section", { class: "panel dlg ghost", role: "dialog", "aria-label": "Edit a saved rule" });
      panel.innerHTML = EDIT_HTML;
      root.append(panel);
      drag(panel, q("ehead"));
      bindEdit();
    }
    refreshEdit();
    Preview.show(previewOf(null));
    refreshBar();
  }

  function applyEdit(token) {
    const r = currentEdit();
    if (!r) return;
    const k = decodeKey(r.key);
    if (k.kind === "var") {
      writeRule(r.key, {
        sel: r.sel,
        decl: remapVariable(k.name, r.decl, token),
        meta: `var ${k.name} → ${token}`,
        key: varKey(r.sel, k.name),
      });
      flash(`✓ ${k.name} → ${token}`);
      return;
    }
    if (r.raw !== undefined || k.kind === "raw") return;
    if (k.group === "display") return;
    if (k.kind === "sel" && typeof k.group === "string" && k.group.startsWith("custom:")) {
      const swapped = remapPalette(r.decl, token);
      if (swapped === r.decl) return;
      writeRule(r.key, {
        sel: r.sel,
        decl: important(swapped),
        meta: `${k.group}: ${token}`,
        key: r.key,
      });
      flash(`✓ ${token}`);
      return;
    }
    const group = colourGroup(k.group);
    writeRule(r.key, {
      sel: r.sel, decl: declFor(group, token),
      meta: `${group}: ${token}`, key: selKey(r.sel, group),
    });
    flash(`✓ ${group}: ${token}`);
  }

  function commitSelector() {
    const r = currentEdit();
    if (!r) return;
    const sel = q("esel").value.trim();
    const k = decodeKey(r.key);
    if (!validSelector(sel)) { flash("invalid selector", "err"); q("esel").value = r.sel ?? ""; return; }
    if (sel === r.sel) return;
    const key = k.kind === "var" ? varKey(sel, k.name) : selKey(sel, k.group ?? groupOfDecl(r.decl));
    writeRule(r.key, { ...r, sel, key });
    flash("✓ selector updated");
  }

  function commitDecl() {
    const r = currentEdit();
    if (!r || r.raw !== undefined) return;
    const decl = normaliseDecl(q("ecustom").value);
    if (!decl) { flash("that is not a valid declaration", "err"); return; }
    const group = groupOfDecl(decl);
    const name = group === "var" ? propsOf(decl)[0] : "";
    writeRule(r.key, {
      sel: r.sel, decl, meta: `${group}: ${tokenOf(decl) || "custom"}`,
      key: group === "var" ? varKey(r.sel, name) : selKey(r.sel, group),
    });
    flash("✓ declaration updated");
  }

  function commitRaw() {
    const r = currentEdit();
    if (!r) return;
    const next = parseRule(q("eraw").value);
    if (!next) { flash("nothing to save", "err"); return; }
    writeRule(r.key, next);
    flash("✓ rule updated");
  }

  /* Refresh mutates state only — it NEVER rebuilds the palette grid. */
  function refreshEdit() {
    if (panelKind !== "edit") return;
    const r = currentEdit();
    if (!r) { closePanel(); return; }
    const k = decodeKey(r.key);
    const isVar = k.kind === "var";
    const isRaw = r.raw !== undefined;

    q("esel-lbl").textContent = isVar ? "Scope" : "Selector";
    q("esel-row").hidden = isRaw;
    if (!isRaw && q("esel") !== root.activeElement) q("esel").value = r.sel;
    q("evar-row").hidden = !isVar;
    if (isVar) q("evar").textContent = k.name;
    q("eprop-row").hidden = isVar || isRaw;
    q("eraw-row").hidden = !isRaw;
    if (isRaw && q("eraw") !== root.activeElement) q("eraw").value = r.raw;
    q("ecustom-row").hidden = isRaw;
    if (!isRaw && q("ecustom") !== root.activeElement) q("ecustom").value = r.decl;
    q("egrid").hidden = isRaw || k.group === "display" ||
      (k.group?.startsWith("custom:") && !tokenOf(r.decl));

    const group = k.kind === "sel" ? colourGroup(k.group) : "";
    for (const b of q("eseg").children) b.setAttribute("aria-pressed", String(b.dataset.group === group));
    editGrid?.setActive(tokenOf(r.decl).replace(PALETTE_SUFFIX, ""));
    positionDialog();

    q("ehint").textContent = isRaw
      ? "Hand-written rule — edit the CSS and press Tab/Enter to save it."
      : isVar
        ? "Hover or Tab a swatch to preview this variable pointing at another palette colour; click commits. Scope is where the override is declared."
        : k.group?.startsWith("custom:") || k.group === "display"
          ? "Hover a swatch to preview substituting the colour inside this rule; click commits."
          : "Hover or Tab a swatch to preview · click commits · switch Property to convert the rule · the selector is editable.";
  }

  /* ── Rules drawer ──────────────────────────────────────────────────── */
  function toggleDrawer() {
    if (drawer) { drawer.remove(); drawer = null; return; }
    drawer = el("section", { class: "panel drawer", role: "dialog", "aria-label": "Rules for this site" });
    drawer.innerHTML = DRAWER_HTML;
    root.append(drawer);
    drag(drawer, q("rhead"));
    q("rclose").addEventListener("click", toggleDrawer);
    q("rclear").addEventListener("click", () => {
      if (S.rules.length) { snapshot(); S.rules = Object.freeze([]); commit(); }
    });
    refreshDrawer();
  }

  function refreshDrawer() {
    if (!drawer) return;
    const list = q("rlist");
    list.textContent = "";
    if (!S.rules.length) {
      list.append(el("p", { class: "hint", text: "No rules yet — click any element on the page." }));
      return;
    }
    for (const r of S.rules) {
      const tok = tokenOf(r.decl).replace(PALETTE_SUFFIX, "");
      const open = el("button", { class: "open", type: "button", title: ruleLine(r), onclick: () => openEdit(r) },
        el("i", { class: "dot", style: { background: tok ? `var(--${tok}, transparent)` : "transparent" } }),
        el("span", { class: "sel", text: r.raw !== undefined ? r.raw : r.sel }),
        el("span", { class: "meta", text: r.meta || "manual" }));
      /* Hover here previews the rule — and leaving restores whatever the open
       * panel was previewing, WITHOUT rebuilding any panel DOM. */
      open.addEventListener("pointerenter", () => Preview.show(previewForKey(r.key, r.sel, r.decl, "")));
      open.addEventListener("pointerleave", () => Preview.show(previewOf(null)));
      list.append(el("div", { class: "item" }, open,
        el("button", {
          class: "del x", type: "button", title: "Remove this rule", text: "✕",
          onclick: () => removeRule(r.key),
        })));
    }
  }

  /* ══ Mutation / history / persistence ═══════════════════════════════ */
  function snapshot() {
    S.undo.push(S.rules);                       /* frozen array — safe by value */
    if (S.undo.length > 100) S.undo.shift();
    S.redo = [];
  }
  const undo = () => { if (S.undo.length) { S.redo.push(S.rules); S.rules = S.undo.pop(); commit(); } };
  const redo = () => { if (S.redo.length) { S.undo.push(S.rules); S.rules = S.redo.pop(); commit(); } };

  function commit() {
    S.generation++;
    previewRules = [];
    renderLive();
    refreshBar();
    refreshDrawer();
    refreshPanel();
    schedulePersist();
  }

  function validRule(rule) {
    if (rule.raw !== undefined) return rule.raw.length <= LIMITS.DECL;
    if (!validSelector(rule.sel)) { flash("invalid selector", "err"); return false; }
    if (!rule.decl || rule.decl.length > LIMITS.DECL) { flash("declaration too long", "err"); return false; }
    if (decodeKey(rule.key).kind === "invalid") { flash("internal: bad rule key", "err"); return false; }
    return true;
  }

  function upsert(rule) {
    if (!validRule(rule)) return;
    const i = S.rules.findIndex((r) => r.key === rule.key);
    if (i < 0 && S.rules.length >= LIMITS.RULES) { flash(`rule limit (${LIMITS.RULES}) reached`, "err"); return; }
    snapshot();
    const next = S.rules.filter((r, at) => r.key !== rule.key || at === i);
    if (i >= 0) next[i] = rule; else next.push(rule);
    S.rules = Object.freeze(next);
    commit();
  }

  /* Replace oldKey in place, even when the new rule has a different key. */
  function writeRule(oldKey, rule) {
    if (!validRule(rule)) return;
    snapshot();
    const next = S.rules.slice();
    if (rule.key !== oldKey) {
      const dup = next.findIndex((r) => r.key === rule.key);
      if (dup >= 0) next.splice(dup, 1);
    }
    const at = next.findIndex((r) => r.key === oldKey);
    if (at >= 0) next[at] = rule; else next.push(rule);
    S.rules = Object.freeze(next);
    S.editKey = rule.key;
    commit();
  }

  function removeRule(key) {
    const i = S.rules.findIndex((r) => r.key === key);
    if (i < 0) return;
    snapshot();
    const next = S.rules.slice();
    next.splice(i, 1);
    S.rules = Object.freeze(next);
    commit();
  }

  const send = (msg) => browser.runtime.sendMessage({ ...msg, origin: ORIGIN })
    .catch((e) => ({ ok: false, error: String(e?.message ?? e) }));

  let diskRules = Object.freeze([]);

  function mergeForeign(foreignPicks) {
    const base = new Map(diskRules.map((r) => [r.key, ruleCss(r)]));
    const mine = new Map(S.rules.map((r) => [r.key, r]));
    const foreign = parseLines(foreignPicks);
    const rawCounts = (rules) => {
      const counts = new Map();
      for (const rule of rules) if (rule.raw !== undefined) counts.set(rule.key, (counts.get(rule.key) ?? 0) + 1);
      return counts;
    };
    const baseRaw = rawCounts(diskRules), mineRaw = rawCounts(S.rules);
    const remaining = rawCounts(foreign);
    // Raw identity is its CSS text, so equal copies share a key. A local
    // count change is an edit to that key; unchanged counts adopt disk.
    for (const key of new Set([...baseRaw.keys(), ...mineRaw.keys()])) {
      const count = mineRaw.get(key) ?? 0;
      if (count !== (baseRaw.get(key) ?? 0)) remaining.set(key, count);
    }
    const merged = [];
    const seen = new Set();
    for (const rule of foreign) {
      if (rule.raw !== undefined) {
        const count = remaining.get(rule.key) ?? 0;
        if (count) { merged.push(rule); remaining.set(rule.key, count - 1); }
        continue;
      }
      const local = mine.get(rule.key);
      if (!local && base.has(rule.key)) continue;  /* deleted locally */
      merged.push(local && base.get(rule.key) !== ruleCss(local) ? local : rule);
      seen.add(rule.key);
    }
    for (const rule of S.rules) {
      const key = rule.key;
      if (rule.raw !== undefined) {
        const count = remaining.get(key) ?? 0;
        if (count) { merged.push(rule); remaining.set(key, count - 1); }
      } else if (!seen.has(key) && base.get(key) !== ruleCss(mine.get(key))) {
        merged.push(mine.get(key)); seen.add(key);
      }
    }
    diskRules = Object.freeze(foreign);
    S.rules = Object.freeze(merged);               /* preserve repeated raw lines */
  }

  let saveInFlight = null;

  function schedulePersist() {
    if (!S.hydrated) {                          /* never write what we never read */
      setState(`⚠ NOT SAVING — ${S.note || "host unreachable"} · press ⟲`, "err");
      return;
    }
    if (saveInFlight) return;                   /* generation loop picks it up */
    saveInFlight = (async () => {
      try {
        await Promise.resolve(); // combine synchronous edits before the first native write
        let written = -1;
        while (written !== S.generation) {
          const generation = S.generation;
          await persistOnce();
          written = generation;
        }
      } catch (err) {
        S.note = String(err?.message ?? err);
        setState(`⚠ NOT SAVED: ${S.note}`, "err");
      } finally { saveInFlight = null; }
    })();
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function persistOnce() {
    setState("saving…", "");
    for (let attempt = 0; attempt < 3; attempt++) {
      const writtenRules = S.rules;
      const body = serialise();
      if (new TextEncoder().encode(body).length > LIMITS.BODY) {
        throw new Error("picks region exceeds 512 KiB — remove some rules");
      }
      const reply = await send({ type: "splice", region: "picks", body, base_rev: S.rev });
      if (reply?.ok) {
        diskRules = writtenRules;
        S.rev = reply.rev ?? 0;
        S.note = "";
        flash(`✓ saved ${String(reply.path ?? "").split("/").pop()}`);
        return;
      }
      if (!reply?.conflict) {
        throw new Error(reply?.error ?? "no reply");
      }
      /* Someone else wrote the file: adopt their rules, keep ours, retry. */
      const fresh = await send({ type: "read" });
      if (fresh?.ok) {
        S.rev = fresh.rev ?? 0;
        mergeForeign(fresh.picks);
        renderLive(); refreshBar(); refreshDrawer(); refreshPanel();
      } else throw new Error(fresh?.error ?? "cannot refresh after a save conflict");
      if (attempt < 2) await sleep(40 + Math.random() * 120 * (attempt + 1));
    }
    throw new Error("the file keeps changing on disk — press ⟲");
  }

  async function hydrate(force = false) {
    if (S.hydrated && !force) return true;
    const reply = await send({ type: "read" });
    if (!reply?.ok) {
      S.hydrated = false;
      S.note = reply?.error ?? "cannot reach the native host";
      return false;
    }
    S.hydrated = true;
    S.note = "";
    S.rev = reply.rev ?? 0;
    S.warnings = reply.warnings ?? [];
    S.rules = Object.freeze(parseLines(reply.picks));
    diskRules = S.rules;
    return true;
  }

  /* Re-read disk and re-apply anything we have locally that disk lacks. */
  async function recover(manual = false) {
    let reply;
    for (;;) {
      await saveInFlight?.catch(() => {});
      const rev = S.rev;
      reply = await send({ type: "read" });
      // A save or another resync can complete while this snapshot travels
      // back. Merge only against the disk baseline that produced the read.
      if (!saveInFlight && S.rev === rev) break;
    }
    if (!reply?.ok) {
      S.hydrated = false;
      S.note = reply?.error ?? "cannot reach the native host";
      baseState();
      return false;
    }
    S.hydrated = true;
    S.note = "";
    S.rev = reply.rev ?? 0;
    mergeForeign(reply.picks);
    const diskText = diskRules.map((r) => `    ${ruleLine(r)}`).join("\n");
    renderLive(); refreshBar(); refreshDrawer(); refreshPanel();
    if (serialise() !== diskText) { S.generation++; schedulePersist(); }
    if (manual) flash("⟲ resynced with disk");
    return true;
  }

  /* Adopt disk changes while preserving any edits still awaiting a commit. */
  let rehydratePending = false, rehydrateInFlight = null;
  function rehydrate() {
    rehydratePending = true;
    if (rehydrateInFlight) return rehydrateInFlight;
    rehydrateInFlight = (async () => {
      // Coalesce synchronous notifications and request one fresh snapshot if
      // more arrive during a read. Concurrent reads otherwise amplify retries.
      await Promise.resolve();
      while (rehydratePending) {
        rehydratePending = false;
        const before = S.rules;
        const beforeText = serialise();
        if (!await recover()) return;
        if (serialise() !== beforeText) {
          S.undo.push(before);
          if (S.undo.length > 100) S.undo.shift();
          S.redo = [];
          refreshBar();
        }
        flash("↻ reloaded from disk");
      }
    })().finally(() => { rehydrateInFlight = null; });
    return rehydrateInFlight;
  }

  /* ══ Targeting ══════════════════════════════════════════════════════ */
  function setStack(elm, refresh = true) {
    const chain = [];
    for (let n = elm; n?.nodeType === 1; n = n.parentElement) chain.push(n);
    S.stack = chain;
    S.depth = 0;
    if (refresh) retarget();
  }
  function retarget() {
    drawMask();
    refreshBar();
    if (panelKind === "pick") {
      dropIndex();
      resetVarSelection();
      S.elementVars = getElementVars(target());
      q("pvarbtn").disabled = S.elementVars.length === 0;
      if (!S.elementVars.length) S.targetMode = "selector";
      refreshPick(true);
    }
  }
  function step(delta) {
    if (!S.stack.length) return;
    S.depth = Math.min(Math.max(0, S.depth + delta), S.stack.length - 1);
    retarget();
  }
  function typing() {
    const a = root.activeElement ?? document.activeElement;
    return !!a && (a.isContentEditable || /^(input|select|textarea)$/i.test(a.tagName));
  }

  /* ══ Page events ════════════════════════════════════════════════════ */
  const onOver = (e) => { if (!S.locked && !isOurs(e)) setStack(e.target); };
  const onPointerDown = (e) => {
    if (isOurs(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
  };

  function onClick(e) {
    if (isOurs(e)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const sameTarget = panelKind === "pick" && S.stack.includes(e.target);

    if (e.shiftKey) {
      /* Honour the panel's selector + depth when it is open on this element. */
      if (!sameTarget) { S.locked = false; setStack(e.target); }
      const sel = sameTarget ? selected() : (candidates(target()).at(0)?.sel ?? "");
      if (!validSelector(sel)) { flash("no usable selector here", "err"); return; }
      if (/^(html|body)$/i.test(sel.trim())) { flash("refusing to hide the whole page", "err"); return; }
      upsert({ sel, decl: "display: none !important;", meta: "hidden", key: selKey(sel, "display") });
      flash(`✓ hidden ${sel}`);
      return;
    }
    if (panelKind === "edit") closePanel();
    S.locked = false;
    setStack(e.target, false);
    openPick();
  }

  function onKey(e) {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey;
    if (k === "Escape") {
      /* First Esc leaves the field (keeping what you typed), second closes. */
      if (typing()) (root.activeElement ?? document.activeElement)?.blur?.();
      else if (panel) closePanel();
      else if (drawer) toggleDrawer();
      else void setActive(false);
    } else if (typing()) return;
    else if (k === "ArrowUp" || k === "ArrowDown") {
      if (!S.stack.length) return;
      step(k === "ArrowUp" ? 1 : -1);
    } else if (ctrl && !e.altKey && k.toLowerCase() === "z") { e.shiftKey ? redo() : undo(); }
    else if (ctrl && !e.altKey && k.toLowerCase() === "y") redo();
    else return;
    e.preventDefault();
    e.stopImmediatePropagation();
  }

  const LISTENERS = [["mouseover", onOver], ["pointerdown", onPointerDown], ["click", onClick], ["keydown", onKey]];
  let listenerCtl = null;

  let activation = Promise.resolve();
  function setActive(on) {
    const job = activation.then(() => changeActive(on ?? !S.active));
    activation = job.catch(() => {});
    return job;
  }

  async function changeActive(on) {
    if (on === S.active) return;
    S.active = on;
    if (on) {
      dropIndex();
      await saveInFlight;
      if (S.note) await recover();
      else await hydrate(true);
      S.undo = []; S.redo = [];
      document.documentElement.append(hostEl);
      buildBar();
      renderLive();
      listenerCtl = new AbortController();
      const { signal } = listenerCtl;
      const opts = { capture: true, signal };
      for (const [type, fn] of LISTENERS) window.addEventListener(type, fn, opts);
      window.addEventListener("scroll", scheduleMask, { capture: true, passive: true, signal });
      window.addEventListener("resize", () => { positionDialog(); scheduleMask(); }, { passive: true, signal });
      /* SPA route changes leave the stack pointing at detached nodes. */
      globalThis.navigation?.addEventListener("navigate", () => {
        S.stack = []; S.depth = 0; dropIndex(); closePanel();
      }, { signal });
    } else {
      closePanel();
      if (drawer) toggleDrawer();
      bar?.remove();
      bar = null;
      listenerCtl?.abort();
      listenerCtl = null;
      S.stack = [];
      S.depth = 0;
      S.locked = false;
      dropIndex();
      hostEl.remove();
      dropProbe();
      await saveInFlight?.catch(() => {});
      detachSheet();
    }
  }

  /* ══ Extension messages ═════════════════════════════════════════════ */
  browser.runtime.onMessage.addListener((msg) => {
    switch (msg?.type) {
      case "ping":
        return Promise.resolve({ ok: true, active: S.active, rules: S.rules.length, hydrated: S.hydrated });
      case "scan":
        try { return Promise.resolve(scan()); }
        catch (e) { return Promise.resolve({ ok: false, error: `Scan failed: ${e?.message ?? e}` }); }
      case "picker":
        return setActive(msg.enable).then(() => ({ ok: true, active: S.active }));
      case "reset":
        S.rules = Object.freeze([]); S.undo = []; S.redo = [];
        S.hydrated = false; S.note = ""; S.rev = 0; diskRules = S.rules;
        renderLive(); refreshBar(); refreshDrawer(); refreshPanel(); baseState();
        return Promise.resolve({ ok: true });
      case "rehydrate":
        if (!S.active) return Promise.resolve({ ok: true, active: false });
        return rehydrate().then(() => ({ ok: true, rules: S.rules.length }));
      default:
        return false;
    }
  });
})();
