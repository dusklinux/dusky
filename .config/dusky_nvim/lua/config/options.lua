-- ================================================================================================
-- TITLE : NeoVim options
-- ABOUT : basic settings native to neovim
-- ================================================================================================

-- Basic Settings
vim.opt.background = "dark" -- Force dark background to bypass terminal DSR query delay
vim.opt.number = true -- Line numbers
vim.opt.relativenumber = true -- Relative line numbers
-- vim.opt.cursorline = true -- Highlight current line
vim.opt.scrolloff = 10 -- Keep 10 lines above/below cursor
vim.opt.sidescrolloff = 8 -- Keep 8 columns left/right of cursor
vim.opt.wrap = true -- wrap lines
vim.opt.cmdheight = 1 -- Command line height
vim.opt.spell = true -- Enable spell checking
vim.opt.spelllang = "en_us" -- Set language for spellchecking

-- Tabbing / Indentation
vim.opt.tabstop = 2 -- Tab width
vim.opt.shiftwidth = 2 -- Indent width
vim.opt.softtabstop = 2 -- Soft tab stop
vim.opt.expandtab = true -- Use spaces instead of tabs
vim.opt.smartindent = true -- Smart auto-indenting
vim.opt.autoindent = true -- Copy indent from current line
vim.opt.grepprg = "rg --vimgrep" -- Use ripgrep if available
vim.opt.grepformat = "%f:%l:%c:%m" -- filename, line number, column, content

-- Search Settings
vim.opt.ignorecase = true -- Case-insensitive search
vim.opt.smartcase = true -- Override ignorecase when the search contains uppercase
vim.opt.hlsearch = true -- Highlight search results
vim.opt.incsearch = true -- Show matches as you type

-- Visual Settings
vim.opt.termguicolors = true -- Enable 24-bit colors
vim.opt.signcolumn = "yes" -- Always show sign column
--vim.opt.colorcolumn = "100" -- Show column at 100 characters
vim.opt.showmatch = true -- Highlight matching brackets
vim.opt.matchtime = 2 -- How long to show matching bracket
vim.opt.completeopt = "menu,menuone,noinsert,noselect,popup,fuzzy" -- Native completion; nvim-cmp configures its own popup
vim.opt.showmode = false -- Don't show mode in command line
vim.opt.laststatus = 3 -- Match lualine globalstatus
vim.opt.ruler = false -- Hide default ruler (lualine handles it)
vim.opt.pumheight = 10 -- Popup menu height
vim.opt.pumblend = 10 -- Popup menu transparency
vim.opt.winblend = 0 -- Floating window transparency
vim.opt.conceallevel = 0 -- Don't hide markup
vim.opt.concealcursor = "" -- Show markup even on cursor line
vim.opt.redrawtime = 10000 -- Timeout for syntax highlighting redraw
vim.opt.maxmempattern = 20000 -- Max memory for pattern matching
vim.opt.synmaxcol = 500 -- Limit regex syntax highlighting on long lines

-- File Handling
vim.opt.backup = false -- Don't create backup files
vim.opt.writebackup = false -- Don't backup before overwriting
vim.opt.swapfile = false -- Don't create swap files
vim.opt.undofile = true -- Persistent undo
vim.opt.updatetime = 250 -- Time in ms to trigger CursorHold
vim.opt.timeoutlen = 500 -- Key mapping timeout
vim.opt.ttimeoutlen = 0 -- No wait for key code sequences
vim.opt.autoread = true -- Auto-reload file if changed outside
vim.opt.autowrite = false -- Don't auto-save on some events
-- diffopt: start from clean slate to avoid duplicate linematch:40 + linematch:60 seen in :checkhealth
vim.opt.diffopt = "internal,filler,closeoff,vertical,algorithm:patience,linematch:60,indent-heuristic,inline:char"

-- Neovim creates its default undo directory under stdpath("state") on write.

-- Behavior Settings
-- vim.opt.errorbells = false -- Disable error sounds
vim.opt.backspace = "indent,eol,start" -- Make backspace behave naturally
vim.opt.autochdir = false -- Don't change directory automatically
-- vim.opt.iskeyword:append("-") -- Treat dash as part of a word
vim.opt.path:append("**") -- Search into subfolders with `gf`
vim.opt.selection = "inclusive" -- Use inclusive selection
vim.opt.mouse = "a" -- Enable mouse support
vim.opt.clipboard:append("unnamedplus") -- Use system clipboard
vim.opt.modifiable = true -- Allow editing buffers
vim.opt.wildmenu = true -- Enable command-line completion menu
vim.opt.wildmode = "longest:full,full" -- Completion mode for command-line
vim.opt.wildignorecase = true -- Case-insensitive tab completion in commands

-- Folding Settings (0.12 native: foldexpr requires treesitter parser for buffer, else fallback)
vim.opt.foldmethod = "expr"
vim.opt.foldexpr = "v:lua.vim.treesitter.foldexpr()"
vim.opt.foldlevel = 99
vim.opt.foldlevelstart = 99 -- Ensure folds open on new buffers
vim.opt.foldenable = true
vim.opt.foldtext = "" -- Show the original folded line with its highlighting

-- Split Behavior
vim.opt.splitbelow = true -- Horizontal splits open below
vim.opt.splitright = true -- Vertical splits open to the right
