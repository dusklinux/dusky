-- lua/plugins/nvim-tree.lua
-- Configurations for nvim-tree.lua (File Explorer)

return {
	{
		"nvim-tree/nvim-tree.lua",
		dependencies = {
			"nvim-tree/nvim-web-devicons",
		},
		cmd = { "NvimTreeToggle", "NvimTreeFocus" },
		init = function()
			vim.g.loaded_netrw = 1
			vim.g.loaded_netrwPlugin = 1
		end,
		keys = {
			{ "<leader>e", "<cmd>NvimTreeToggle<cr>", desc = "Toggle File Explorer" },
			{ "<leader>m", "<cmd>NvimTreeFocus<cr>", desc = "Focus on File Explorer" },
		},
		opts = {
			filters = {
				dotfiles = false,
			},
			disable_netrw = true,
			hijack_netrw = false,
			view = {
				width = 30,
				side = "left",
			},
			renderer = {
				group_empty = true,
			},
		},
		config = function(_, opts)
			require("nvim-tree").setup(opts)
		end,
	},
}
