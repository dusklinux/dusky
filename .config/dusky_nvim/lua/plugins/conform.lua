-- lua/plugins/conform.lua
return {
  "stevearc/conform.nvim",
  event = { "BufReadPost", "BufNewFile" },
  cmd = { "ConformInfo" },
  keys = {
    {
      -- The "Trigger" keybind
      "<leader>cf",
      function()
        require("conform").format({ async = true, lsp_fallback = true })
      end,
      mode = { "n", "v" }, -- Works in Normal and Visual mode
      desc = "Code Format",
    },
  },
  opts = {
    -- Define which tools to use for which filetype
    formatters_by_ft = {
      lua = { "stylua" },
      bash = { "shfmt" },
      sh = { "shfmt" },
      zsh = { "shfmt" },
      
      -- Web / Config standards
      javascript = { "prettier" },
      typescript = { "prettier" },
      css = { "prettier" },
      html = { "prettier" },
      json = { "prettier" },
      yaml = { "prettier" },
      markdown = { "prettier" },
    },
    
    -- Explicitly disable auto-formatting
    format_on_save = false,
  },
}
