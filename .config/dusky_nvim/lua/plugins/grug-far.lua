-- lua/plugins/grug-far.lua
return {
	"MagicDuck/grug-far.nvim",
	cmd = { "GrugFar" },
	keys = {
		{
			"<leader>sr",
			function()
				local current_file = vim.api.nvim_buf_get_name(0)
				if current_file == "" then
					vim.notify("Current buffer has no file name", vim.log.levels.WARN)
					return
				end
				require("grug-far").open({ prefills = { paths = current_file:gsub(" ", "\\ ") } })
			end,
			desc = "Search and Replace (current file)",
		},
		{
			"<leader>sg",
			function()
				require("grug-far").open()
			end,
			desc = "Search and Replace (workspace)",
		},
	},
	opts = {
		headerInfoMuted = true,
	},
}
