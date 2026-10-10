#!/usr/bin/env python3
"""Dusky Nvim configurator for the deployed configuration, never its source copy."""
import os
import sys
import shlex
from pathlib import Path
lazy import subprocess

_DUSKY_TUI_ROOT = Path.home() / 'user_scripts' / 'dusky_tui'
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

_CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'nvim'
ENGINE_TYPE = 'neovim'
TARGET_FILE = str(_CONFIG / 'lua/config/options.lua')
APP_TITLE = 'Dusky Nvim'
DEFAULT_MODE = 'auto'
THEME_FILE = '~/.config/matugen/generated/dusky_tui.json'
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = 'Profiles'
TABS = ['Settings', 'Editing', 'Search', 'Windows', 'Features', 'Updates', 'Profiles']
TAB_NOTICES = {
    0: {'level': 'info', 'message': 'Edits deployed Neovim files. Reopen Neovim to apply changes.'},
    4: {'level': 'info', 'message': 'Large-file policy can override spell checking and folds. Filetype plugins can override indentation.'},
    5: {'level': 'info', 'message': 'Actions open native Neovim interfaces. Quit Neovim to return here. Updates require internet.'},
    6: {'level': 'info', 'message': 'Profiles and profile reset cover native editor settings. Other preferences reset individually. Manager redeployment replaces deployed edits.'},
}


def option(label, key, default, help_text, *, group, type_=None, **kwargs):
    kind = type_ or ('bool' if isinstance(default, bool) else 'int' if isinstance(default, int) else 'string')
    return ConfigItem(label=label, key=key, scope='options', type_=kind, default=default,
                      group=group, extended_help=help_text, **kwargs)


def feature(label, key, scope, file, default, help_text, *, group, type_=None, **kwargs):
    kind = type_ or ('bool' if isinstance(default, bool) else 'int' if isinstance(default, int) else 'string')
    return ConfigItem(label=label, key=key, scope=scope, type_=kind, default=default,
                      target_file_override=str(_CONFIG / file), group=group,
                      extended_help=help_text, **kwargs)


def nvim_command(*args):
    # Always select the deployed nvim app, even if the launcher uses NVIM_APPNAME.
    return shlex.join(['env', 'NVIM_APPNAME=nvim', 'nvim', *args])


def action(label, key, command, help_text, *, group):
    return ConfigItem(label=label, key='action_' + key, type_='action', default=command,
                      group=group, force_interactive=True, extended_help=help_text)


