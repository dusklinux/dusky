-- ~/.config/nvim/lua/plugins/highlight_colors.lua
return {
	{
		"brenoprata10/nvim-highlight-colors",
		event = { "BufReadPost", "BufNewFile" },
		cmd = "HighlightColors",
		keys = { { "<leader>hc", "<cmd>HighlightColors Toggle<cr>", desc = "Toggle highlight colors" } },
		opts = {
			render = "background",
			enable_named_colors = true,
		},
	},
}
