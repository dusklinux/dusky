return {
	"MeanderingProgrammer/render-markdown.nvim",
	ft = { "markdown" },
	dependencies = {
		"nvim-treesitter/nvim-treesitter",
		"nvim-tree/nvim-web-devicons",
	},
	opts = {
		max_file_size = 1, -- MiB; match the default large-file threshold.
		latex = { enabled = false }, -- No LaTeX parser or converter is bundled.
		heading = {
			sign = false,
			icons = { "◉ ", "○ ", "✸ ", "✿ ", "✦ ", "✧ " },
		},
		checkbox = {
			enabled = true,
			unchecked = { icon = "󰄱 " },
			checked = { icon = "󰄵 " },
		},
		code = {
			sign = false,
			width = "block",
			right_pad = 4,
		},
	},
	config = function(_, opts)
		local markdown = require("render-markdown")
		markdown.setup(opts)
		-- The plugin's size check only runs at attachment. Also handle rereads.
		vim.api.nvim_create_autocmd("BufReadPre", {
			group = vim.api.nvim_create_augroup("DuskyMarkdownSize", { clear = true }),
			callback = function(args)
				if vim.bo[args.buf].filetype ~= "markdown" then
					return
				end
				local stat = vim.uv.fs_stat(args.file)
				if stat and stat.size > opts.max_file_size * 1024 * 1024 then
					vim.api.nvim_buf_call(args.buf, markdown.buf_disable)
				end
			end,
		})
	end,
}
