-- Bootstrap lazy.nvim from its current development branch.
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.uv.fs_stat(lazypath) then
	local lazyrepo = "https://github.com/folke/lazy.nvim.git"
	local out = vim.fn.system({ "git", "clone", "--filter=blob:none", "--branch=main", lazyrepo, lazypath })
	if vim.v.shell_error ~= 0 then
		vim.api.nvim_echo({
			{ "Failed to clone lazy.nvim:\n", "ErrorMsg" },
			{ out, "WarningMsg" },
		}, true, {})
		os.exit(1)
	end
end
vim.opt.rtp:prepend(lazypath)

-- importing.

require("config.globals")
require("config.options")
require("config.keymaps")
require("config.autocmds")

-- Setup lazy.nvim

require("lazy").setup({

	rocks = {
		enabled = false,
		hererocks = false,
	},

	spec = {
		{ import = "plugins" },
	},
	performance = {
		rtp = {
			disabled_plugins = {
				"netrw",
				"netrwPlugin",
				"tohtml",
				"tutor",
				"zipPlugin", -- OPTIMIZATION: Disable zip plugin if not used
			},
		},
	},
	checker = {
		enabled = false, -- Updates belong to explicit :Lazy update / deployment sync.
		notify = false,
	},
	change_detection = {
		notify = false, -- OPTIMIZATION: Less spam
	},
})
