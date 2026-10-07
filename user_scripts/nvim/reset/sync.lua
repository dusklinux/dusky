-- Lazy does not propagate failed plugin tasks to Neovim's exit status.
-- Inspect its task results after the documented blocking sync operation.
local ok, err = pcall(function()
	local lazy = require("lazy")
	lazy.sync({ wait = true, show = false })
	for _, plugin in pairs(lazy.plugins()) do
		for _, task in ipairs(plugin._.tasks or {}) do
			if task:has_errors() then
				error(plugin.name .. ": " .. task:output())
			end
		end
	end
	if vim.g.dusky_nvim then
		lazy.load({ plugins = { "nvim-lspconfig" } })
		local packages = { "lua-language-server", "pyright", "bash-language-server", "prettier" }
		local registry = require("mason-registry")
		local missing_packages = vim.tbl_filter(function(name)
			return not registry.is_installed(name)
		end, packages)
		if #missing_packages > 0 then
			vim.cmd("MasonInstall " .. table.concat(missing_packages, " "))
		end
		for _, name in ipairs(packages) do
			assert(registry.is_installed(name), "Mason installation failed: " .. name)
		end
		local treesitter = require("nvim-treesitter")
		local parsers = {
			"bash",
			"python",
			"lua",
			"vim",
			"vimdoc",
			"query",
			"c",
			"markdown",
			"markdown_inline",
			"json",
			"yaml",
			"html",
			"css",
			"javascript",
			"typescript",
			"tsx",
			"regex",
		}
		local installed = treesitter.get_installed("parsers")
		local missing_parsers = vim.tbl_filter(function(name)
			return not vim.list_contains(installed, name)
		end, parsers)
		if #missing_parsers > 0 then
			assert(treesitter.install(missing_parsers, { force = true }):wait(300000), "Parser installation failed")
		end
		assert(treesitter.update(nil, { summary = true }):wait(300000), "Parser update failed")
		installed = treesitter.get_installed("parsers")
		for _, name in ipairs(parsers) do
			assert(vim.list_contains(installed, name), "Parser installation failed: " .. name)
		end
	end

	if vim.v.errmsg ~= "" then
		error(vim.v.errmsg)
	end
end)
if not ok then
	vim.api.nvim_err_writeln(tostring(err))
	vim.cmd("cquit 1")
else
	vim.cmd("qall!")
end
