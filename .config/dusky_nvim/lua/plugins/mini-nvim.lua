-- ================================================================================================
-- TITLE : mini.nvim
-- LINKS :
--   > github : https://github.com/nvim-mini/mini.nvim
-- ABOUT : Library of 40+ independent Lua modules.
-- ================================================================================================

return {
	{ "nvim-mini/mini.ai", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{ "nvim-mini/mini.comment", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{ "nvim-mini/mini.move", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{
		"nvim-mini/mini.surround",
		event = { "BufReadPost", "BufNewFile" },
		opts = {
			-- Keep Flash's single-key s mapping unambiguous.
			mappings = {
				add = "gsa",
				delete = "gsd",
				find = "gsf",
				find_left = "gsF",
				highlight = "gsh",
				replace = "gsr",
				update_n_lines = "gsn",
			},
		},
	},
	{ "nvim-mini/mini.cursorword", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{ "nvim-mini/mini.indentscope", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{ "nvim-mini/mini.pairs", event = "InsertEnter", opts = {} }, -- Optimized for Insert Mode
	{ "nvim-mini/mini.trailspace", event = { "BufReadPost", "BufNewFile" }, opts = {} },
	{ "nvim-mini/mini.bufremove", event = { "BufReadPost", "BufNewFile" }, opts = {} },
}
