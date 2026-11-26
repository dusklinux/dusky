# Dusky Sites: Firefox theme maintenance

Target: native Linux Firefox **161+**, Python **3.15+**, and Wayland.
The setup handles the traditional `~/.mozilla/firefox` and current
`~/.config/mozilla/firefox` profile registries, including external profiles.
Forks and Flatpak installations are outside this verified installation path.

## Color flow

1. Matugen renders `~/.config/matugen/templates/dusky_sites.css` into
   `~/.config/matugen/generated/dusky_sites.css`. Its CSS variables use the
   `--dusky-palette-` prefix so other userChrome palettes (including an older
   `colors.css`) cannot override them. The host converts these private names to
   the existing palette keys sent to the signed extension.
2. The native host reads color declarations and sends palette changes to the
   extension. Its source is
   `~/.config/firefox_extentions/dusky_sites/dusky_sites_host.py`; setup copies
   it to `$XDG_DATA_HOME/dusky-sites/dusky_sites_host.py` (default:
   `~/.local/share/dusky-sites/dusky_sites_host.py`).
3. `extension/background.js` maps seven palette roles to Firefox's theme API.
   Firefox owns toolbar, tab, sidebar, new-tab and URL-bar selection colors.
4. Setup imports `dusky_menu.css` from each profile's `userChrome.css` to theme
   native menu defaults, current browser design tokens, and the address-bar
   search button's shadow-host tokens. It preserves popup parts, checkbox/radio indicators, disabled states, status icons, tab-group
   colors, URL-bar row layout, and direction-aware autoscroll icons.
5. `~/.config/dusky_sites/about.css` supplies current design tokens for internal
   documents. Setup copies it to `chrome/dusky_about.css`, imports that from
   both `userChrome.css` and `userContent.css`, and links `dusky_palette.css`
   to the configured palette.
   This includes the print settings panel, common confirmation dialogs, and the
   Library (history/bookmarks/downloads), and Developer Tools. DevTools panels,
   toolbars, inputs, selections, borders and links follow the palette; native
   syntax highlighting, HTTP status, errors and warnings retain their meaning.
   Matugen also emits its light/dark mode so those native diagnostic colors stay
   readable against either palette, including in a detached DevTools window.
   Exact chrome document URLs keep the
   print preview document separate. Both the palette and internal-page rules
   exclude ordinary webpages and `about:blank` / `about:srcdoc` frames.

Chrome palette updates are live. Profile stylesheets, including the internal
page palette, are loaded at browser startup; restart Firefox after regenerating
colors to refresh internal pages. Re-run setup after changing the setup CSS,
internal-page template, or `colorsPath`; wallpaper-only changes need no setup
re-run. The `toolkit.legacyUserProfileCustomizations.stylesheets` preference
retains that exact name in Firefox 161 and is still required.

## Webpage opt-in

The primary settings file is
`~/.config/dusky/settings/dusky_sites/config.json`. New setup, the native host,
and extension source all default `webThemeEnabled` to **false**.
Existing configuration is preserved, including an explicit opt-in.

Setting `webThemeEnabled` to `true` enables matching domain templates in
`~/.config/dusky_sites`. `forceUnthemedWebsites` separately enables fallback
rules for other websites and defaults to false. Turning webpage theming off
rolls back the extension's injected palette and rules. `defaults.js` can
explicitly override host-owned settings; it is optional.

`contentColorScheme` controls the extension's separate theme API
`content_color_scheme` setting. Its default is `auto` (follows the browser
palette), which can affect websites' own `prefers-color-scheme` behavior even
with CSS injection off. Set it to `system` in `defaults.js` to follow the
system, or explicitly to `light` or `dark`. This setting does not enable
webpage CSS injection.

## Installation and signed packages

```sh
python3 ~/user_scripts/firefox/theme_matugen/dusky_sites_setup.py
```

Setup enables profile stylesheets, installs the host and manifests, and copies
an XPI when its extension ID and signature metadata match. Firefox performs
cryptographic signature and compatibility checks and manages its own add-on
state. Setup never edits `extensions.json` or marks an incompatible add-on active.
Setup requires a signed package and prefers one matching the shipped extension
source, falling back to the shipped signed release while local JS edits await
signing; `audit_variables.py` requires an exact source/package match.
Restart Firefox to discover copied extensions and load stylesheets. Missing
profiles or profile write failures produce a nonzero setup exit status.

