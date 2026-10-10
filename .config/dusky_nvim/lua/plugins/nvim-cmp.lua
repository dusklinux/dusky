-- lua/plugins/nvim-cmp.lua
-- Completion with Neovim native LSP snippets and command-line sources.

return {
	{
		"hrsh7th/nvim-cmp",
		event = { "InsertEnter", "CmdlineEnter" },
		dependencies = {
			"hrsh7th/cmp-buffer",
			"hrsh7th/cmp-path", -- path completions (files/dirs)
			"hrsh7th/cmp-cmdline", -- optional: completion in : and / cmdline
			"hrsh7th/cmp-nvim-lsp", -- LSP completion capabilities
		},
		config = function()
			local cmp = require("cmp")

			cmp.setup({
				enabled = function()
					return vim.bo.buftype ~= "prompt" and not vim.b.dusky_bigfile
				end,
				snippet = {
					expand = function(args)
						vim.snippet.expand(args.body)
					end,
				},

				mapping = cmp.mapping.preset.insert({
					-- Manual completion; the second binding also works in terminals without Ctrl-Space.
					["<C-Space>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),
					-- Fallback manual trigger mapping that works everywhere: <C-x><C-o>
					["<C-x><C-o>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),

					["<CR>"] = cmp.mapping.confirm({ select = true }),
					["<Tab>"] = cmp.mapping(function(fallback)
						if cmp.visible() then
							cmp.select_next_item()
						elseif vim.snippet.active({ direction = 1 }) then
							vim.snippet.jump(1)
						else
							fallback()
						end
					end, { "i", "s" }),
					["<S-Tab>"] = cmp.mapping(function(fallback)
						if cmp.visible() then
							cmp.select_prev_item()
						elseif vim.snippet.active({ direction = -1 }) then
							vim.snippet.jump(-1)
						else
							fallback()
						end
					end, { "i", "s" }),
				}),

				sources = cmp.config.sources({
					{ name = "lazydev", priority = 1100 },
					{ name = "nvim_lsp", priority = 1000 }, -- Prefer language-server completions
					{ name = "path", option = { trailing_slash = true }, priority = 500 },
					{
						name = "buffer",
						keyword_length = 3,
						priority = 250,
						option = {
							get_bufnrs = function()
								return vim.b.dusky_bigfile and {} or { vim.api.nvim_get_current_buf() }
							end,
						},
					},
				}),

				formatting = {
					fields = { "kind", "abbr", "menu" },
				},
			})

			-- File paths and Ex commands in the command line.
			cmp.setup.cmdline(":", {
				enabled = true, -- Ex/path completion remains useful in large files.
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
