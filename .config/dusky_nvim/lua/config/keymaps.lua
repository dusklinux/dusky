-- ================================================================================================
-- TITLE: NeoVim keymaps
-- ABOUT: sets some quality-of-life keymaps
-- ================================================================================================

-- Center screen when jumping
vim.keymap.set("n", "n", "nzzzv", { desc = "Next search result (centered)" })
vim.keymap.set("n", "N", "Nzzzv", { desc = "Previous search result (centered)" })
vim.keymap.set("n", "<C-d>", "<C-d>zz", { desc = "Half page down (centered)" })
vim.keymap.set("n", "<C-u>", "<C-u>zz", { desc = "Half page up (centered)" })

-- Spell Check "Wizard" Mode
-- Press <leader>z to jump to the next error and open the suggestion list instantly
vim.keymap.set("n", "<leader>z", "]sz=", { desc = "Next Spell Suggestion" })

-- Clear search highlights and dismiss notifications on Esc
vim.keymap.set("n", "<Esc>", function()
	vim.cmd("nohlsearch")
	pcall(function()
		require("notify").dismiss()
	end)
end, { desc = "Clear search highlight and notifications" })

-- Better window navigation
vim.keymap.set("n", "<C-h>", "<C-w>h", { desc = "Move to left window" })
vim.keymap.set("n", "<C-j>", "<C-w>j", { desc = "Move to bottom window" })
vim.keymap.set("n", "<C-k>", "<C-w>k", { desc = "Move to top window" })
vim.keymap.set("n", "<C-l>", "<C-w>l", { desc = "Move to right window" })

-- Splitting & Resizing
vim.keymap.set("n", "<leader>sv", "<Cmd>vsplit<CR>", { desc = "Split window vertically" })
vim.keymap.set("n", "<leader>sh", "<Cmd>split<CR>", { desc = "Split window horizontally" })
vim.keymap.set("n", "<C-Up>", "<Cmd>resize +2<CR>", { desc = "Increase window height" })
vim.keymap.set("n", "<C-Down>", "<Cmd>resize -2<CR>", { desc = "Decrease window height" })
vim.keymap.set("n", "<C-Left>", "<Cmd>vertical resize -2<CR>", { desc = "Decrease window width" })
vim.keymap.set("n", "<C-Right>", "<Cmd>vertical resize +2<CR>", { desc = "Increase window width" })

-- Better indenting in visual mode
vim.keymap.set("v", "<", "<gv", { desc = "Indent left and reselect" })
vim.keymap.set("v", ">", ">gv", { desc = "Indent right and reselect" })

-- Better J behavior
vim.keymap.set("n", "J", "mzJ`z", { desc = "Join lines and keep cursor position" })

-- Quick config editing
vim.keymap.set("n", "<leader>rc", function()
	vim.cmd.edit(vim.fs.joinpath(vim.fn.stdpath("config"), "init.lua"))
end, { desc = "Edit config" })

-- Buffer Management
vim.keymap.set("n", "<leader>fn", "<Cmd>enew<CR>", { desc = "New Empty Buffer" })
vim.keymap.set("n", "<leader>bd", "<Cmd>bdelete<CR>", { desc = "Delete/Close Buffer" })

-- Custom user command to push current file to dusky bare repo
vim.api.nvim_create_user_command("DuskyPush", function()
	local file = vim.api.nvim_buf_get_name(0)
	if file == "" then
		vim.notify("Error: No file associated with current buffer", vim.log.levels.ERROR)
		return
	end

	-- Resolve relative to HOME
	local home = os.getenv("HOME")
	local home_dir = home:gsub("/+$", "") .. "/"

	-- Check if file is within home
	if file:sub(1, #home_dir) ~= home_dir then
		vim.notify("Error: File is outside home directory / working tree", vim.log.levels.ERROR)
		return
	end

	local relative_file = file:sub(#home_dir + 1)

	-- Check if file is tracked in .git_dusky_list
	local list_file = io.open(home .. "/.git_dusky_list", "r")
	local in_list = false
	if list_file then
		for line in list_file:lines() do
			line = line:match("^%s*(.-)%s*$")
			if line ~= "" and line:sub(1, 1) ~= "#" then
				local clean_line = line:gsub("/+$", "")
				if relative_file == clean_line or relative_file:sub(1, #clean_line + 1) == clean_line .. "/" then
					in_list = true
					break
				end
			end
		end
		list_file:close()
	end

	if not in_list then
		vim.notify("Push aborted: " .. relative_file .. " is not tracked in .git_dusky_list", vim.log.levels.WARN)
		return
	end

	local git = { "git", "--git-dir=" .. home .. "/dusky", "--work-tree=" .. home }
	local pathspec = ":(literal,top)" .. relative_file
	local function command(args)
		return vim.list_extend(vim.deepcopy(git), args)
	end
	local function run(args, callback)
		vim.system(
			command(args),
			{ text = true, cwd = home },
			vim.schedule_wrap(function(result)
				if result.code ~= 0 then
					local detail = (result.stderr or "") .. (result.stdout or "")
					vim.notify(
						detail ~= "" and detail or ("Git failed (exit " .. result.code .. ")"),
						vim.log.levels.ERROR
					)
					return
				end
				callback(result)
			end)
		)
	end

	run({ "status", "--porcelain", "--", pathspec }, function(result)
		if result.stdout == "" then
			vim.notify("No changes detected for " .. relative_file, vim.log.levels.INFO)
			return
		end
		vim.ui.input({ prompt = "Commit message for " .. relative_file .. ": " }, function(msg)
			if not msg or not msg:find("%S") then
				return
			end
			run({ "add", "--", pathspec }, function()
				run({ "commit", "--only", "-m", msg, "--", pathspec }, function()
					vim.cmd.split()
					vim.cmd.enew()
					vim.fn.jobstart(command({ "push" }), { term = true, cwd = home })
					vim.cmd.startinsert()
				end)
			end)
		end)
	end)
end, {})

-- Bind to a keymap: <leader>gp for Git Push
vim.keymap.set("n", "<leader>gp", ":DuskyPush<CR>", { desc = "Push current file to dotfiles repo" })
