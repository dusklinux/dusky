-- lua/plugins/nvim-cmp.lua
-- Adds nvim-cmp + cmp-path + cmp-cmdline, modeled like NVChad's approach

return {
	{
		"hrsh7th/nvim-cmp",
		event = { "InsertEnter", "CmdlineEnter" },
		dependencies = {
			"hrsh7th/cmp-buffer",
			"hrsh7th/cmp-path", -- path completions (files/dirs)
			"hrsh7th/cmp-cmdline", -- optional: completion in : and / cmdline
			"hrsh7th/cmp-nvim-lsp", -- LSP completion capabilities
			{
				"L3MON4D3/LuaSnip",
				build = "make install_jsregexp",
			}, -- snippet engine
			"saadparwaiz1/cmp_luasnip",
		},
		config = function()
			local cmp = require("cmp")
			local luasnip = require("luasnip")

			-- FIX: don't clobber global completeopt set in options.lua (menu,menuone,noinsert,noselect,popup,fuzzy); nvim-cmp manages its own
			-- vim.o.completeopt = "menuone,noselect"

			cmp.setup({
				snippet = {
					expand = function(args)
						luasnip.lsp_expand(args.body)
					end,
				},

				mapping = cmp.mapping.preset.insert({
					-- Manual open (NvChad commonly exposes <C-Space> to open completions)
					-- note: <C-Space> can be flaky in some terminals; keep a fallback below
					["<C-Space>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),
					-- Fallback manual trigger mapping that works everywhere: <C-x><C-o>
					["<C-x><C-o>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),

					["<CR>"] = cmp.mapping.confirm({ select = true }),
					["<Tab>"] = cmp.mapping(function(fallback)
						if cmp.visible() then
							cmp.select_next_item()
						elseif luasnip.expand_or_locally_jumpable() then
							luasnip.expand_or_jump()
						else
							fallback()
						end
					end, { "i", "s" }),
					["<S-Tab>"] = cmp.mapping(function(fallback)
						if cmp.visible() then
							cmp.select_prev_item()
						elseif luasnip.locally_jumpable(-1) then
							luasnip.jump(-1)
						else
							fallback()
						end
					end, { "i", "s" }),
				}),

				sources = cmp.config.sources({
					{ name = "nvim_lsp", priority = 1000 }, -- Prefer language-server completions
					{ name = "luasnip", priority = 750 },
					{ name = "path", option = { trailing_slash = true }, priority = 500 },
					{ name = "buffer", keyword_length = 3, priority = 250 },
				}),

				formatting = {
					fields = { "kind", "abbr", "menu" },
				},
			})

			-- Commandline setup: use path + cmdline source for ':' (optional, like NvChad users do)
			cmp.setup.cmdline(":", {
				mapping = cmp.mapping.preset.cmdline(),
				sources = cmp.config.sources({
					{ name = "path" }, -- get file path suggestions in : (e.g., :e /usr/...)
				}, {
					{ name = "cmdline" }, -- fallback to command names
				}),
			})

			-- Forward and backward search completion uses the current buffer
			cmp.setup.cmdline({ "/", "?" }, {
				mapping = cmp.mapping.preset.cmdline(),
				sources = {
					{ name = "buffer" },
				},
			})
		end,
	},
}
