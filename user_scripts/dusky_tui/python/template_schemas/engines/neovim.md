# Engine: `neovim`

- **Class:** `NeovimEngine` — `engines/neovim.py`
- **Schema:** `~/user_scripts/nvim/tui_dusky_nvim.py`
- **Requires:** the ISO's Neovim 0.12.6+ and Python 3.15+.
- **Target:** deployed files under `$XDG_CONFIG_HOME/nvim` (default `~/.config/nvim`).
  The maintained `dusky_nvim` tree is never selected by this schema.

## Scope and key mapping

Each engine instance edits one Lua file. The schema uses `target_file_override`
for the deployed init and plugin files, so router backups include every target.

| Scope | Binding | Target |
| --- | --- | --- |
| `options` | `vim.opt.<key> = <scalar>` | `lua/config/options.lua` |
| `globals` | `vim.g.dusky_bigfile_size = <integer>` | `init.lua` |
| `opts/view` | `opts.view.width`, `opts.view.side` | `lua/plugins/nvim-tree.lua` |
| `opts/filters` | `opts.filters.dotfiles` | `lua/plugins/nvim-tree.lua` |
| `opts/renderer` | `opts.renderer.group_empty` | `lua/plugins/nvim-tree.lua` |
| `opts` | `opts.max_file_size` | `lua/plugins/render-markdown.lua` |
| `opts/heading`, `opts/checkbox`, `opts/code` | literal fields within those tables | `lua/plugins/render-markdown.lua` |

State keys use `/`; item UIDs use the normal `scope.key` rule. For example,
`--set options.scrolloff=12` or `--set opts/view.side=right`. Bare `width` is
ambiguous, because the explorer and Markdown expose separate width settings.

## Read and write behavior

This is a focused engine for the audited Dusky Nvim layout, not a general Lua
interpreter. It tokenizes short scalar literals and named table fields, ignoring
comments and long strings. It does not execute configuration functions. Missing,
complex, or duplicate bindings cannot be rewritten. Arbitrary Lua expressions,
computed tables, mappings, and plugin enable/disable policy belong in Neovim.

The large-file global is the one supported missing binding: its displayed default
is 1 MiB. The first write inserts it immediately before the unique
`require("config.lazy")` in deployed `init.lua`. Subsequent writes update it.
An existing complex or duplicate assignment is refused, rather than overridden.

Boolean, integer, finite float, and quoted string values are supported. Strings
use Lua byte escapes for controls and preserve Unicode. The router supplies the
schema's numeric bounds and picker/cycle choices; the engine checks these for
both interactive and headless writes. Neovim validates native option values.
A clean Neovim subprocess syntax-checks the complete candidate before saving,
without loading plugins or running the configuration.

All bindings in a per-file batch are validated before replacement. Shared
`config_io` helpers detect stale writes and preserve ownership, permissions,
comments, and formatting with atomic replacement and fsync. F5 reloads externally
changed state. Batches across different target files are separate commits;
inspect failures rather than assuming a transaction across all four targets.
No hooks, generated runtime modules, or extra startup work are installed.

## TUI behavior and updates

Seven tabs expose 53 settings and eight native actions. Settings apply to newly
opened Neovim sessions. Filetype plugins can override native indentation defaults;
large-file policy overrides expensive buffer features, and diff windows retain
native diff folding. Markdown's limit is independent and measured in MiB.

Actions explicitly set `NVIM_APPNAME=nvim`, retain XDG paths, and suspend the TUI
while Neovim runs. Quit Neovim to return to the TUI:

- Plugin update, manager, and startup profile use Lazy's public Lua API and native
  interface. The `:Lazy` command is registered after interactive startup commands;
  using it with `-c` can resolve to the `:LazyDev` stub and fail instead.
- Language tools use Mason's native installation/update interface.
- Parser update uses `nvim-treesitter.update(...):wait(300000)` and reports success
  or failure inside Neovim; it updates installed parsers, not a new parser list.
- Health, Conform information, and deployed-config editing remain native views.

Updates can change deployed `lazy-lock.json` and installed data. They do not copy
changes to the maintained source. Redeploying through the separate manager
replaces deployed settings; copy desired settings back manually if they should
become ISO defaults. User profiles save the 42 native editor settings in the
default target; the framework excludes separately routed plugin settings and the
large-file global. Applying a user profile leaves those separate targets alone.
Profile reset also covers those 42 native settings. Reset other preferences per
row, or use the headless `--default` command for all 53 exposed settings. Profiles
and defaults are not full configuration backups.
The TUI does not run the deployment/reset scripts.

## Launch and verification

```bash
~/user_scripts/nvim/tui_dusky_nvim.py
~/user_scripts/nvim/tui_dusky_nvim.py --export-state
~/user_scripts/nvim/tui_dusky_nvim.py --set options.scrolloff=12
~/user_scripts/nvim/tui_dusky_nvim.py --set opts/view.width=40
~/user_scripts/nvim/tui_dusky_nvim.py --reset-key options.scrolloff
~/user_scripts/nvim/tui_dusky_nvim.py --backup --default
~/user_scripts/nvim/tui_dusky_nvim.py --restore
```

Engine integration tests use isolated copies and exercise comment/Unicode round
trips, invalid values, bounds, stale state, ambiguity, threshold insertion, schema
routing, and defaults:

```bash
python -m unittest discover -s ~/user_scripts/dusky_tui/python/tests -p test_neovim_engine.py -v
```

Native plugin actions follow [Lazy's documented commands](https://lazy.folke.io/usage).
Option validation uses [Neovim's native API](https://neovim.io/doc/user/api/#nvim_set_option_value()).
