local M = { revision = 0 }

function M.setup(colors)
	-- Validate and apply the palette before exporting it to UI components.
	require("mini.base16").setup({ palette = colors })
	for name, value in pairs(colors) do
		vim.g[name] = value
		vim.g["base16_gui" .. name:sub(5)] = value
	end
	M.revision = M.revision + 1
end

return M
