# Engine: `gsettings` / `dconf`

Uses Gio.Settings in process, with a `gsettings` CLI fallback for engine consumers.
The GTK TUI requires PyGObject to obtain installed schema defaults, enum choices,
ranges, descriptions, and writability. Supported baseline: Python 3.15 and the
ISO's installed GLib/PyGObject schemas; no older-Python compatibility paths.

- `scope`: installed, non-relocatable GSettings schema ID; never `DEFAULT`.
- `key`: exact installed schema key.
- Target: `$XDG_CONFIG_HOME/dconf/user` (default `~/.config/dconf/user`) is a
  frontend watch identifier, not a selectable file backend. GSettings honors
  `GSETTINGS_BACKEND` and `DCONF_PROFILE`; passing another filename does not
  redirect writes. Custom profiles may require explicit refresh in the TUI.
- State: both `scope/key` and `scope.key`. Scalars use literal strings; booleans
  use `true`/`false`. Gio compound values use serialized GVariant text.
- Writes: schema type controls Gio conversion, including unsigned and 64-bit
  integers. Strings retain quotes and whitespace. Invalid booleans, nonfinite
  numbers, missing keys, range violations, and locked keys report errors.
  CLI consumers should specify the correct scalar `item_type`; compound variant
  writes require Gio. NUL characters are rejected before calling GLib, which
  otherwise silently truncates strings.
- Batch results report each key separately. Batches are **not atomic** across
  schemas. Gio flushes once, then checks the observed value of each accepted
  write and its user override, including writes equal to schema defaults.
  CLI dconf commit warnings are failures even if the process exits zero.
  Successful writes remain saved when another key fails; canonical observed
  values are cached and published to the frontend. A saved cursor value whose
  hook fails is reconciled as saved, while the hook failure is still reported.
- Cursor updates/reset read both active theme and size, run
  `hyprctl setcursor THEME SIZE` in an active Hyprland session, and atomically
  replace `~/.icons/default/index.theme`. Hyprctl must return its `ok` response,
  not just exit zero. Hook errors are reported separately
  from the fact that the GSettings value has already been saved.
- Defaults in the TUI come from `get_default_value`, including installed
  overrides. Applying defaults writes explicit values; `reset_key` removes a
  user override and verifies that it was removed. Reset keys remain tracked in
  cache with their effective values. Saved presets are snapshots of managed settings;
  keys missing from an imported/older snapshot use current installed defaults.
  Candidate values are validated against the same schema rules as writes before
  applying a preset, so malformed snapshots cannot silently coerce values or
  start a partial transaction.
- Generic `--backup`/`--restore` file copies are unsupported for this live backend.
  Use saved presets for managed preferences or `dconf dump`/`dconf load` for
  backend backups. Never replace a live dconf database with a file-engine restore.

GTK, privacy, media, sound, proxy, and terminal preferences affect only software
that honors their schemas. They do not configure Hyprland input/window policy,
Waybar, PipeWire, system routing, MIME associations, or enforce hardware access.
Optional Nemo/Nautilus controls appear only when their schema/key exists. Runtime
geometry, histories, deprecated GTK controls, and compositor-only GNOME settings
are omitted. GTK3 and GTK4 dialogs are discovered independently.

Passing `items=[]` registers an empty catalog; omitting `items` lets the engine
browse all installed fixed schemas. Empty loads and empty batches perform no
backend I/O. Integer metadata, including unsigned/64-bit bounds, is shared
between the engine and TUI rather than duplicated. Optional cursor actions
appear only when the interface schema exists.
