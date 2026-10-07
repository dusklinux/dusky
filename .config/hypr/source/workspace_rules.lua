-- ==============================================================================
-- USER CONFIGURATION: workspace_rules.lua
-- ==============================================================================
--
-- HOW THIS FILE IS ORGANIZED:
--
--   §1  CORE WORKSPACES (1-10) ..........  Directly managed by the Python AST Engine
--   §2  (MERGED INTO §1) ................  Old loop generator replaced by direct AST
--   §3  SMART GAPS  .....................  No gaps / borders when only one window
--   §4  SPECIAL WORKSPACES / SCRATCHPADS.  Floating overlay workspaces
--   §5  NAMED / PROJECT WORKSPACES  .....  Semantic per-project environments
--   §6  MONITOR BINDING & PERSISTENCE  ..  Assign workspaces to specific outputs
--   §7  PER-WORKSPACE AESTHETICS  .......  Borders, rounding, gaps, animations
--   §8  PER-WORKSPACE LAYOUT OVERRIDES  .  dwindle / master / scrolling / custom
--   §9  RANGE RULES (Global Fallbacks)  .  Catch-all rules for workspace ranges
--   §10 GLOBAL WORKSPACE BEHAVIOUR  .....  hl.config() options that affect all ws
--
-- WORKSPACE IDENTIFIER QUICK REFERENCE:
--   "1" .. "N"          →  numbered workspace
--   "name:foo"          →  named workspace
--   "special:foo"       →  special (scratchpad) workspace
--   "r[X-Y]"            →  range selector  (e.g. "r[1-5]")
--   "w[tv1]"            →  selector: exactly 1 visible Tiled window
--   "w[tv2]"            →  selector: exactly 2 visible Tiled windows  (etc.)
--   "f[-1]"             →  selector: workspace has NO fullscreen window
--   "f[0]"              →  selector: workspace has a fullscreen window
--   "f[1]"              →  selector: workspace has a maximized window
--   "s[false]"          →  selector: exclude special workspaces
--   "s[true]"           →  selector: special workspaces only
-- ==============================================================================


-- ==============================================================================
-- §1 & §2  CORE WORKSPACES (1-10) - DYNAMIC GENERATOR
-- The Python TUI AST Engine directly hooks into these specific statements.
-- By explicitly defining them, you get absolute per-workspace granularity in 
-- the UI without needing any complex Lua loops or Bash arrays!
--
-- Full list of supported keys (all optional except `workspace`):
--   workspace       (string)  -- REQUIRED. Identifier or selector (see above).
--   monitor         (string)  -- Bind to monitor by name ("DP-1") or desc.
--   default         (bool)    -- Make this the default workspace on its monitor.
--   persistent      (bool)    -- Keep workspace alive even when empty.
--   default_name    (string)  -- Human-readable display name (shown in bars, etc.).
--   on_created_empty(string)  -- Shell command to run when workspace is first created.
--   layout          (string)  -- Override layout: "dwindle" | "master" | "scrolling" | "monocle"
--   layout_opts     (table)   -- Layout-specific options (see §8 for details).
--   gaps_in         (number)  -- Inner gap override (px).
--   gaps_out        (number)  -- Outer gap override (px).
--   float_gaps      (number)  -- Gaps for floating windows (px).
--   no_border       (bool)    -- Disable all window borders on this workspace.
--   border_size     (number)  -- Override border thickness (px).
--   no_rounding     (bool)    -- Disable corner rounding on this workspace.
--   decorate        (bool)    -- Enable/disable decorations (shadows, etc.).
--   no_shadow       (bool)    -- Disable window shadows on this workspace.
--   animation       (string)  -- Override workspace switch animation style.
-- ==============================================================================
hl.workspace_rule({ workspace = "1", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "2", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "3", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "4", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "5", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "6", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "7", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "8", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "9", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })
hl.workspace_rule({ workspace = "10", layout = nil, persistent = false, monitor = "", default = false, default_name = "", on_created_empty = "" })


-- ==============================================================================
-- §3  SMART GAPS ("no gaps when only")
-- Removes gaps and borders when exactly one tiled window is on screen,
-- or when a window is in fullscreen/maximized state.
--
-- NOTE: The "Smart Gaps" feature has been moved to appearance.lua.
-- The Python TUI automatically reroutes the toggle there using `target_file_override`.
-- ==============================================================================


