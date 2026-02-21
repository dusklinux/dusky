-- lua/base16-colorscheme.lua
local M = {}

function M.setup(colors)
  -- 1. Set Global Variables (The Missing Link)
  -- We loop through the colors (base00, base01...) and set them as globals
  -- in the format 'vim.g.base16_gui00' so Lualine/Noice can find them.
  for name, value in pairs(colors) do
    -- Export 'base00' as 'vim.g.base00'
    vim.g[name] = value
    
    -- Export 'base00' as 'vim.g.base16_gui00' (This is what Lualine expects)
    local hex_code = name:gsub("base", "") -- extracts "00" from "base00"
    vim.g["base16_gui" .. hex_code] = value
  end

  -- 2. Setup the Theme via Mini.base16
  -- We pass the palette to mini.base16 to generate the actual highlights
  require("mini.base16").setup({
    palette = colors
  })
end

return M
