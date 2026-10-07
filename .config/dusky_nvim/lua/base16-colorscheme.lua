local M = {}

function M.setup(colors)
	-- Validate and apply the palette before exporting it to UI components.
	require("mini.base16").setup({ palette = colors })
	for name, value in pairs(colors) do
		vim.g[name] = value
		vim.g["base16_gui" .. name:sub(5)] = value
	end
end

return M
