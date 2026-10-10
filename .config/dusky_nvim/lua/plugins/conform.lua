-- lua/plugins/conform.lua
return {
	"stevearc/conform.nvim",
	cmd = { "ConformInfo" },
	keys = {
		{
			-- The "Trigger" keybind
			"<leader>cf",
			function()
				require("conform").format({ async = true, lsp_format = "fallback" })
			end,
			mode = { "n", "x" }, -- Works in Normal and Visual mode
			desc = "Code Format",
		},
	},
	opts = {
		-- Define which tools to use for which filetype
		formatters_by_ft = {
			lua = { "stylua" },
			bash = { "shfmt" },
			sh = { "shfmt" },

			-- Web / Config standards
			javascript = { "prettier" },
			typescript = { "prettier" },
			javascriptreact = { "prettier" },
			typescriptreact = { "prettier" },
			css = { "prettier" },
			html = { "prettier" },
			json = { "prettier" },
			jsonc = { "prettier" },
			yaml = { "prettier" },
			markdown = { "prettier" },
		},

		-- No format_on_save callback: formatting is manual.
	},
}