-- ==============================================================================
-- §4  SPECIAL WORKSPACES / SCRATCHPADS
-- Special workspaces float over any monitor and can be toggled on/off.
-- They are identified by the "special:" prefix.
-- Toggle them with: hl.dispatch(hl.dsp.workspace.toggle_special("scratchpad"))
--
-- Notes:
--   • Only one special workspace can be visible on a monitor at a time.
--   • `misc.close_special_on_empty` (§10e) controls auto-close behaviour.
--   • `on_created_empty` launches an app the first time the workspace is shown.
--   • Use gaps_out in hl.workspace_rule() for scratchpad margins.
--     The magic scratchpad geometry is managed in appearance.lua (Appearance TUI → Special).
-- ==============================================================================
local special_workspaces = {
    -- { name = "scratchpad", on_created_empty = "kitty" },
    -- { name = "browser",    on_created_empty = "firefox",   layout = "scrolling" },
    -- { name = "music",      on_created_empty = "spotify" },
    -- { name = "notes",      on_created_empty = "obsidian" },
}

for _, ws in ipairs(special_workspaces) do
    hl.workspace_rule({
        workspace        = "special:" .. ws.name,
        on_created_empty = ws.on_created_empty,
        layout           = ws.layout,  -- nil is safe; Hyprland ignores nil fields
    })
end


-- ==============================================================================
-- §5  NAMED / PROJECT WORKSPACES
-- Use "name:foo" identifiers for semantic, project-specific workspaces.
-- These can coexist alongside numbered workspaces.
-- Navigate with: hl.dispatch(hl.dsp.focus({ workspace = "name:coding" }))
-- ==============================================================================
local named_workspaces = {
    -- { name = "coding",  monitor = "DP-1",  on_created_empty = "kitty",
    --   gaps_in = 0, gaps_out = 0, no_border = true, no_rounding = true, decorate = false },
    -- { name = "browser", monitor = "DP-2",  on_created_empty = "firefox" },
    -- { name = "gaming",  monitor = "desc:Chimei Innolux Corporation 0x150C",
    --   no_border = true, no_rounding = true, decorate = false,
    --   layout = "scrolling" },
}

for _, ws in ipairs(named_workspaces) do
    hl.workspace_rule({
        workspace        = "name:" .. ws.name,
        monitor          = ws.monitor,
        on_created_empty = ws.on_created_empty,
        layout           = ws.layout,
        layout_opts      = ws.layout_opts,
        gaps_in          = ws.gaps_in,
        gaps_out         = ws.gaps_out,
        no_border        = ws.no_border,
        border_size      = ws.border_size,
        no_rounding      = ws.no_rounding,
        decorate         = ws.decorate,
        animation        = ws.animation,
        default_name     = ws.default_name,
    })
end


-- ==============================================================================
-- §6  MONITOR BINDING & PERSISTENCE
-- Bind specific numbered workspaces to specific monitors.
-- `default = true` means Hyprland will show this workspace when the monitor
-- is first connected (or has no other workspace assigned).
-- `persistent = true` keeps the workspace alive even when empty.
-- ==============================================================================
local enable_monitor_bindings = false

local monitor_bindings = {
    -- { workspace = "1",  monitor = "DP-1",   default = true,  persistent = true },
    -- { workspace = "6",  monitor = "eDP-1",  default = true,  persistent = true },
}

if enable_monitor_bindings then
    for _, binding in ipairs(monitor_bindings) do
        hl.workspace_rule(binding)
    end
end


-- ==============================================================================
-- §7  PER-WORKSPACE AESTHETIC OVERRIDES
-- Override visual properties on a workspace-by-workspace basis.
-- These stack on top of / override the global decoration settings.
-- ==============================================================================
local aesthetic_overrides = {
    -- Completely clean workspace — no distractions
    -- { workspace = "1",  gaps_in = 0, gaps_out = 0, no_border = true,
    --   no_rounding = true, decorate = false },

    -- Thick decorative border + vertical slide animation
    -- { workspace = "8",  border_size = 8, animation = "slidevert",
    --   default_name = "visuals" },
}

for _, override in ipairs(aesthetic_overrides) do
    hl.workspace_rule(override)
end


-- ==============================================================================
-- §8  PER-WORKSPACE LAYOUT OVERRIDES
-- Override the tiling layout on a per-workspace basis (for WS 11+).
-- Workspaces 1-10 layout overrides are now handled directly in §1.
--
-- Valid layout values: "dwindle" | "master" | "scrolling" | "monocle" | "lua:*"
-- ==============================================================================
local layout_overrides = {
    -- Master layout, master pane on top (horizontal split)
    -- { workspace = "11", layout = "master", layout_opts = { orientation = "top" } },
    
    -- Scrolling layout, tape grows downward
    -- { workspace = "12", layout = "scrolling", layout_opts = { direction = "down" } },
}

for _, override in ipairs(layout_overrides) do
    hl.workspace_rule(override)
end


