-- ================================================================================================
-- TITLE : mini.nvim
-- LINKS :
--   > github : https://github.com/echasnovski/mini.nvim
-- ABOUT : Library of 40+ independent Lua modules.
-- ================================================================================================

return {
	{ "echasnovski/mini.ai", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.comment", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.move", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.surround", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.cursorword", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.indentscope", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.pairs", event = "InsertEnter", version = "*", opts = {} }, -- Optimized for Insert Mode
	{ "echasnovski/mini.trailspace", event = "BufReadPost", version = "*", opts = {} },
	{ "echasnovski/mini.bufremove", event = "BufReadPost", version = "*", opts = {} },
}
