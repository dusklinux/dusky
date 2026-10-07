return {
	"lewis6991/gitsigns.nvim",
	event = { "BufReadPre", "BufNewFile" },
	opts = {
		-- 1. The Aesthetic "Rice" Settings
		signs = {
			add = { text = "" },
			change = { text = "" },
			delete = { text = "󰅘" },
			topdelete = { text = "" },
			changedelete = { text = "" },
			untracked = { text = "󰧠" },
		},
		numhl = false,
		linehl = false,

		-- 2. Behavior Settings
		signcolumn = true,

		-- Critical for dotfiles: show the "new file" bar for untracked files
		attach_to_untracked = true,

		-- 3. The "Dual Mode" Logic
		worktrees = {
			{
				toplevel = os.getenv("HOME"),
				gitdir = os.getenv("HOME") .. "/dusky",
			},
		},
	},
	-- Match Git signs to the active diagnostic palette.
	config = function(_, opts)
		require("gitsigns").setup(opts)

		vim.api.nvim_set_hl(0, "GitSignsAdd", { link = "DiagnosticOk" })
		vim.api.nvim_set_hl(0, "GitSignsUntracked", { link = "DiagnosticOk" })
		vim.api.nvim_set_hl(0, "GitSignsChange", { link = "DiagnosticWarn" })
		vim.api.nvim_set_hl(0, "GitSignsChangeDelete", { link = "DiagnosticWarn" })
		vim.api.nvim_set_hl(0, "GitSignsDelete", { link = "DiagnosticError" })
		vim.api.nvim_set_hl(0, "GitSignsTopDelete", { link = "DiagnosticError" })
		vim.api.nvim_set_hl(0, "GitSignsCurrentLineBlame", { link = "NonText", default = true })
	end,
}
