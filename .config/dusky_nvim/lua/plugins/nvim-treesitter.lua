-- Parser installation is explicit; startup never downloads parsers.
return {
	"nvim-treesitter/nvim-treesitter",
	branch = "main",
	lazy = false, -- main requires its runtime files before FileType events.
	build = function()
		assert(require("nvim-treesitter").update(nil, { summary = true }):wait(300000), "Parser update failed")
	end,
}
