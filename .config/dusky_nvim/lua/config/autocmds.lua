local group = vim.api.nvim_create_augroup("DuskyEditing", { clear = true })
local saved_spell = {}

local function configure_window(buf, win)
	local opts = vim.wo[win][0]
	local saved = saved_spell[buf]
	if vim.b[buf].dusky_bigfile then
		saved = saved or {}
		saved_spell[buf] = saved
		if saved[win] == nil then
			saved[win] = opts.spell
		end
		opts.spell = false
	else
		if saved and saved[win] ~= nil then
			opts.spell = saved[win]
			saved[win] = nil
			if next(saved) == nil then
				saved_spell[buf] = nil
			end
		end
	end
	if not opts.diff then
		opts.foldmethod = vim.b[buf].dusky_bigfile and "manual"
			or (vim.treesitter.highlighter.active[buf] and "expr" or "manual")
	end
end

-- Drop saved preferences when their buffer or window is destroyed.
vim.api.nvim_create_autocmd("BufWipeout", {
	group = group,
	callback = function(args)
		saved_spell[args.buf] = nil
	end,
})
vim.api.nvim_create_autocmd("WinClosed", {
	group = group,
	callback = function(args)
		for buf, saved in pairs(saved_spell) do
			saved[tonumber(args.match)] = nil
			if next(saved) == nil then
				saved_spell[buf] = nil
			end
		end
	end,
})

-- Decide before FileType/plugin callbacks can start expensive buffer work.
-- Override the byte threshold in init.lua with vim.g.dusky_bigfile_size.
vim.api.nvim_create_autocmd("BufReadPre", {
	group = group,
	callback = function(args)
		local stat = vim.uv.fs_stat(args.file)
		vim.b[args.buf].dusky_bigfile = stat ~= nil and stat.size > (vim.g.dusky_bigfile_size or 1024 * 1024)
		for _, module in ipairs({ "ai", "cursorword", "indentscope", "trailspace" }) do
			vim.b[args.buf]["mini" .. module .. "_disable"] = vim.b[args.buf].dusky_bigfile
		end
	end,
})

-- Parsers and queries are provisioned during explicit deployment sync.
vim.api.nvim_create_autocmd("FileType", {
	group = group,
	callback = function(args)
		if vim.b[args.buf].dusky_bigfile then
			vim.treesitter.stop(args.buf) -- Native ftplugins may already have started it.
			vim.bo[args.buf].indentexpr = ""
			vim.bo[args.buf].syntax = "OFF"
		else
			local lang = vim.treesitter.language.get_lang(args.match)
			if not (lang and pcall(vim.treesitter.start, args.buf, lang)) then
				vim.treesitter.stop(args.buf)
			end
		end
		for _, win in ipairs(vim.fn.win_findbuf(args.buf)) do
			configure_window(args.buf, win)
		end
	end,
})

-- BufWinEnter runs after filetype detection, including for hidden buffers.
vim.api.nvim_create_autocmd("BufWinEnter", {
	group = group,
	callback = function(args)
		if vim.bo[args.buf].buftype ~= "" then
			return
		end
		if vim.b[args.buf].dusky_bigfile then
			vim.bo[args.buf].syntax = "OFF"
		end
		configure_window(args.buf, vim.api.nvim_get_current_win())
		if vim.b[args.buf].dusky_last_position then
			return
		end
		vim.b[args.buf].dusky_last_position = true
		if vim.list_contains({ "gitcommit", "gitrebase" }, vim.bo[args.buf].filetype) then
			return
		end
		local mark = vim.api.nvim_buf_get_mark(args.buf, '"')
		if mark[1] > 0 and mark[1] <= vim.api.nvim_buf_line_count(args.buf) then
			pcall(vim.api.nvim_win_set_cursor, 0, mark)
		end
	end,
})

vim.api.nvim_create_autocmd("TextYankPost", {
	group = group,
	callback = function()
		vim.hl.on_yank({ higroup = "IncSearch", timeout = 200 })
	end,
})

-- Autoread also needs a check when focus returns or a terminal command exits.
vim.api.nvim_create_autocmd({ "FocusGained", "TermClose", "TermLeave" }, {
	group = group,
	callback = function()
		if vim.fn.getcmdwintype() == "" then
			vim.cmd.checktime()
		end
	end,
})