Both Dusky update sequences run setup with `--update-installed` after refreshing
Matugen output, so pulling new source and its signed XPI also updates existing
profiles and the native host. This mode skips installations whose native host is
absent, preserving an explicit uninstall and leaving new installations to the
TUI's Install / Update action or a normal setup run.
The setup task runs on every update, including updates where only the XPI changes.
Restart Firefox after the update; a running browser retains its loaded extension.
The audit checks each registered profile's XPI against the current signed package.
Setup always registers the host under `~/.mozilla/native-messaging-hosts`,
Firefox 161's native-manifest lookup path, even with profiles only in the XDG
registry. Profile location does not determine the native-host lookup location.

The signed XPI under `~/.config/firefox_extentions/dusky_sites/xpi` is a separate
artifact. Source edits do **not** update it. Rebuild and re-sign the extension
before deploying JavaScript changes; modifying its ZIP contents invalidates its
signature. The audit reports source/package differences. For development,
load `extension/manifest.json` as a temporary add-on through `about:debugging`.

Extension 6.2.1+ repairs page-overwritten root palette properties and removed
fallback style elements in the mutation observer, before paint. Deferring this
repair to another animation frame could briefly expose the white page canvas
when returning to a themed page such as Discord. Palette revisions still use
the existing frame scheduler; repeated page mutations retain the repair limit.
Local source changes in `extension/` (currently 6.2.4) require rebuilding and
re-signing the XPI before Firefox will load them.

The Matugen palette template must remain document-scoped. Regenerate its output
through the normal wallpaper/color workflow after changing its scope or variable
names. Setup refuses an unscoped or non-private palette. An alternate `colorsPath`
must provide the same scoped declarations with `--dusky-palette-` variable names;
the native host reads declarations regardless of their document wrapper.

Uninstall remains available with `--uninstall` (or `--purge`) and optional
`--yes`. It removes Dusky-owned imports, stylesheets, extension copies, host,
and settings; it retains site templates and development sources. It removes
only the delimited managed preference block in `user.js`, leaving older
user preferences intact.

## Verification after Firefox updates

```sh
# Development source contracts; no deployment needed:
python3 ~/user_scripts/firefox/theme_matugen/templates/audit_variables.py --source-only

# Also check current installed files, prefs, imports, links and host:
python3 ~/user_scripts/firefox/theme_matugen/templates/audit_variables.py
```

The audit reads the **installed** Firefox's `omni.ja` files and identifies its
version, build and source revision. For an alternate application directory use
`--firefox-dir /path/to/firefox`. It checks theme keys against
`LightweightThemeManager`, CSS variables against shipped consumers, palette
references, default opt-in settings, and installation drift. A stale stylesheet,
missing import, broken link or mismatched installed host is a failure. Cached
`live_theme_cache.json` data is displayed as a timestamped snapshot, never as a
live query. Successful checks are source/file checks, not proof of rendering.

Useful Mozilla references:

- [Theme API colors and properties](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/theme)
- [LightweightThemeConsumer](https://searchfox.org/firefox-main/source/toolkit/modules/LightweightThemeConsumer.sys.mjs)
- [Browser color rules](https://searchfox.org/firefox-main/source/browser/themes/shared/browser-colors.css)
- [Marionette protocol for isolated runtime tests](https://firefox-source-docs.mozilla.org/remote/marionette/Protocol.html)

Verify colors in a disposable Firefox profile after source changes. Check actual
popup content parts, keyboard selection, disabled and checked menu items, focused
and failed findbar searches, sidebar/tab layouts, notification severity colors,
internal pages, print settings and preview, window-modal confirmations, Library
tree selection and search, and an ordinary HTTP page with blank/srcdoc frames.
Test explicit webpage opt-in and opt-out. Check both Firefox console messages and native-host
stderr. Internal Firefox CSS is not a stable public interface: repeat the source
and runtime checks when upgrading; do not add speculative selectors or old-version
fallbacks.
