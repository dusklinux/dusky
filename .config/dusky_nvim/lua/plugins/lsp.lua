-- Native LSP configuration; Mason handles optional server provisioning.
return {
	{
		"mason-org/mason.nvim",
		cmd = { "Mason", "MasonInstall", "MasonUninstall", "MasonUpdate", "MasonLog" },
		opts = {},
	},
	{
		"neovim/nvim-lspconfig",
		event = { "BufReadPre", "BufNewFile" },
		dependencies = {
			"mason-org/mason.nvim",
			"mason-org/mason-lspconfig.nvim",
			"hrsh7th/cmp-nvim-lsp",
		},
		config = function()
			-- Configure native diagnostic text, signs and floating windows.
			vim.diagnostic.config({
				virtual_text = { spacing = 2, prefix = "●" },
				signs = {
					text = {
						[vim.diagnostic.severity.ERROR] = "",
						[vim.diagnostic.severity.WARN] = "",
						[vim.diagnostic.severity.INFO] = "",
						[vim.diagnostic.severity.HINT] = "",
					},
				},
				underline = true,
				update_in_insert = false,
				severity_sort = true,
				float = { border = "rounded", source = "if_many" },
			})

			-- Buffer-local mappings for attached language servers.
			vim.api.nvim_create_autocmd("LspAttach", {
				group = vim.api.nvim_create_augroup("UserLspConfig", {}),
				callback = function(ev)
					local function map(mode, lhs, rhs, desc)
						vim.keymap.set(mode, lhs, rhs, { buf = ev.buf, desc = desc })
					end
					map("n", "gD", vim.lsp.buf.declaration, "Go to declaration")
					map("n", "gd", vim.lsp.buf.definition, "Go to definition")
					map("n", "K", vim.lsp.buf.hover, "Hover docs")
					map("n", "gi", vim.lsp.buf.implementation, "Go to implementation")
					map("n", "<leader>ck", vim.lsp.buf.signature_help, "Signature help")
					map("n", "<leader>wa", vim.lsp.buf.add_workspace_folder, "Add workspace folder")
					map("n", "<leader>wr", vim.lsp.buf.remove_workspace_folder, "Remove workspace folder")
					map("n", "<leader>wl", function()
						print(vim.inspect(vim.lsp.buf.list_workspace_folders()))
					end, "List workspace folders")
					map("n", "<leader>D", vim.lsp.buf.type_definition, "Type definition")
					map("n", "<leader>rn", vim.lsp.buf.rename, "Rename symbol")
					map({ "n", "x" }, "<leader>ca", vim.lsp.buf.code_action, "Code actions")
					map("n", "grr", vim.lsp.buf.references, "Show references")
				end,
			})

			-- Advertise completion support for current and future server configurations.
			vim.lsp.config("*", { capabilities = require("cmp_nvim_lsp").default_capabilities() })

			-- Define configurations for preferred language servers
			local servers = {
				-- Lua LSP config
				lua_ls = {
					settings = {
						Lua = {
							diagnostics = {
								globals = { "vim" },
							},
							workspace = {
								checkThirdParty = false,
							},
							telemetry = { enable = false },
							hint = { enable = true },
							completion = { callSnippet = "Replace" },
						},
					},
				},
				-- Python LSP config
				pyright = {
					settings = {
						pyright = { disableOrganizeImports = false },
						python = { analysis = { typeCheckingMode = "basic", autoSearchPaths = true } },
					},
				},
				-- Bash LSP config
				bashls = {},
			}

			-- Define configs before Mason enables installed servers.
			for name, config in pairs(servers) do
				config.root_dir = function(buf, on_dir)
					if vim.b[buf].dusky_bigfile then
						return
					end
					local root
					for _, marker in ipairs(vim.lsp.config[name].root_markers or {}) do
						root = vim.fs.root(buf, marker)
						if root then
							break
						end
					end
					on_dir(root or vim.fs.dirname(vim.api.nvim_buf_get_name(buf)) or vim.uv.cwd())
				end
				vim.lsp.config(name, config)
			end
			require("mason-lspconfig").setup({
				ensure_installed = {}, -- The deployment sync installs tools explicitly.
				automatic_enable = { "lua_ls", "pyright", "bashls" },
			})
		end,
	},
}