SCHEMA = {
    0: [
        option('Line numbers', 'number', True, 'Show absolute line numbers.', group='Display'),
        option('Relative numbers', 'relativenumber', True, 'Show relative line counts for motions; the cursor line follows the absolute-number setting.', group='Display'),
        option('Wrap long lines', 'wrap', True, 'Wrap lines visually without changing file contents.', group='Display'),
        option('Vertical context', 'scrolloff', 10, 'Minimum visible lines above and below the cursor.', group='Display', min_val=0, max_val=100, step=1),
        option('Horizontal context', 'sidescrolloff', 8, 'Minimum visible columns beside the cursor when wrapping is off.', group='Display', min_val=0, max_val=100, step=1),
        option('Sign column', 'signcolumn', 'yes', 'Reserve room for diagnostics and Git signs.', group='Display', type_='cycle', options=['yes', 'auto', 'no', 'number']),
        option('Matching brackets', 'showmatch', True, 'Briefly highlight the matching bracket when typing a closing bracket.', group='Display'),
        option('Bracket duration', 'matchtime', 2, 'Matching-bracket display duration in tenths of a second.', group='Display', min_val=0, max_val=20, step=1),
        option('Conceal level', 'conceallevel', 0, 'Control hiding of syntax-concealed text. Markdown rendering also manages concealment while active.', group='Display', min_val=0, max_val=3, step=1),
        option('Conceal cursor modes', 'concealcursor', '', 'Modes where concealed text remains hidden at the cursor: n, v, i, c. Empty shows it.', group='Display', options=['', 'n', 'nc', 'nvic']),
        option('Mouse modes', 'mouse', 'a', 'Enable mouse interaction in all modes (a), selected modes, or none (empty).', group='Interaction', type_='cycle', options=['a', '', 'n', 'nv']),
        option('Mapping timeout', 'timeoutlen', 500, 'Milliseconds to wait for the remainder of a multi-key mapping.', group='Interaction', min_val=0, max_val=3000, step=50),
        option('Idle update interval', 'updatetime', 250, 'Milliseconds before CursorHold events; smaller values produce more frequent idle callbacks.', group='Interaction', min_val=50, max_val=4000, step=50),
        option('Popup height', 'pumheight', 10, 'Maximum native completion popup rows. Zero lets Neovim choose.', group='Interaction', min_val=0, max_val=40, step=1),
        option('Popup transparency', 'pumblend', 10, 'Native popup transparency percentage; plugin windows may have their own styling.', group='Interaction', min_val=0, max_val=100, step=5),
    ],
    1: [
        option('Insert spaces', 'expandtab', True, 'Use spaces for inserted indentation. Existing file contents are unchanged.', group='Indentation'),
        option('Tab display width', 'tabstop', 2, 'Display width of a literal tab. Filetype plugins can override this default.', group='Indentation', min_val=1, max_val=16, step=1),
        option('Indent width', 'shiftwidth', 2, 'Width used by indentation commands; zero follows tabstop.', group='Indentation', min_val=0, max_val=16, step=1),
        option('Soft tab width', 'softtabstop', 2, 'Spaces inserted or removed by Tab/Backspace. This configurator exposes nonnegative widths.', group='Indentation', min_val=0, max_val=16, step=1),
        option('Copy indentation', 'autoindent', True, 'Copy the previous line’s indentation when starting a new line.', group='Indentation'),
        option('Smart indentation', 'smartindent', True, 'Basic automatic indentation when no filetype indent expression takes precedence.', group='Indentation'),
        option('Spell checking', 'spell', True, 'Default spelling preference. Disabled temporarily for files above the large-file threshold.', group='Spelling'),
        option('Spelling languages', 'spelllang', 'en_us', 'Comma-separated installed spell dictionaries. Additional dictionaries may require provisioning.', group='Spelling', options=['en_us', 'en_gb', 'en_us,en_gb']),
        option('Persistent undo', 'undofile', True, 'Save undo history between sessions in Neovim’s state directory.', group='Files'),
        option('Reload disk changes', 'autoread', True, 'Reload externally changed files when the buffer has no unsaved edits; Dusky Nvim checks on focus return.', group='Files'),
        option('Swap files', 'swapfile', False, 'Write swap files for recovery during editing.', group='Files'),
        option('Backup before write', 'writebackup', False, 'Create a temporary backup while replacing a file.', group='Files'),
        option('Keep backup files', 'backup', False, 'Retain backup files after successful writes.', group='Files'),
    ],
    2: [
        option('Ignore case', 'ignorecase', True, 'Use case-insensitive searches unless smartcase applies.', group='Matching'),
        option('Smart case', 'smartcase', True, 'Make searches case-sensitive when the pattern includes uppercase letters.', group='Matching'),
        option('Highlight matches', 'hlsearch', True, 'Highlight search matches. Escape clears the active highlight.', group='Matching'),
        option('Incremental search', 'incsearch', True, 'Preview matches as the search pattern is typed.', group='Matching'),
        option('Path completion case', 'wildignorecase', True, 'Ignore case when completing file and directory names.', group='Completion'),
        option('Command completion', 'wildmode', 'longest:full,full', 'Native command-line completion behavior. nvim-cmp also provides command/path completion.', group='Completion', options=['longest:full,full', 'full', 'longest,list', 'list:full']),
        option('Syntax column limit', 'synmaxcol', 500, 'Stop regular-expression syntax highlighting after this column. Zero removes the limit; Tree-sitter uses its own policy.', group='Performance', min_val=0, max_val=5000, step=100),
        feature('Large-file limit (bytes)', 'dusky_bigfile_size', 'globals', 'init.lua', 1048576,
                'Files strictly larger than this limit use the light mode on read. Markdown has an independent MiB limit. This is inserted before startup when not already configured.',
                group='Performance', min_val=1, max_val=1073741824, step=262144),
    ],
    3: [
        option('Split below', 'splitbelow', True, 'Open horizontal splits below the current window.', group='Splits'),
        option('Split to the right', 'splitright', True, 'Open vertical splits to the right of the current window.', group='Splits'),
        option('Enable folding', 'foldenable', True, 'Enable fold display. Dusky Nvim selects Tree-sitter, manual, or native diff folding per buffer/window.', group='Folds'),
        option('Initial fold level', 'foldlevelstart', 99, 'Fold level when editing a new buffer. A high value opens all ordinary folds.', group='Folds', min_val=0, max_val=99, step=1),
        option('Default fold level', 'foldlevel', 99, 'Default nesting depth visible before folds close. Initial fold level can override it when opening files.', group='Folds', min_val=0, max_val=99, step=1),
        option('Floating transparency', 'winblend', 0, 'Default transparency percentage for floating windows; plugins may override it.', group='Appearance', min_val=0, max_val=100, step=5),
        option('Cursor shapes', 'guicursor', 'n-v-c:hor20-Cursor,i-ci-ve:ver25-Cursor,r-cr-o:hor20-Cursor', 'Native cursor shape specification. Neovim validates this before saving.', group='Appearance', options=['n-v-c:hor20-Cursor,i-ci-ve:ver25-Cursor,r-cr-o:hor20-Cursor', 'n-v-c:block,i-ci-ve:ver25,r-cr-o:hor20']),
    ],
    4: [
        feature('Explorer width', 'width', 'opts/view', 'lua/plugins/nvim-tree.lua', 30, 'Initial explorer width in columns.', group='Explorer', min_val=15, max_val=120, step=5),
        feature('Explorer side', 'side', 'opts/view', 'lua/plugins/nvim-tree.lua', 'left', 'Place the explorer on the left or right.', group='Explorer', type_='cycle', options=['left', 'right']),
        feature('Hide dotfiles', 'dotfiles', 'opts/filters', 'lua/plugins/nvim-tree.lua', False, 'Hide names starting with a dot in the explorer.', group='Explorer'),
        feature('Group empty folders', 'group_empty', 'opts/renderer', 'lua/plugins/nvim-tree.lua', True, 'Collapse chains of directories with a single child into one explorer row.', group='Explorer'),
        feature('Markdown limit (MiB)', 'max_file_size', 'opts', 'lua/plugins/render-markdown.lua', 1, 'Independent rendering limit in MiB. Large rereads disable rendering; use :RenderMarkdown buf_enable after shrinking the file.', group='Markdown', type_='float', min_val=0.25, max_val=64, step=0.25),
        feature('Heading signs', 'sign', 'opts/heading', 'lua/plugins/render-markdown.lua', False, 'Show Markdown heading icons in the sign column.', group='Markdown'),
        feature('Checkbox rendering', 'enabled', 'opts/checkbox', 'lua/plugins/render-markdown.lua', True, 'Render Markdown task checkboxes.', group='Markdown'),
        feature('Code block signs', 'sign', 'opts/code', 'lua/plugins/render-markdown.lua', False, 'Show Markdown code block signs.', group='Markdown'),
        feature('Code block width', 'width', 'opts/code', 'lua/plugins/render-markdown.lua', 'block', 'Render code backgrounds to the code block or full window width.', group='Markdown', type_='cycle', options=['block', 'full']),
        feature('Code right padding', 'right_pad', 'opts/code', 'lua/plugins/render-markdown.lua', 4, 'Extra columns on the right of rendered code blocks.', group='Markdown', min_val=0, max_val=16, step=1),
    ],
    5: [
        # :Lazy is registered on VeryLazy, after interactive startup -c commands.
        # Calling the public API avoids resolving :Lazy as the :LazyDev stub.
        action('Update plugins', 'update_plugins', nvim_command('-c', 'lua require("lazy").update()'), 'Update installed plugins using native Lazy UI. This updates the deployed lazy-lock.json; it does not copy the lockfile back to the maintained source.', group='Plugins'),
        action('Plugin manager', 'plugins', nvim_command('-c', 'lua require("lazy").home()'), 'Inspect installed plugins and choose install, sync, update, or restore operations in the native interface.', group='Plugins'),
        action('Plugin startup profile', 'profile', nvim_command('-c', 'lua require("lazy").profile()'), 'Inspect native plugin loading timings. Open a representative file inside Neovim to include its demand-loaded features.', group='Plugins'),
        action('Language tools', 'tools', nvim_command('-c', 'Mason'), 'Manage language servers and formatters in Mason. Its native UI provides installation and update actions.', group='Tools'),
        action('Update parsers', 'parsers', nvim_command('-c', 'lua local ok,err=pcall(function() assert(require("nvim-treesitter").update(nil,{summary=true}):wait(300000),"Parser update failed") end); vim.notify(ok and "Dusky Nvim parsers updated" or tostring(err),ok and vim.log.levels.INFO or vim.log.levels.ERROR)'), 'Update installed Tree-sitter parsers with the provisioned nvim-treesitter API. Progress and any failure appear in Neovim. This can take several minutes.', group='Tools'),
        action('Health check', 'health', nvim_command('-c', 'checkhealth'), 'Run Neovim and plugin health checks; no packages are installed by this action.', group='Inspect'),
        action('Formatter information', 'formatters', nvim_command('-c', 'ConformInfo'), 'Inspect formatter availability and logs using the native Conform interface.', group='Inspect'),
        action('Edit deployed config', 'config', nvim_command(str(_CONFIG / 'init.lua')), 'Open the deployed init.lua for advanced configuration. The normal leader config shortcut targets the maintained source, so this action opens the deployed path explicitly.', group='Inspect'),
    ],
    6: [],  # The frontend supplies save/import/reset and saved-profile rows.
}

if __name__ == '__main__':
    router = _DUSKY_TUI_ROOT / 'python/main/main.py'
    sys.exit(subprocess.run([sys.executable, str(router), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
