-- lua/plugins/nvim-cmp.lua
-- Adds nvim-cmp + cmp-path + cmp-cmdline, modeled like NVChad's approach

return {
  {
    "hrsh7th/nvim-cmp",
    event = { "InsertEnter", "CmdlineEnter" },
    dependencies = {
      "hrsh7th/cmp-buffer",
      "hrsh7th/cmp-path",       -- path completions (files/dirs)
      "hrsh7th/cmp-cmdline",    -- optional: completion in : and / cmdline
      "L3MON4D3/LuaSnip",       -- snippet engine (optional)
      "saadparwaiz1/cmp_luasnip",
    },
    config = function()
      local ok, cmp = pcall(require, "cmp")
      if not ok then return end

      local luasnip_ok, luasnip = pcall(require, "luasnip")
      if not luasnip_ok then luasnip = nil end

      vim.o.completeopt = "menuone,noselect"

      cmp.setup({
        snippet = {
          expand = function(args)
            if luasnip then luasnip.lsp_expand(args.body) end
          end,
        },

        mapping = cmp.mapping.preset.insert({
          -- Manual open (NvChad commonly exposes <C-Space> to open completions)
          -- note: <C-Space> can be flaky in some terminals; keep a fallback below
          ["<C-Space>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),
          -- Fallback manual trigger mapping that works everywhere: <C-x><C-o>
          ["<C-x><C-o>"] = cmp.mapping(cmp.mapping.complete(), { "i", "c" }),

          ["<CR>"] = cmp.mapping.confirm({ select = true }),
          ["<Tab>"] = cmp.mapping(function(fallback)
            if cmp.visible() then
              cmp.select_next_item()
            elseif luasnip and luasnip.expand_or_jumpable() then
              luasnip.expand_or_jump()
            else
              fallback()
            end
          end, { "i", "s" }),
          ["<S-Tab>"] = cmp.mapping(function(fallback)
            if cmp.visible() then
              cmp.select_prev_item()
            elseif luasnip and luasnip.jumpable(-1) then
              luasnip.jump(-1)
            else
              fallback()
            end
          end, { "i", "s" }),
        }),

        sources = cmp.config.sources({
          { name = "path", option = { trailing_slash = true } }, -- important: path source first
          { name = "buffer" },
          { name = "luasnip" },
        }),

        formatting = {
          fields = { "kind", "abbr", "menu" },
        },
      })

      -- Commandline setup: use path + cmdline source for ':' (optional, like NvChad users do)
      cmp.setup.cmdline(":", {
        mapping = cmp.mapping.preset.cmdline(),
        sources = cmp.config.sources({
          { name = "path" },     -- get file path suggestions in : (e.g., :e /usr/...)
        }, {
          { name = "cmdline" },  -- fallback to command names
        })
      })

      -- Search (/) completion uses buffer
      cmp.setup.cmdline("/", {
        mapping = cmp.mapping.preset.cmdline(),
        sources = {
          { name = "buffer" }
        }
      })
    end,
  },
}
