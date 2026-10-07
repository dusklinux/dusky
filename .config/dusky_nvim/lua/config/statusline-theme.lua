return function()
	return {
		normal = {
			a = { fg = vim.g.base16_gui01 or "#130d08", bg = vim.g.base16_gui0D or "#ffdcc1", gui = "bold" },
			b = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
			c = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
		},
		insert = {
			a = { fg = vim.g.base16_gui01, bg = vim.g.base16_gui0B, gui = "bold" },
			b = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
			c = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
		},
		visual = {
			a = { fg = vim.g.base16_gui01, bg = vim.g.base16_gui09, gui = "bold" },
			b = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
			c = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
		},
		replace = {
			a = { fg = vim.g.base16_gui01, bg = vim.g.base16_gui08, gui = "bold" },
			b = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
			c = { fg = vim.g.base16_gui05, bg = vim.g.base16_gui02 },
		},
		inactive = {
			a = { fg = vim.g.base16_gui03 or "#9e8e82", bg = vim.g.base16_gui01 },
			b = { fg = vim.g.base16_gui03 or "#9e8e82", bg = vim.g.base16_gui01 },
			c = { fg = vim.g.base16_gui03 or "#9e8e82", bg = vim.g.base16_gui01 },
		},
	}
end
