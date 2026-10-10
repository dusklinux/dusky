/* Shared Matugen roles for the popup and the isolated in-page picker. */
"use strict";
(() => {
  const roles = Object.freeze({
    canvas: ["background", "#101418"],
    panel: ["surface_container", "#1a2027"],
    field: ["surface_container_lowest", "#0b1015"],
    control: ["surface_container_high", "#28313c"],
    hover: ["surface_container_highest", "#344050"],
    text: ["on_surface", "#edf1f7"],
    muted: ["on_surface_variant", "#bcc6d4"],
    primary: ["primary", "#a8ceff"],
    "on-primary": ["on_primary", "#003258"],
    border: ["outline_variant", "#526070"],
    error: ["error", "#ffb4ab"],
    success: ["secondary", "#b8c8df"],
    warning: ["tertiary", "#d4bfe8"],
  });
  const css = `:root,:host{${Object.entries(roles).map(([name, [, value]]) =>
    `--dusky-ui-${name}:${value};`).join("")}color-scheme:dark;}
    :root,:host,*{scrollbar-color:var(--dusky-ui-border) var(--dusky-ui-field);}`;
  function apply(target, colors = {}) {
    for (const [name, [token, fallback]] of Object.entries(roles)) {
      const value = colors[`--${token}`];
      target.style.setProperty(`--dusky-ui-${name}`, /^#[0-9a-f]{6}$/i.test(value ?? "") ? value : fallback, "important");
    }
    const hex = target.style.getPropertyValue("--dusky-ui-canvas");
    const channels = [1, 3, 5].map((i) => {
      const value = parseInt(hex.slice(i, i + 2), 16) / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    const luminance = .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2];
    const scheme = luminance > .179 ? "light" : "dark";
    target.style.setProperty("--dusky-ui-scheme", scheme, "important");
    target.style.setProperty("color-scheme", scheme, "important");
  }
  globalThis.__duskyTemplateTheme = Object.freeze({ css, apply });
})();
