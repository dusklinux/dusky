-- lua/plugins/bufferline.lua
return {
	"akinsho/bufferline.nvim",
	event = "VeryLazy",
	dependencies = { "nvim-tree/nvim-web-devicons" },
	keys = {
		{ "<tab>", "<cmd>BufferLineCycleNext<cr>", desc = "Next Buffer" },
		{ "<s-tab>", "<cmd>BufferLineCyclePrev<cr>", desc = "Previous Buffer" },
		{ "<leader>bp", "<cmd>BufferLineTogglePin<cr>", desc = "Pin Buffer" },
		{ "<leader>bc", "<cmd>BufferLinePickClose<cr>", desc = "Close Picked Buffer" },
	},
	opts = {
		options = {
			mode = "buffers",
			diagnostics = "nvim_lsp",
			always_show_bufferline = false,
			show_buffer_close_icons = false,
			show_close_icon = false,
			offsets = {
				{
					filetype = "NvimTree",
					text = "File Explorer",
					text_align = "left",
					separator = true,
				},
			},
		},
	},
	config = function(_, opts)
		require("bufferline").setup(opts)
	end,
}
