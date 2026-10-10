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
			-- Load the explorer only when a directory is actually opened.
			vim.api.nvim_create_autocmd("BufEnter", {
				group = vim.api.nvim_create_augroup("DuskyDirectory", { clear = true }),
				callback = function(args)
					local stat = vim.uv.fs_stat(args.file)
					if stat and stat.type == "directory" then
						require("nvim-tree.api").tree.open({ path = args.file })
					end
				end,
			})
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
			hijack_netrw = false, -- Avoid cleanup of netrw groups that were never loaded.
			view = {
				width = 30,
				side = "left",
			},
			renderer = {
				group_empty = true,
			},
		},
		config = function(_, opts)
			-- The plugin handles subsequent directories; retire the bootstrap listener.
			vim.api.nvim_del_augroup_by_name("DuskyDirectory")
			require("nvim-tree").setup(opts)
		end,
	},
}
