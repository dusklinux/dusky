-- lua/plugins/theme.lua
return {
	{
		"nvim-mini/mini.base16",
		lazy = false, -- Apply the palette during startup
		priority = 1000, -- UI components depend on palette globals
		config = function()
			-- Define the path to your Matugen output
			local config_home = vim.env.XDG_CONFIG_HOME
			if not config_home or config_home == "" then
				config_home = vim.fs.joinpath(vim.env.HOME, ".config")
			end
			local matugen_path = vim.fs.joinpath(config_home, "matugen/generated/neovim-colors.lua")

			-- Use a complete palette when Matugen has not run yet.
			-- This ensures vim.g.base16_guiXX globals exist for Lualine/Noice.
			local default_colors = {
				base00 = "#1e1e2e",
				base01 = "#181825",
				base02 = "#313244",
				base03 = "#45475a",
				base04 = "#585b70",
				base05 = "#cdd6f4",
				base06 = "#f5e0dc",
				base07 = "#b4befe",
				base08 = "#f38ba8",
				base09 = "#fab387",
				base0A = "#f9e2af",
				base0B = "#a6e3a1",
				base0C = "#94e2d5",
				base0D = "#89b4fa",
				base0E = "#cba6f7",
				base0F = "#f2cdcd",
			}

			-- Function to safely source the theme
			local function load_theme()
				if vim.uv.fs_stat(matugen_path) then
					local base16 = require("base16-colorscheme")
					local revision = base16.revision
					local ok, err = pcall(dofile, matugen_path)
					if ok and base16.revision > revision then
						return
					end
					if not ok then
						vim.notify("Matugen Load Error: " .. tostring(err), vim.log.levels.ERROR)
					end
				end
				require("base16-colorscheme").setup(default_colors)
			end

			-- 1. Load the theme
			load_theme()

			-- 2. Apply tweaks that must happen AFTER the theme loads
			local function apply_tweaks()
				vim.api.nvim_set_hl(0, "Comment", { italic = true, update = true })

				-- Match the transparent file explorer to the terminal.
				vim.api.nvim_set_hl(0, "NvimTreeNormal", { fg = vim.g.base16_gui05, bg = "NONE" })
				-- Also ensure NvimTree window separator contrasts
				vim.api.nvim_set_hl(0, "NvimTreeWinSeparator", { fg = vim.g.base16_gui03 or "#9e8e82", bg = "NONE" })

				-- Reapply sign links after mini.base16 rebuilds highlights.
				for group, link in pairs({
					GitSignsAdd = "DiagnosticOk",
					GitSignsUntracked = "DiagnosticOk",
					GitSignsChange = "DiagnosticWarn",
					GitSignsChangeDelete = "DiagnosticWarn",
					GitSignsDelete = "DiagnosticError",
					GitSignsTopDelete = "DiagnosticError",
				}) do
					vim.api.nvim_set_hl(0, group, { link = link })
				end
			end

			apply_tweaks()

			-- 3. Live Reloading (NATIVE SIGNAL LISTENER)
			vim.api.nvim_create_autocmd("Signal", {
				group = vim.api.nvim_create_augroup("DuskyTheme", { clear = true }),
				pattern = "SIGUSR1",
				callback = function()
					vim.schedule(function()
						load_theme()
						apply_tweaks()

						-- Refresh lualine if it's loaded to pick up new globals
						if package.loaded["lualine"] then
							require("lualine").setup({ options = { theme = require("config.statusline-theme")() } })
							require("lualine").refresh()
						end

						vim.notify("DuskyNVIM theme reloaded", vim.log.levels.INFO)
					end)
				end,
			})
		end,
	},
}
