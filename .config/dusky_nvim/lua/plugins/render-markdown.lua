return {
	"MeanderingProgrammer/render-markdown.nvim",
	ft = { "markdown" },
	dependencies = {
		"nvim-treesitter/nvim-treesitter",
		"nvim-tree/nvim-web-devicons",
	},
	opts = {
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
}