-- ==============================================================================
-- §9  RANGE RULES (Global Fallbacks)
-- Apply rules to a range of workspaces using the "r[X-Y]" selector.
-- These are evaluated for all workspaces in the range that EXIST at the time.
-- ==============================================================================
local enforce_global_fallbacks = false

if enforce_global_fallbacks then
    -- Example: workspaces 11–99 use scrolling layout by default
    hl.workspace_rule({
        workspace = "r[11-99]",
        layout    = "scrolling",
    })
end


-- ==============================================================================
-- §10 GLOBAL WORKSPACE BEHAVIOUR
-- hl.config() calls that affect workspace/window behaviour globally.
-- The Python AST engine will mutate these keys directly. 
-- ==============================================================================
hl.config({
    -- §10a General — global layout
    general = {
        layout = "dwindle",
    },

    -- §10b Dwindle layout
    dwindle = {
        -- 0 = last window direction, 1 = always right/down, 2 = always left/up
        force_split                  = 0,
        -- keep the split direction when toggling — KEEP THIS TRUE for togglesplit
        preserve_split               = true,
        -- split based on window dimensions instead of count
        smart_split                  = false,
        -- resize the smaller side on manual resize
        smart_resizing               = true,
        permanent_direction_override = false,
        split_width_multiplier       = 1.0,
        use_active_for_splits        = true,
        default_split_ratio          = 1.0,
        split_bias                   = 0,
        precise_mouse_move           = false,
    },

    -- §10c Master layout
    master = {
        -- "master" | "slave" | "inherit" — where new windows go
        new_status                    = "slave",
        -- insert new slave windows at the TOP of the stack
        new_on_top                    = false,
        new_on_active                 = "none",
        -- fraction of the screen the master pane takes
        mfact                         = 0.55,
        -- "left" | "right" | "top" | "bottom" | "center"
        orientation                   = "left",
        -- allow adding extra master windows in horizontal-split style
        allow_small_split             = false,
        slave_count_for_center_master = 2,
        center_master_fallback        = "left",
        center_ignores_reserved       = false,
        smart_resizing                = true,
        drop_at_cursor                = true,
        always_keep_position          = false,
        focus_master_on_close         = false,
    },

    -- §10d Scrolling layout
    scrolling = {
        -- direction for new windows to spawn
        direction                = "right",
        -- single-column workspace fills screen
        fullscreen_on_one_column = true,
        column_width             = 0.9,
        -- 0 = center (previews adjacent windows on both left & right) | 1 = fit (preview right only)
        focus_fit_method         = 0,
        follow_focus             = true,
        -- Require 40% window visibility before mouse hover auto-scrolls; prevents runaway scrolling on edge previews
        follow_min_visible       = 0.4,
        explicit_column_widths   = "0.333, 0.5, 0.667, 1.0",
        wrap_focus               = true,
        wrap_swapcol             = true,
    },

    -- §10e Misc — special workspace & focus behaviour
    misc = {
        -- clean up empty scratchpads
        close_special_on_empty    = true,
        -- steal focus on activation
        focus_on_activate         = true,
        -- 0 = stay behind | 1 = take over | 2 = swap fs
        on_focus_under_fullscreen = 2,
    },

    -- §10f Binds — workspace navigation behaviour
    binds = {
        -- allow pinned windows to go fullscreen
        allow_pin_fullscreen              = true,
        -- toggle back on re-dispatch
        workspace_back_and_forth          = 0,
        -- wrap around at ends
        allow_workspace_cycles            = false,
        -- 0 = center on workspace, 1 = center on last active window
        workspace_center_on               = 0,
        -- keep scratchpad visible on switch
        hide_special_on_workspace_change  = false,
        -- movefocus wraps around fullscreen
        movefocus_cycles_fullscreen       = true,
        -- cross-monitor window movement
        window_direction_monitor_fallback = true,
    },
})

-- Additional TUI controls; existing configuration layers keep their load order.
hl.config({
    misc = {
        initial_workspace_tracking = 1,
        initial_workspace_token_timeout = 10,
        exit_window_retains_fullscreen = 0,
        float_force_onscreen = 0,
        new_float_force_onscreen = 2,
        size_limits_tiled = false,
    },
    binds = {
        focus_preferred_method = 0,
        movefocus_cycles_groupfirst = false,
        ignore_group_lock = false,
    },
    general = {
        modal_parent_blocking = true,
        no_focus_fallback = false,
    },
    layout = {
        single_window_aspect_ratio_tolerance = 0.1,
    },
    group = {
        auto_group = true,
        drag_into_group = 1,
        group_on_movetoworkspace = false,
        insert_after_current = true,
    },
})

-- Zero disables the optional aspect-ratio padding for a single window.
hl.config({ layout = { single_window_aspect_ratio = {0, 0} } })
