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
		on_attach = function(buf)
			return not vim.b[buf].dusky_bigfile
		end,

		-- 3. The "Dual Mode" Logic
		worktrees = {
			{
				toplevel = os.getenv("HOME"),
				gitdir = os.getenv("HOME") .. "/dusky",
			},
		},
	},
}
