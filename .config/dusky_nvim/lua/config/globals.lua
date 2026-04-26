-- lua/config/globals.lua
-- Disable unused providers to speed up startup and clear checkhealth warnings
vim.g.loaded_node_provider = 0
vim.g.loaded_python3_provider = 0
vim.g.loaded_perl_provider = 0
vim.g.loaded_ruby_provider = 0

-- LEADER DEFINED AS SPACE --
vim.g.mapleader = " "
vim.g.maplocalleader = " "
