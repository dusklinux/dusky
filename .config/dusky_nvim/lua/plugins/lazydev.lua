-- Load Neovim/plugin Lua types only when their modules are referenced.
return {
	"folke/lazydev.nvim",
	ft = "lua",
	cmd = "LazyDev",
	opts = {
		integrations = { lspconfig = false }, -- Preserve Dusky root selection and large-file exclusion.
		library = {
			{ path = "${3rd}/luv/library", words = { "vim%.uv" } },
		},
	},
}
