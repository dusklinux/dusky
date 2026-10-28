# Selected installed fonts for Dusky's Rust GUIs

Center, Tray, Papers, and Snap patch fontdb through their Cargo manifests.
The local copy is fontdb 0.23.0 (MIT). Its system-discovery implementation is
replaced by `src/dusky.rs`; explicit file/directory loading APIs are unchanged.
This covers Cosmic Text, SVG text, and Snap's annotation renderer.

The loader asks `fc-list` for cached family/style/file metadata once per process,
then parses only these seven installed faces:

- Atkinson Hyperlegible: regular, bold, italic, bold italic.
- DejaVu Sans: Book (regular) and bold, for fallback glyphs.
- DejaVu Sans Mono: Book (regular), for monospace fields.

Fontconfig locates files without hardcoded directories. Duplicate TTF/OTF faces
are deduplicated by family/style. No Atkinson or DejaVu font binaries are stored
in this repository or embedded in the applications. The package profile supplies Atkinson and
`ttf-dejavu`; no startup service is needed. Fontconfig's package-install hooks
maintain its normal metadata caches.

`ui_fonts.rs` restores the generic family aliases after Cosmic Text sets its
platform defaults. All four applications default to Atkinson Hyperlegible.
Iced retains its own icon font, and the panels' SVG icons are font independent.
Snap's existing `Sans` documents still use the sans-serif alias.

The bounded fallback set supports common Latin, Greek, Cyrillic, Arabic, Hebrew,
and symbols, but not complete Unicode/CJK/emoji coverage. Other installed
families are not automatically loaded; unknown names use the selected fallback.
To extend coverage, add the required face to the loader and package profile.

The four setup scripts fingerprint the shared loader alongside their application
sources, so changing it invalidates native and ISO-package reuse. Builds remain
offline and use the normal Cargo lockfiles. Keep this shared tree with the
application sources when building the ISO.

`DUSKY_FONT_FAMILY` is exported by the patch and referenced by every application.
If a dependency update bypasses this patch, compilation fails instead of silently
restoring the full font scan. When updating fontdb, port the loader and rerun the
shared shaping tests, SVG/UI launch checks, and Snap annotation tests. The
upstream README in `fontdb/` describes its original discovery behavior.

## Verification (2026-10-28)

All four native release binaries were rebuilt offline and installed. With only
the installed font-file cache evicted, Center's first frame improved from
3260.1 ms before the change to 111.3 / 110.9 ms afterward. Tray presented at
125.0 ms. Papers attached its first Wayland buffer at 140.9 ms; Snap's editor
attached at 239.1 ms. The latter two measure protocol attachment, not presentation.

File-access traces for each application opened exactly seven unique font files
(1,934,168 bytes on this machine); the Fontconfig query ran once per process.
The UI/SVG database and multilingual shaping checks passed. Snap's unit tests
passed (44 passed, 2 ignored), including annotation export and legacy Sans
documents. Shared-loader changes invalidate all four installer fingerprints.

The former warmup service was disabled and its deployed unit removed. A real
reboot with this final installed-font version remains to be verified; the above
measurements are controlled cold-font-cache tests. Missing packages or stale
Fontconfig caches can still affect discovery; install the supplied font profile
and allow the normal font-cache package hooks to complete.
