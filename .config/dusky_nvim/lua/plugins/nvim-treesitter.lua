-- ================================================================================================
-- TITLE : nvim-treesitter
-- ABOUT : Treesitter configurations and abstraction layer for Neovim.
-- LINKS :
--   > github : https://github.com/nvim-treesitter/nvim-treesitter
-- ================================================================================================

return {
  "nvim-treesitter/nvim-treesitter",
  version = false, -- Always use the latest commit (master), not tags
  branch = "master", -- Explicitly track master to avoid detached HEAD states
  build = ":TSUpdate",
  event = { "BufReadPost", "BufNewFile" },
  lazy = vim.fn.argc(-1) == 0, -- Load immediately if opening a file from CLI, otherwise lazy load
  
  -- CRITICAL FIX: Tell lazy which module allows this plugin to be loaded
  main = "nvim-treesitter.configs", 
  
  -- Define options here instead of inside config()
  opts = {
    ensure_installed = {
      "bash",
      "c",
      "cpp",
      "css",
      "dockerfile",
      "go",
      "html",
      "javascript",
      "json",
      "lua",
      "markdown",
      "markdown_inline",
      "python",
      "query",
      "regex",
      "rust",
      "svelte",
      "typescript",
      "vim",
      "vimdoc",
      "vue",
      "yaml",
    },
    auto_install = true,
    sync_install = false, -- Optimization: Don't block UI on install
    highlight = {
      enable = true,
      additional_vim_regex_highlighting = false,
    },
    indent = { enable = true },
    incremental_selection = {
      enable = true,
      keymaps = {
        init_selection = "<CR>",
        node_incremental = "<CR>",
        scope_incremental = "<TAB>",
        node_decremental = "<S-TAB>",
      },
    },
  },

  -- Use the opts we defined above
  config = function(_, opts)
    require("nvim-treesitter.configs").setup(opts)
  end,
}
