-- ~/.config/nvim/lua/plugins/highlight_colors.lua
return {
  {
    "brenoprata10/nvim-highlight-colors",
    event = "BufReadPost", -- OPTIMIZATION: Load only when reading a file
    opts = {
      render = "background",
      enable_named_colors = true,
    },
  },
}
