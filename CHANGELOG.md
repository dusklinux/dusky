# Changelog

All notable changes to Dusky are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Dusky tracks Arch Linux, so it is a rolling configuration rather than a project with
long-lived release branches — `main` is always the supported version.

Your installed version:

```bash
cat ~/.config/dusky/version
```

> **Note on history:** Dusky was developed for its first several months without a
> changelog. Entries below `4.0` are therefore not reconstructed — the commit history
> from 2025-11-14 onward is the authoritative record for that period. Everything from
> `4.0` forward is tracked here.

---

## [Unreleased]

### Added

- Open source project infrastructure: contributing guide, code of conduct, security
  policy, support guide, issue and pull request templates.
- `lint` CI workflow — checks shell syntax, Python syntax, and CRLF line endings on every
  push and pull request.
- `.gitattributes` pinning all text files to LF. Prevents a contributor with
  `core.autocrlf=true` from committing scripts that fail on Arch with
  `/usr/bin/env: 'bash\r': No such file or directory`.
- `.editorconfig` matching the existing 4-space convention.

### Fixed

- `user_scripts/dusky_tui/bash/for_equal_assignment_delimiter_only/dusky_tui_4.3.0.sh`:
  an `if … then` block was closed with `}` instead of `fi`, which made the entire script
  fail to parse and therefore impossible to run.
- README: corrected the setup subscript path
  (`user_scripts/setup_scripts/scripts/` → `user_scripts/arch_setup_scripts/scripts/`)
  and the drive manager path
  (`user_scripts/drives/drive_manager.sh` → `user_scripts/drives/drive_manager/drive_manager.py`,
  configured via `drives.toml`).
- README: removed a reference to `user_scripts/network_manager/nmcli_wifi.sh`, which does
  not exist in the repository.

---

## [4.0]

The current release line. Highlights from this cycle:

### Added

- **Dusky Control Center** — a single GUI surface for system settings and features.
- **Firefox theming via Matugen**, built on
  [MatugenFox](https://github.com/Ubaidullah-Web-Dev/MatugenFox), extending the unified
  colour scheme into the browser.
- **`ai_bridge` Firefox extension** — bridges the desktop to browser-based AI chat sites,
  with focus handling, workspace switching, and per-site configuration.
- **Matugen theming TUI** for managing themes and templates from the terminal.
- **Dusky ISO** — a fully offline Arch installer image built from
  `user_scripts/arch_iso_scripts/`.
- **Gaming support** — FPS limiter and a gaming package setup script.
- **iOS sideloading tooling** under `user_scripts` and the accompanying notes in the
  Obsidian vault.
- **Waybar layout options** — horizontal and vertical, selectable during setup and
  toggleable from Rofi (block, circular, and minimal variants).
- **Network throttle** — traffic shaping and per-application quotas with a TUI, including
  a pytest suite.

### Changed

- Reorganised `user_scripts/` into clearer functional directories.
- Template handling now supports multiple passes without duplicating entries, and
  templates can be deleted or reset.

---

## Release process

Dusky does not currently tag releases. Adopting tags would let users pin a known-good
state and would give this changelog anchors to link to:

```bash
# Bump the version file, then tag it.
echo "v4.1" > .config/dusky/version
git commit -am "release: v4.1"
git tag -a v4.1 -m "v4.1"
git push origin main --tags
```

GitHub will then generate a Releases page, and each entry here can link to its tag.

<!--
Link references. Add one per tag once tagging begins, e.g.:
[Unreleased]: https://github.com/dusklinux/dusky/compare/v4.0...HEAD
[4.0]: https://github.com/dusklinux/dusky/releases/tag/v4.0
-->
