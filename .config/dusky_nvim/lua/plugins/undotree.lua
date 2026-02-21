return {
  "mbbill/undotree",
  cmd = "UndotreeToggle", -- Lazy load on command
  keys = {
    { "<leader>u", vim.cmd.UndotreeToggle, desc = "Toggle Undotree" },
  },
  config = function()
    vim.g.undotree_SetFocusWhenToggle = 1
  end,
}
