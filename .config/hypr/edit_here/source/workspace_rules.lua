-- =============================================================================
-- USER CONFIGURATION: workspace_rules.lua
-- =============================================================================
-- Add your custom workspace rules here.
-- These will override or add to the defaults found in:
--   ~/.config/hypr/source/workspace_rules.lua
--
-- Managed by Dusky TUI - Granular Matrix v4.4.1
-- =============================================================================

-- --- Global Rules ---
local global_layout = "dwindle"
hl.workspace_rule({ workspace = "r[11-99]", layout = global_layout })

-- --- Ephemeral Global Override (Resets on reboot) ---
-- local ephemeral_layout  = "monocle"
-- local ephemeral_enabled = false

-- --- Individual Workspaces (1-10) ---
local ws = {
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
    { layout = "dwindle", persistent = false, master_orient = "left", scroll_dir = "right" },
}

for i, w in ipairs(ws) do
    hl.workspace_rule({
        workspace  = tostring(i),
        layout     = w.layout,
        persistent = w.persistent,
        layout_opts = { orientation = w.master_orient, direction = w.scroll_dir },
    })
end
