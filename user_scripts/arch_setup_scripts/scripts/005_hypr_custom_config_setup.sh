#!/usr/bin/env bash
# Initializes or validates the 'edit_here' user configuration overlay for Hyprland.
#              Ensures all template files exist.
#              Designed for Arch Linux / Hyprland 0.55+ / UWSM environments.
#              All configuration files use Lua syntax (.lua) as of Hyprland 0.55.
#              hyprlang (.conf) is deprecated and will be dropped in a future release.
#
# Usage:       ./005_hypr_custom_config_setup.sh [--force] [--<filename> ...]
#              --force:      Backs up existing configs and regenerates templates.
#              --<filename>: Dynamically targets specific files (e.g., --monitors, --trackpad).
#                            Combine flags to deploy multiple specific files.
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. Strict Mode & Configuration
# ------------------------------------------------------------------------------
set -euo pipefail

# --- ANSI Color Codes ---
readonly RED=$'\033[0;31m'
readonly GREEN=$'\033[0;32m'
readonly YELLOW=$'\033[0;33m'
readonly BLUE=$'\033[0;34m'
readonly RESET=$'\033[0m'

# --- Paths ---
readonly HYPR_DIR="${HOME}/.config/hypr"
readonly EDIT_DIR="${HYPR_DIR}/edit_here"
readonly EDIT_SOURCE_DIR="${EDIT_DIR}/source"
readonly MAIN_CONF="${HYPR_DIR}/hyprland.lua"
readonly NEW_CONF="${EDIT_DIR}/hyprland.lua"

# Lua require() strings that are inserted into / searched for in hyprland.lua.
# These are the EXACT literal strings written to and grepped from the main config.
#
# Dot-separated Lua module paths map to filesystem paths relative to ~/.config/hypr/:
#   "edit_here.source.default_apps"  ->  ~/.config/hypr/edit_here/source/default_apps.lua
#   "edit_here.hyprland"             ->  ~/.config/hypr/edit_here/hyprland.lua
readonly APPS_DEFAULTS_REQUIRE='require("edit_here.source.default_apps")'
readonly OVERLAY_REQUIRE='require("edit_here.hyprland")'

# ==============================================================================
# CONFIG FILE LIST  <<<  EDIT THIS TO ADD / REMOVE FILES  >>>
# ==============================================================================
# Each entry is a .lua filename created inside:
#   ~/.config/hypr/edit_here/source/
#
# The script will automatically:
#   - Create a template file if it does not already exist
#   - Append a require() line for it to ~/.config/hypr/edit_here/hyprland.lua
#     (the loader that is sourced at the bottom of hyprland.lua)
#
# "default_apps.lua" is SPECIAL:
#   It is require()d at the very TOP of hyprland.lua so that its global
#   variables are available to every other file.  If you rename it you must
#   also update the APPS_DEFAULTS_REQUIRE variable above.
#
# FUTURE EXPANSION EXAMPLE — splitting input.lua into sub-files:
#   Remove "input.lua" and add:
#     "keyboard.lua"
#     "touchpad.lua"
#     "cursor.lua"
#   Each new file is automatically picked up on next run.
# ==============================================================================
readonly -a CONFIG_FILES=(
    # --- Core (required at top of hyprland.lua via APPS_DEFAULTS_REQUIRE) ---
    "default_apps.lua"

    # --- Display & Layout ---
    "monitors.lua"
    "appearance.lua"
    "workspace_rules.lua"

    # --- Behavior ---
    "keybinds.lua"
    "input.lua"
    "trackpad.lua"
    "window_rules.lua"

    # --- Session ---
    "autostart.lua"
    "environment_variables.lua"
    "plugins.lua"

    # --- Future files: add new entries here ---
    # "keyboard.lua"
    # "touchpad.lua"
    # "cursor.lua"
)

# ------------------------------------------------------------------------------
# 2. Helper Functions
# ------------------------------------------------------------------------------
log_info()    { printf '%s[INFO]%s %s\n'    "${BLUE}"   "${RESET}" "${1:-}"; }
log_success() { printf '%s[OK]%s   %s\n'    "${GREEN}"  "${RESET}" "${1:-}"; }
log_warn()    { printf '%s[WARN]%s %s\n'    "${YELLOW}" "${RESET}" "${1:-}"; }
log_error()   { printf '%s[ERR]%s  %s\n'    "${RED}"    "${RESET}" "${1:-}" >&2; }

# ------------------------------------------------------------------------------
# Generates template content for each configuration file.
# All files use Lua syntax — comments are --, not #.
#
# NOTE: We use <<'EOF' (single-quoted) heredocs to prevent shell variable
# expansion, so Lua strings like "edit_here.source.foo" are written literally.
#
# EDIT THIS FUNCTION to update the default template for any file.
# ------------------------------------------------------------------------------
get_file_content() {
    local -r filename="${1:-}"

    case "${filename}" in

        # ======================================================================
        "default_apps.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: default_apps.lua
-- ==============================================================================
-- Override default applications here.
-- These are Lua GLOBALS (defined WITHOUT the 'local' keyword) so that they
-- are accessible in every file require()d after this one in hyprland.lua.
--
-- This file is require()d at the very TOP of hyprland.lua — before all
-- other config files — so these variables are always in scope.
--
-- See: https://wiki.hypr.land/Configuring/Start/
-- ==============================================================================

-- -------------------------------------------------------------------------------------------------
-- User Configurable Defaults
-- -------------------------------------------------------------------------------------------------

terminal    = "foot"
fileManager = "nemo"
menu        = "rofi -show drun"
browser     = "firefox"
textEditor  = "nvim"
EOF
            ;;

        # ======================================================================
        "monitors.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: monitors.lua
-- ==============================================================================
-- Add your monitor configuration here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky monitor from the rofi menu or
-- from dusky control center.
-- HOW THIS FILE IS STRUCTURED
-- ──────────────────────────────────────────────────────────────────────────
--  SECTION 1 │ GLOBAL FALLBACK RULE       (required — keep this enabled)
--  SECTION 2 │ LAPTOP BUILT-IN DISPLAY    (eDP-1 example)
--  SECTION 3 │ EXTERNAL / DESKTOP MONITORS (DP / HDMI examples)
--  SECTION 4 │ MIRROR / CLONE SETUP
--  SECTION 5 │ DISABLING A MONITOR
--  SECTION 6 │ WORKSPACE → MONITOR BINDINGS
--  SECTION 7 │ GLOBAL RENDER & POWER SETTINGS (VRR, VFR, color pipeline)
--
-- QUICK REFERENCE — hl.monitor() FIELDS
-- ──────────────────────────────────────────────────────────────────────────
--  output         STRING   Port name ("eDP-1", "DP-1", "HDMI-A-1") or ""
--                          for the global fallback.  Use `hyprctl monitors all`
--                          to list every connected and disconnected output.
--                          You may also match by description (see SECTION 3).
--
--  mode           STRING   "WIDTHxHEIGHT[@REFRESH]"  e.g. "1920x1080@144"
--                          Special values: "preferred"  (native res/rate)
--                                          "highres"    (highest resolution)
--                                          "highrr"     (highest refresh rate)
--
--  position       STRING   "XxY" pixel offset from the virtual layout origin.
--                          Hyprland uses an INVERSE-Y system:
--                            negative Y = higher on screen
--                            positive Y = lower on screen
--                          Special values: "auto"        (place to the right)
--                                          "auto-left"   "auto-right"
--                                          "auto-up"     "auto-down"
--
--  scale          NUMBER   Fractional scale factor, e.g. 1, 1.5, 2.
--                 STRING   "auto" lets Hyprland pick based on PPI.
--                          Tip: integer scales (1, 2) avoid sub-pixel blur.
--                          Valid scale = resolution / scale must be integer.
--
--  transform      NUMBER   Screen rotation / flip:
--                            0  normal              4  flipped
--                            1  90°                 5  flipped + 90°
--                            2  180°                6  flipped + 180°
--                            3  270°                7  flipped + 270°
--
--  mirror         STRING   Output name to clone this monitor from.
--                          e.g.  mirror = "eDP-1"  makes this display a copy.
--
--  disabled       BOOLEAN  true = tell Hyprland this output does not exist.
--                          Useful for phantom outputs (e.g. "Unknown-1").
--
--  bitdepth       NUMBER   8 (default) or 10 for 10-bit colour output.
--                          NOTE: Hyprland border colours do NOT support 10-bit.
--                          Some screen-capture tools also break with 10-bit.
--
--  cm             STRING   Colour management preset:
--                            "auto"     automatic (default)
--                            "sdronly"  force SDR pipeline
--                            "hdr"      HDR output (requires HDR-capable panel)
--                            "edid"     use display's EDID colour profile
--
--  sdrbrightness  NUMBER   SDR content brightness multiplier when HDR is on.
--                          Range 0.5–2.0.  Default ~1.0.
--
--  sdrsaturation  NUMBER   SDR content saturation multiplier when HDR is on.
--                          Range 0.5–1.5.  Default ~1.0.
--
--  sdr_eotf       STRING   Transfer function assumed for SDR/sRGB content:
--                            "default"  follows render.cm_sdr_eotf (global)
--                            "srgb"     piecewise sRGB
--                            "gamma22"  Gamma 2.2
--
--  icc            STRING   ABSOLUTE path to an .icm / .icc profile.
--                          Forces sdr_eotf = "srgb" automatically.
--                          Overrides the cm preset.
--                          ⚠  Incompatible with HDR gaming; artefacts may occur.
--
--  vrr            NUMBER   Variable Refresh Rate override for this monitor:
--                            0  off
--                            1  always on
--                            2  fullscreen apps only (recommended for desktops)
--                          Overrides the global misc.vrr setting.
--
--  reserved_area  NUMBER   Pixels reserved on all four edges (single value), or
--                 TABLE    a table { top=N, bottom=N, left=N, right=N }.
--                          Stacks on top of bars / layer-shells.
--                          Only ONE reserved_area rule per monitor is allowed.
-- ──────────────────────────────────────────────────────────────────────────


-- #############################################################################
-- SECTION 1 — GLOBAL FALLBACK RULE
-- #############################################################################
-- This catches any monitor that has no explicit rule below.
-- Critical for plug-and-play (projectors, docks, etc.) — do NOT remove this.
-- Change scale to 2 here if you commonly hotplug HiDPI external displays.

hl.monitor({
    output   = "",          -- "" = match any output not covered by a specific rule
    mode     = "preferred", -- use the display's advertised native resolution & rate
    position = "auto",      -- auto-place to the right of other monitors
    scale    = "auto",      -- let Hyprland decide based on PPI
})


-- #############################################################################
-- SECTION 2 — LAPTOP BUILT-IN DISPLAY (eDP-1)
-- #############################################################################
-- Uncomment and adjust the block that matches your use-case.
-- Run `hyprctl monitors all` to verify your internal display is named "eDP-1".

-- ── 2a. Standard laptop panel ─────────────────────────────────────────────
-- hl.monitor({
--     output    = "eDP-1",
--     mode      = "preferred",   -- or e.g. "2560x1600@165"
--     position  = "0x0",
--     scale     = 1,             -- use 2 for HiDPI / Retina panels
--     transform = 0,             -- 0 = normal (no rotation)
-- })

-- ── 2b. Laptop panel — 10-bit HDR (requires HDR-capable display) ───────────
-- hl.monitor({
--     output        = "eDP-1",
--     mode          = "2880x1800@90",
--     position      = "0x0",
--     scale         = 2,
--     bitdepth      = 10,        -- 10-bit colour depth
--     cm            = "hdr",     -- enable HDR colour pipeline
--     sdrbrightness = 1.0,       -- SDR content brightness in HDR mode (0.5–2.0)
--     sdrsaturation = 1.0,       -- SDR content saturation in HDR mode (0.5–1.5)
-- })

-- ── 2c. Laptop panel with ICC colour profile ───────────────────────────────
-- Absolute path required. Automatically forces sdr_eotf = "srgb".
-- hl.monitor({
--     output = "eDP-1",
--     mode   = "preferred",
--     position = "0x0",
--     scale  = 2,
--     icc    = "/home/USERNAME/.config/hypr/icc/your_panel.icm",
-- })

-- ── 2d. Laptop panel — custom SDR transfer function ───────────────────────
-- Use when you want explicit control over how sRGB content is tone-mapped.
-- hl.monitor({
--     output   = "eDP-1",
--     mode     = "preferred",
--     position = "0x0",
--     scale    = 2,
--     sdr_eotf = "srgb",         -- "default" | "srgb" | "gamma22"
-- })


-- #############################################################################
-- SECTION 3 — EXTERNAL / DESKTOP MONITORS
-- #############################################################################
-- You can match monitors by port name OR by description string.
-- Description matching is more robust (survives port changes on docks):
--   desc:MANUFACTURER MODEL SERIAL   e.g. desc:LG Electronics LG HDR 4K 0x00007B3E
-- Get the description string from:  hyprctl monitors all

-- ── 3a. Single external monitor (simple) ──────────────────────────────────
-- hl.monitor({
--     output   = "DP-1",         -- or HDMI-A-1, DP-2, etc.
--     mode     = "1920x1080@144",
--     position = "0x0",
--     scale    = 1,
-- })

-- ── 3b. Dual-monitor horizontal layout (laptop left, external right) ───────
-- Place the laptop screen at the left edge (x = 0).
-- Place the external monitor immediately to the right (x = laptop logical width).
-- If laptop is 2560px wide at scale 2, its logical width = 1280 → use "1280x0".
--
-- hl.monitor({
--     output   = "eDP-1",
--     mode     = "2560x1600@165",
--     position = "0x0",
--     scale    = 2,
-- })
-- hl.monitor({
--     output   = "DP-1",
--     mode     = "1920x1080@144",
--     position = "1280x0",       -- eDP-1 logical width (2560 / 2) = 1280
--     scale    = 1,
-- })

-- ── 3c. Triple-monitor layout (left / centre / right) ─────────────────────
-- hl.monitor({
--     output   = "DP-1",
--     mode     = "1920x1080@144",
--     position = "0x0",
--     scale    = 1,
-- })
-- hl.monitor({
--     output   = "DP-2",
--     mode     = "2560x1440@165",
--     position = "1920x0",
--     scale    = 1,
-- })
-- hl.monitor({
--     output   = "HDMI-A-1",
--     mode     = "1920x1080@60",
--     position = "4480x0",       -- 1920 + 2560
--     scale    = 1,
-- })

-- ── 3d. Vertical stack (primary on top, secondary below) ──────────────────
-- Hyprland's Y axis is inverted: positive Y goes downward on screen.
-- hl.monitor({
--     output   = "DP-1",
--     mode     = "2560x1440@165",
--     position = "0x0",
--     scale    = 1,
-- })
-- hl.monitor({
--     output   = "HDMI-A-1",
--     mode     = "1920x1080@60",
--     position = "0x1440",       -- placed directly below DP-1
--     scale    = 1,
-- })

-- ── 3e. Portrait monitor (rotated 90°) ────────────────────────────────────
-- When rotated, logical dimensions are swapped.
-- A 1080x1920 portrait monitor's logical width = 1080 → next monitor at "1080x0".
-- hl.monitor({
--     output    = "DP-3",
--     mode      = "1920x1080@60",
--     position  = "0x0",
--     scale     = 1,
--     transform = 1,             -- 1 = 90°  |  3 = 270°
-- })

-- ── 3f. 4K external with per-monitor VRR and 10-bit ───────────────────────
-- hl.monitor({
--     output   = "DP-1",
--     mode     = "3840x2160@144",
--     position = "0x0",
--     scale    = 2,
--     bitdepth = 10,
--     vrr      = 2,              -- VRR only for fullscreen apps (0=off 1=on 2=fs-only)
-- })

-- ── 3g. Match by monitor description (dock / hotplug-safe) ────────────────
-- hl.monitor({
--     output   = "desc:Dell Inc. DELL S2722DGM F9GHVJ3",
--     mode     = "2560x1440@165",
--     position = "1920x0",
--     scale    = 1,
-- })


-- #############################################################################
-- SECTION 4 — MIRROR / CLONE SETUP
-- #############################################################################
-- Mirrors duplicate another monitor's output pixel-for-pixel.
-- The `mirror` field takes the output NAME of the source display.

-- ── 4a. Mirror one specific monitor to another ────────────────────────────
-- hl.monitor({
--     output   = "HDMI-A-1",
--     mode     = "1920x1080@60",
--     position = "0x0",
--     scale    = 1,
--     mirror   = "eDP-1",        -- clone eDP-1 onto HDMI-A-1
-- })

-- ── 4b. Mirror all hotplugged monitors to the primary display ─────────────
-- (Combine with the global fallback rule in SECTION 1)
-- hl.monitor({
--     output   = "",
--     mode     = "preferred",
--     position = "auto",
--     scale    = 1,
--     mirror   = "eDP-1",        -- every unspecified output mirrors eDP-1
-- })


-- #############################################################################
-- SECTION 5 — DISABLING A MONITOR
-- #############################################################################
-- Use `disabled = true` to tell Hyprland a port does not exist.
-- This is especially useful for phantom outputs that appear on some GPUs.
-- To blank an active display temporarily, use the DPMS dispatcher instead:
--   hl.dispatch(hl.dsp.dpms({ action = "disable" }))

-- ── 5a. Suppress a phantom / ghost output ─────────────────────────────────
-- hl.monitor({
--     output   = "Unknown-1",
--     disabled = true,
-- })

-- ── 5b. Disable a known port until you need it ────────────────────────────
-- hl.monitor({
--     output   = "HDMI-A-2",
--     disabled = true,
-- })


-- #############################################################################
-- SECTION 6 — WORKSPACE → MONITOR BINDINGS
-- #############################################################################
-- Use hl.workspace_rule() to pin specific workspaces to specific monitors.
-- `monitor` accepts a port name OR a "desc:..." description string.
-- `default = true` makes that workspace the one shown when the monitor connects.

-- ── 6a. Pin individual workspaces to monitors ─────────────────────────────
-- hl.workspace_rule({ workspace = "1",  monitor = "eDP-1",   default = true })
-- hl.workspace_rule({ workspace = "2",  monitor = "eDP-1" })
-- hl.workspace_rule({ workspace = "3",  monitor = "eDP-1" })
-- hl.workspace_rule({ workspace = "4",  monitor = "eDP-1" })
-- hl.workspace_rule({ workspace = "5",  monitor = "eDP-1" })
-- hl.workspace_rule({ workspace = "6",  monitor = "DP-1",    default = true })
-- hl.workspace_rule({ workspace = "7",  monitor = "DP-1" })
-- hl.workspace_rule({ workspace = "8",  monitor = "DP-1" })
-- hl.workspace_rule({ workspace = "9",  monitor = "DP-1" })
-- hl.workspace_rule({ workspace = "10", monitor = "DP-1" })

-- ── 6b. Pin a named workspace to a monitor (by description) ───────────────
-- hl.workspace_rule({
--     workspace = "name:gaming",
--     monitor   = "desc:LG Electronics LG ULTRAGEAR 0x0000B256",
--     default   = true,
-- })

-- ── 6c. Reserved area for a specific monitor ──────────────────────────────
-- Use this when a bar/panel does not automatically reserve space,
-- or when you want extra padding on any edge.
-- hl.monitor({
--     output        = "eDP-1",
--     mode          = "preferred",
--     position      = "0x0",
--     scale         = 2,
--     reserved_area = { top = 0, bottom = 0, left = 0, right = 0 },
-- })
--
-- Or as a single integer for equal padding on all sides:
-- hl.monitor({ output = "eDP-1", reserved_area = 10 })


-- #############################################################################
-- SECTION 7 — GLOBAL RENDER & POWER SETTINGS
-- #############################################################################
-- These hl.config() options affect all monitors globally.
-- Per-monitor VRR overrides can be set with the `vrr` field in hl.monitor().

hl.config({

    misc = {
        -- ── Variable Refresh Rate (global default) ────────────────────────
        -- Overridden per-monitor by the `vrr` field in hl.monitor().
        --   0 = disabled
        --   1 = always enabled  (can cause brightness flicker on some displays)
        --   2 = fullscreen apps only  ← recommended for most users
        vrr = 0,
    },

    debug = {
        -- ── Variable Frame Rate (power saving) ───────────────────────────
        -- When true, Hyprland stops sending frames to the GPU while nothing
        -- is changing on screen.  Saves ~1 W on a laptop; looks identical.
        -- Set to false only if you notice input latency regressions.
        vfr = true,
    },

    render = {
        -- ── Global SDR EOTF (transfer function for SDR/sRGB content) ─────
        -- Applied to every monitor whose per-monitor sdr_eotf is "default".
        --   "auto"    Hyprland decides (recommended)
        --   "srgb"    piecewise sRGB curve  (best colour accuracy on most panels)
        --   "gamma22" traditional Gamma 2.2
        -- cm_sdr_eotf = "auto",

        -- ── Fullscreen HDR passthrough ────────────────────────────────────
        -- When true, fullscreen apps that output HDR signals bypass Hyprland's
        -- colour pipeline entirely for zero-overhead HDR gaming.
        -- Alternative to setting cm = "hdr" per-monitor.
        -- cm_fs_passthrough = false,

        -- ── Automatic HDR ─────────────────────────────────────────────────
        -- Experimental: automatically promote SDR content to HDR where possible.
        -- Requires --target-colorspace-hint-mode=source in mpv ≥ 0.41.
        -- cm_auto_hdr = false,
    },

})
EOF
            ;;

        # ======================================================================
        "keybinds.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: keybinds.lua
-- ==============================================================================
-- Add your custom keybinds here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky keybinds manager from the rofi
-- menu or from dusky control center.
--
-- Syntax:
--   local mainMod = "SUPER"
--   hl.bind(mainMod .. " + Q", hl.dsp.exec_cmd(terminal))
--   hl.bind(mainMod .. " + Q", hl.dsp.exec_cmd("kitty"), { description = "Launch terminal" })
--
-- NOTE: 'terminal', 'browser', etc. are globals defined in default_apps.lua.
--
-- See: https://wiki.hypr.land/Configuring/Basics/Binds/
-- ==============================================================================

-- local mainMod = "SUPER"

hl.bind(
    "SUPER + Q",
    hl.dsp.exec_cmd(terminal),
    { description = "Launch Terminal" }
)

hl.bind(
    "SUPER + W",
    hl.dsp.exec_cmd(browser),
    { description = "Launch Browser" }
)

hl.bind(
    "SUPER + E",
    hl.dsp.exec_cmd(fileManager),
    { description = "File Manager" }
)

hl.bind(
    "SUPER + R",
    hl.dsp.exec_cmd(textEditor),
    { description = "Open Text Editor" }
)
EOF
            ;;

        # ======================================================================
        "appearance.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: appearance.lua
-- ==============================================================================
-- Add your custom appearance settings here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky appearance from the rofi menu or
-- from dusky control center.
-- -------------------------------------------------------------------------------------------------
-- APPEARANCE, DECORATION & RENDERING
-- -------------------------------------------------------------------------------------------------

hl.config({
    -- ==========================================
    -- GENERAL (Borders, Gaps, Colors)
    -- ==========================================
    general = {
        border_size = 1, -- Size of the border around windows
        gaps_in = 4, -- Gaps between windows
        gaps_out = 8, -- Gaps between windows and monitor edges
        float_gaps = 0, -- Gaps for floating windows (-1 means default)
        gaps_workspaces = 0, -- Gaps between workspaces (stacks with gaps_out)

        ["col.inactive_border"] = inverse_on_surface, -- Border color for inactive windows
        ["col.active_border"] = primary, -- Border color for the active window
        ["col.nogroup_border"] = inverse_on_surface, -- Inactive border color for window that cannot be added to a group
        ["col.nogroup_border_active"] = secondary, -- Active border color for window that cannot be added to a group

        resize_on_border = true, -- Enables resizing windows by clicking and dragging on borders and gaps
        extend_border_grab_area = 15, -- Extends click/drag area around the border (needs resize_on_border)
        hover_icon_on_border = true, -- Shows cursor icon when hovering over borders (needs resize_on_border)
        allow_tearing = true, -- Master switch for allowing tearing to occur
        resize_corner = 0 -- Forces floating windows to use specific corner when resized (1-4, 0 to disable)
    },

    -- ==========================================
    -- DECORATION (Rounding, Blur, Shadows)
    -- ==========================================
    decoration = {
        rounding = 10, -- Rounded corners' radius (in layout px)
        rounding_power = 2.5, -- Curve used for rounding (2.0 is circle, 4.0 squircle, 1.0 triangular)
        active_opacity = 0.85, -- Opacity of active windows [0.0 - 1.0]
        inactive_opacity = 0.85, -- Opacity of inactive windows [0.0 - 1.0]
        fullscreen_opacity = 1.0, -- Opacity of fullscreen windows [0.0 - 1.0]
        dim_modal = true, -- Enables dimming of parents of modal windows
        dim_inactive = true, -- Enables dimming of inactive windows
        dim_strength = 0.3, -- How much inactive windows should be dimmed [0.0 - 1.0]
        dim_special = 0.8, -- How much to dim screen when special workspace is open [0.0 - 1.0]
        dim_around = 0.4, -- How much the dim_around window rule should dim by [0.0 - 1.0]
        screen_shader = "", -- Path to custom shader applied at the end of rendering
        border_part_of_window = true, -- Whether the window border should be a part of the window

        blur = {
            enabled = true, -- Enable kawase window background blur
            size = 10, -- Blur size (distance)
            passes = 2, -- Amount of passes to perform
            ignore_opacity = true, -- Make the blur layer ignore the opacity of the window
            new_optimizations = true, -- Enable further optimizations (massively improves performance)
            xray = false, -- Floating windows ignore tiled windows in blur (reduces overhead)
            noise = 0.0117, -- How much noise to apply [0.0 - 1.0]
            contrast = 0.8916, -- Contrast modulation for blur [0.0 - 2.0]
            brightness = 0.8172, -- Brightness modulation for blur [0.0 - 2.0]
            vibrancy = 0.1696, -- Increase saturation of blurred colors [0.0 - 1.0]
            vibrancy_darkness = 0.0, -- How strong vibrancy effect is on dark areas [0.0 - 1.0]
            special = false, -- Whether to blur behind special workspace (expensive)
            popups = false, -- Whether to blur popups (e.g. right-click menus)
            popups_ignorealpha = 0.2, -- If pixel opacity is below this, will not blur popups [0.0 - 1.0]
            input_methods = false, -- Whether to blur input methods (e.g. fcitx5)
            input_methods_ignorealpha = 0.2 -- If pixel opacity is below this, will not blur input methods [0.0 - 1.0]
        },

        shadow = {
            enabled = true, -- Enable drop shadows on windows
            range = 10, -- Shadow range ("size") in layout px
            render_power = 1, -- Falloff power (more power = faster falloff) [1 - 4]
            sharp = false, -- Make shadows sharp, akin to infinite render power
            color = "rgba(1a1a1aee)", -- Shadow's color. Alpha dictates opacity
            offset = {0, 0}, -- Shadow's rendering offset
            scale = 1.0 -- Shadow's scale [0.0 - 1.0]
        },

        glow = {
            enabled = false, -- Enable inner glow on windows
            range = 10, -- Glow range ("size") in layout px
            render_power = 3, -- Falloff power [1 - 4]
            color = primary_container -- Glow's color. Alpha dictates opacity
        }
    },

    -- ==========================================
    -- ANIMATIONS
    -- ==========================================
    animations = {
        workspace_wraparound = false -- Directional workspace animations animate as if first/last are adjacent
    },

    -- ==========================================
    -- GROUP UI (Colors & Groupbars)
    -- ==========================================
    group = {
        ["col.border_active"] = primary, -- Active group border color
        ["col.border_inactive"] = inverse_on_surface, -- Inactive group border color
        ["col.border_locked_active"] = tertiary, -- Active locked group border color
        ["col.border_locked_inactive"] = tertiary_container, -- Inactive locked group border color

        groupbar = {
            enabled = true, -- Enables groupbars
            font_family = "", -- Font for groupbar titles (falls back to misc.font_family)
            font_size = 8, -- Font size of title
            font_weight_active = "normal", -- Font weight of active title
            font_weight_inactive = "normal", -- Font weight of inactive title
            gradients = false, -- Enables gradients
            height = 14, -- Height of groupbar
            indicator_gap = 0, -- Gap between indicator and title
            indicator_height = 3, -- Height of indicator
            stacked = false, -- Render as vertical stack
            priority = 3, -- Decoration priority
            render_titles = true, -- Render titles in decoration
            text_offset = 0, -- Vertical position adjust for titles
            text_padding = 0, -- Horizontal padding for titles
            rounding = 1, -- Round indicator
            rounding_power = 2.0, -- Curve used for rounding indicator
            gradient_rounding = 2, -- Round gradients
            gradient_rounding_power = 2.0, -- Curve used for rounding gradients
            round_only_edges = true, -- Round only indicator edges
            gradient_round_only_edges = true, -- Round only gradient edges
            text_color = on_surface, -- Title color
            ["col.active"] = primary, -- Active background color
            ["col.inactive"] = inverse_on_surface, -- Inactive background color
            ["col.locked_active"] = tertiary, -- Active locked background color
            ["col.locked_inactive"] = tertiary_container, -- Inactive locked background color
            gaps_in = 2, -- Gap between gradients
            gaps_out = 2, -- Gap between gradients and window
            keep_upper_gap = true, -- Add/remove upper gap
            blur = false -- Apply blur to indicators and gradients
        }
    },

    -- ==========================================
    -- MISC VISUALS & UI
    -- ==========================================
    misc = {
        disable_hyprland_logo = true, -- Disables random anime girl background
        disable_splash_rendering = true, -- Disables splash rendering
        font_family = "Sans", -- Default font for debug/error text
        splash_font_family = "", -- Font for splash text
        force_default_wallpaper = 1, -- Enforce default wallpapers (-1 random, 0/1 disables anime)
        animate_manual_resizes = false, -- Animate manual window resizes/moves
        animate_mouse_windowdragging = false, -- Animate windows being dragged by mouse
        background_color = background, -- Custom background color
        render_unfocused_fps = 5, -- Max FPS limit for unfocused background windows
        enable_anr_dialog = true -- Enable "App Not Responding" dialog
    },

    -- ==========================================
    -- RENDER PIPELINE & XWAYLAND SCALING
    -- ==========================================
    xwayland = {
        use_nearest_neighbor = true, -- Nearest neighbor filtering (pixelated vs blurry)
        force_zero_scaling = false -- Force scale of 1 on xwayland windows on scaled displays
    },

    opengl = {
        nvidia_anti_flicker = true -- Reduces flickering on nvidia (ignored on others)
    },

    render = {
        direct_scanout = 0, -- Attempt to reduce lag for single fullscreen app [0=off, 1=on, 2=auto]
        expand_undersized_textures = true, -- Expand undersized textures vs stretching entire texture
        xp_mode = false, -- Disables back buffer and bottom layer rendering
        ctm_animation = 2, -- Fade animation for CTM changes (2=auto disables on Nvidia)
        use_shader_blur_blend = false -- Blurred bg blending
    },

    -- ==========================================
    -- DEBUG VISUALS
    -- ==========================================
    debug = {
        overlay = false, -- Print debug performance overlay
        damage_blink = false, -- Flash areas updated with damage tracking
        colored_stdout_logs = true -- Colors in stdout logs
    }
})

-- -------------------------------------------------------------------------------------------------
-- SINGLE WINDOW APPEARANCE
-- Applied when exactly one tiled window is on screen (w[tv1]), or when
-- a window is maximized (f[1]). Excludes special/scratchpad workspaces (s[false]).
--
-- WHAT CANNOT BE SET HERE (window rules don't support these — global hl.config() only):
--   • rounding_power  → decoration.rounding_power in hl.config()
--   • no_shadow / no_dim → no per-window shadow or dim suppression in 0.55 window rules
--   • blur sub-options (size, passes, etc.) → decoration.blur in hl.config()
--   • border_size → must live in hl.workspace_rule(), not hl.window_rule()
-- -------------------------------------------------------------------------------------------------

-- Workspace-level: gaps + border (border_size is only valid here, not in hl.window_rule)
hl.workspace_rule({ workspace = "w[tv1]s[false]", gaps_out = 8, gaps_in = 4, border_size = 1 })
hl.workspace_rule({ workspace = "f[1]s[false]",   gaps_out = 8, gaps_in = 4, border_size = 1 })

-- Single tiled window
hl.window_rule({
    name  = "single_window_style",
    match = { float = false, workspace = "w[tv1]s[false]" },

    -- ROUNDING
    -- matches your global decoration.rounding = 10
    -- set to 0 for sharp corners on a lone window, or keep 10 to match global
    rounding      = 10,
    rounding_power = 2.5,

    -- OPACITY
    -- format: "active [override] inactive [override] fullscreen [override]"
    -- "override" makes it absolute instead of multiplicative with other rules
    -- your global active_opacity and inactive_opacity are both 0.85
    -- using override here so it doesn't compound with the global value
    -- opacity       = "0.85 override 0.85 override 1.0 override",
    opacity       = 0.85,

    -- BLUR
    -- false = keep blur enabled (matches your global blur.enabled = true)
    -- set to true to disable blur for this window only
    no_blur       = false,

    -- BORDER COLOR
    -- leave unset to inherit global col.active_border / col.inactive_border
    -- uncomment to override, e.g. a gradient:
    -- border_color = "rgb(ffffff) rgb(000000) 45deg",

    -- ANIMATION
    -- override the open/close animation for this window
    -- options: "popin", "popin 80%", "slide", "gnomed", or unset to inherit global
    -- animation = "popin 80%",

    -- TEARING
    -- allow this window to request tearing (reduce latency)
    -- matches your global allow_tearing = true, but this is per-window opt-in
    -- immediate = false,
})

-- Maximized window (f[1] = workspace has a maximized window)
hl.window_rule({
    name  = "maximized_window_style",
    match = { float = true, workspace = "f[1]s[false]" },

    rounding      = 10,
    opacity       = 0.45, -- override 0.85 override 1.0 override"
    no_blur       = true,

    -- border_color = "rgb(ffffff) rgb(000000) 45deg",
    -- animation = "popin 80%",
    -- immediate = false,
})



-- -------------------------------------------------------------------------------------------------
-- SPECIAL WORKSPACE APPEARANCE
-- "magic"  → toggled with SUPER+Z  (hl.dsp.workspace.toggle_special("magic"))
--
-- A special workspace is a floating overlay that appears on top of your current workspace.
-- The background dims according to decoration.dim_special (currently 0.8 in hl.config()).
-- The blur *behind* the overlay is controlled by decoration.blur.special (currently false).
--
-- WHAT CANNOT BE SET PER SPECIAL WORKSPACE (global hl.config() only):
--   • dim_special   → decoration.dim_special        ← already 0.8 in your hl.config()
--   • blur.special  → decoration.blur.special       ← currently false; set true to blur behind it
--   • col.active_border / col.inactive_border       ← global only; use border_color in window_rule
-- -------------------------------------------------------------------------------------------------

-- Workspace-level: gaps + border thickness for the magic scratchpad
hl.workspace_rule({
    workspace   = "special:magic",
    gaps_in     = 26,    -- gap between windows inside the scratchpad
    gaps_out    = 80,   -- large outer margin so it feels centered/floating, not edge-to-edge
    border_size = 8,    -- slightly thicker than your global 1, makes it feel distinct
})

-- Window-level: per-window appearance for everything inside special:magic
hl.window_rule({
    name           = "special_magic_style",
    match          = { workspace = "special:magic" },

    -- ROUNDING: slightly more than global 10 for a softer "popup" feel
    rounding       = 12,
    rounding_power = 2.5,

    -- OPACITY: more opaque than your global 0.85 so it pops against the dimmed background
    opacity        = 0.92,

    -- BORDER COLOR: secondary instead of primary so you can visually tell this isn't a normal window
    border_color   = secondary,

    -- BLUR: keep enabled to match your global blur.enabled = true
    no_blur        = false,
})




-- -------------------------------------------------------------------------------------------------
--  ANIMATIONS
-- -------------------------------------------------------------------------------------------------

-- Sourcing active animations
require("source.animations.active.active")
EOF
            ;;

        # ======================================================================
        "autostart.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: autostart.lua
-- ==============================================================================
-- Add your custom autostart entries here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
--
-- Syntax:
--   hl.on("hyprland.start", function()
--       hl.exec_cmd("waybar")
--       hl.exec_cmd("nm-applet")
--   end)
--
-- See: https://wiki.hypr.land/Configuring/Basics/Autostart/
-- ==============================================================================

-- --- XWAYLAND CONFIGURATION ---
-- to disable xwayland to save 20-30 mbs of ram, disabling will prevent xwayland apps from working
-- Uncomment the block below to apply:
hl.config({
    xwayland = {
        enabled = true
    }
})

-- -------------------------------------------------------------------------------------------------
-- AUTOSTART COMMANDS
-- -------------------------------------------------------------------------------------------------
hl.on("hyprland.start", function()

    -- --- SYSTEM ESSENTIALS ---

    -- Gnome Keyring: Stores passwords for apps (VSCode, Chrome, etc.). (recommanded to enable systemd service instead of auto starting with exec-once)
    -- hl.exec_cmd("uwsm-app -- /usr/bin/gnome-keyring-daemon --start --components=secrets")
    -- OR
    -- replace the exec-once line with:
    -- hl.exec_cmd("uwsm-app -- systemctl --user start gnome-keyring-daemon.service")

    -- XHost: Grants root access to the display (needed for GParted/Synaptic to run).
    -- make sure to install xorg-xhost beofre uncommenting the following line, sudo pacman -S xorg-xhost
    -- hl.exec_cmd("uwsm-app -- xhost +si:localuser:root")

    -- --- BACKGROUND SERVICES ---
    hl.exec_cmd("uwsm-app -- awww-daemon")           -- Wallpaper engine

    -- hypridle has systemd service
    -- hl.exec_cmd("uwsm-app -- hypridle")              -- Idle manager
    -- hl.exec_cmd("uwsm-app -- $HOME/user_scripts/hypr/layout_notify.sh") -- Keyboard Layout Notify

    -- --- CLIPBOARD MANAGER ---
    -- hl.exec_cmd("uwsm-app -- wl-paste --type text --watch cliphist store")
    -- hl.exec_cmd("uwsm-app -- wl-paste --type image --watch cliphist store")

    hl.exec_cmd("uwsm-app -- sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store'")
    hl.exec_cmd("uwsm-app -- sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type image --watch cliphist store'")

    hl.exec_cmd("uwsm-app -- wl-clip-persist --clipboard regular")

    -- --- OPTIONAL / USER INTERFACE ---
    hl.exec_cmd("uwsm-app -- $HOME/user_scripts/waybar/waybar_toggle.sh")
    -- hl.exec_cmd("uwsm-app -- $HOME/user_scripts/waybar/toggle_timer_waybar.sh")
    -- hl.exec_cmd("uwsm-app -- nm-applet")

    -- --- Slow app launch fix -- set systemd vars
    -- The subshell evaluating $(env | cut -d'=' -f 1) is passed directly as a string 
    -- to be evaluated by the shell instance spawned by hl.exec_cmd
    hl.exec_cmd("systemctl --user import-environment $(env | cut -d'=' -f 1)")
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")

    -- --- dusky glance ---
    -- EG: dusky glance (uncomment only one at a time)
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --temp")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --uptime")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --workspace")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock")

end)

EOF
            ;;

        # ======================================================================
        "plugins.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: plugins.lua
-- ==============================================================================
-- Add your plugin configuration here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
--
-- See: https://wiki.hypr.land/Plugins/Using-Plugins/
-- ==============================================================================

EOF
            ;;

        # ======================================================================
        "window_rules.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: window_rules.lua
-- ==============================================================================
-- Add your custom window rules here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
--
-- Syntax:
--   hl.window_rule({
--       name  = "my-rule-name",            -- unique identifier (required)
--       match = { class = "^kitty$" },     -- match table
--       float = true,
--   })
--
--   hl.layer_rule({
--       name  = "my-layer-rule",
--       match = { namespace = "^waybar$" },
--       blur  = true,
--   })
--
-- See: https://wiki.hypr.land/Configuring/Basics/Window-Rules/
-- ==============================================================================

EOF
            ;;

        # ======================================================================
        "workspace_rules.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: workspace_rules.lua
-- ==============================================================================
--
-- HOW THIS FILE IS ORGANIZED:
--
--   §1  TUI DATA INJECTION POINT  ......  Machine-written, leave the table here
--   §2  DYNAMIC WORKSPACE GENERATOR  ....  Loop-based rules for workspaces 1-10
--   §3  SMART GAPS  .....................  No gaps / borders when only one window
--   §4  SPECIAL WORKSPACES / SCRATCHPADS  Floating overlay workspaces
--   §5  NAMED / PROJECT WORKSPACES  .....  Semantic per-project environments
--   §6  MONITOR BINDING & PERSISTENCE  ..  Assign workspaces to specific outputs
--   §7  PER-WORKSPACE AESTHETICS  .......  Borders, rounding, gaps, animations
--   §8  PER-WORKSPACE LAYOUT OVERRIDES  .  dwindle / master / scrolling / custom
--   §9  RANGE RULES (Global Fallbacks)  .  Catch-all rules for workspace ranges
--   §10 GLOBAL WORKSPACE BEHAVIOUR  ....  hl.config() options that affect all ws
--       §10a  General (layout)
--       §10b  Dwindle layout
--       §10c  Master layout
--       §10d  Scrolling layout
--       §10e  Misc
--       §10f  Binds (navigation behaviour)
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
--   "f[2]"              →  selector: fullscreen but state not sent to the client
--   "s[false]"          →  selector: exclude special workspaces
--   "s[true]"           →  selector: special workspaces only
--   Selectors can be combined with a space: "w[tv1]s[false]"
--   NOTE: selectors only match EXISTING workspaces at evaluation time.
-- ==============================================================================


-- ==============================================================================
-- §1  TUI DATA INJECTION POINT
-- The TUI populates `tui_workspace_data` and the engine loop below applies it.
-- Do not move or rename this table; the TUI depends on its exact location.
-- ==============================================================================
local tui_workspace_data = {
    -- The TUI will generate and insert rule tables here.
    -- Each entry is a table accepted by hl.workspace_rule().
    --
    -- Full list of supported keys (all optional except `workspace`):
    --
    --   workspace       (string)  -- REQUIRED. Identifier or selector (see above).
    --   monitor         (string)  -- Bind to monitor by name ("DP-1") or desc ("desc:...").
    --   default         (bool)    -- Make this the default workspace on its monitor.
    --   persistent      (bool)    -- Keep workspace alive even when empty.
    --   default_name    (string)  -- Human-readable display name (shown in bars, etc.).
    --   on_created_empty(string)  -- Shell command to run when workspace is first created
    --                             --   empty. Supports window rule flags, e.g. "[float] app".
    --   layout          (string)  -- Override layout: "dwindle" | "master" | "scrolling"
    --                             --   | "lua:<name>" (for custom Lua layouts).
    --   layout_opts     (table)   -- Layout-specific options (see §8 for details).
    --   gaps_in         (number)  -- Inner gap override (px). Accepts a single number or
    --                             --   a table {top, bottom, left, right}.
    --   gaps_out        (number)  -- Outer gap override (px). Same format as gaps_in.
    --   no_border       (bool)    -- Disable all window borders on this workspace.
    --   border_size     (number)  -- Override border thickness (px).
    --   no_rounding     (bool)    -- Disable corner rounding on this workspace.
    --   decorate        (bool)    -- Enable/disable decorations (shadows, etc.).
    --   animation       (string)  -- Override workspace switch animation style.
    --                             --   Values: "slide" | "slidevert" | "fade"
    --                             --           | "slidefade" | "slidefadevert"
    --                             --   Append a percentage for slidefade, e.g. "slidefade 20%"
    --
    -- EXAMPLES (uncomment to activate):
    --
    -- [Monitor Binding & Persistence]
    -- { workspace = "1", monitor = "DP-1",   default = true, persistent = true },
    -- { workspace = "2", monitor = "DP-1",   persistent = true },
    -- { workspace = "3", monitor = "eDP-1",  persistent = true },
    -- { workspace = "4", monitor = "desc:Dell Inc. U2722D 1234", default = true },
    --
    -- [Named Workspaces with Launch Commands]
    -- { workspace = "name:coding",  monitor = "DP-1",  on_created_empty = "kitty",
    --   gaps_in = 0, gaps_out = 0, no_border = true, no_rounding = true, decorate = false },
    -- { workspace = "name:browser", monitor = "DP-2",  on_created_empty = "firefox" },
    -- { workspace = "name:chat",    on_created_empty = "vesktop" },
    --
    -- [Special / Scratchpad Workspaces]
    -- { workspace = "special:scratchpad", on_created_empty = "kitty" },
    -- { workspace = "special:browser",    on_created_empty = "firefox", layout = "scrolling" },
    --
    -- [Aesthetic Overrides]
    -- { workspace = "8",  border_size = 8, animation = "slidevert", default_name = "visuals" },
    -- { workspace = "10", no_border = true, no_rounding = true },
    --
    -- [Layout Overrides — see §8 for full layout_opts reference]
    -- { workspace = "2", layout = "master",    layout_opts = { orientation = "top" } },
    -- { workspace = "3", layout = "scrolling", layout_opts = { direction = "right" } },
}

-- Engine Loop: Applies all TUI-generated rules to Hyprland.
for _, rule in ipairs(tui_workspace_data) do
    hl.workspace_rule(rule)
end


-- ==============================================================================
-- §2  DYNAMIC WORKSPACE GENERATOR (1-10)
-- Toggle `enforce_persistent_1_to_10` to keep workspaces 1-10 always alive,
-- even when all their windows are closed.
-- Useful for status bars that display a fixed set of workspace indicators.
-- ==============================================================================
local enforce_persistent_1_to_10 = false

if enforce_persistent_1_to_10 then
    for i = 1, 10 do
        hl.workspace_rule({
            workspace  = tostring(i),
            persistent = true,
        })
    end
end

-- Optional: Give each workspace a static display name.
-- The TUI can toggle this independently from persistence above.
local enforce_default_names_1_to_10 = false

local default_workspace_names = {
    [1]  = "I",
    [2]  = "II",
    [3]  = "III",
    [4]  = "IV",
    [5]  = "V",
    [6]  = "VI",
    [7]  = "VII",
    [8]  = "VIII",
    [9]  = "IX",
    [10] = "X",
}

if enforce_default_names_1_to_10 then
    for i = 1, 10 do
        hl.workspace_rule({
            workspace    = tostring(i),
            default_name = default_workspace_names[i],
        })
    end
end



-- -- ==============================================================================
-- -- §3  SMART GAPS  ("no gaps when only")
-- -- Removes gaps and borders when exactly one tiled window is on screen,
-- -- or when a window is in fullscreen/maximized state.
-- -- Replicates the popular "smartgaps" feature from other WMs.
-- --
-- -- Selector reference used here:
-- --   w[tv1]   → workspace with exactly 1 visible tiled window
-- --   f[1]     → workspace where a window is maximized
-- --   s[false] → exclude special/scratchpad workspaces
--
-- --  this section is in appearance.lua
--
-- -- ==============================================================================
-- local enable_smart_gaps = false
-- 
-- if enable_smart_gaps then
--     -- Remove gaps when there is only one tiled window
--     hl.workspace_rule({ workspace = "w[tv1]s[false]", gaps_out = 0, gaps_in = 0 })
--     -- Remove gaps when a window is maximized
--     hl.workspace_rule({ workspace = "f[1]s[false]",   gaps_out = 0, gaps_in = 0 })
-- 
--     -- Also remove borders and rounding so the window fills the screen cleanly
--     hl.window_rule({ match = { float = false, workspace = "w[tv1]s[false]" }, border_size = 0, rounding = 0 })
--     hl.window_rule({ match = { float = false, workspace = "f[1]s[false]"   }, border_size = 0, rounding = 0 })
-- end


-- ==============================================================================
-- §4  SPECIAL WORKSPACES / SCRATCHPADS
-- Special workspaces float over any monitor and can be toggled on/off.
-- They are identified by the "special:" prefix.
-- Toggle them with: hl.dsp.workspace.toggle_special({ name = "scratchpad" })
--
-- Notes:
--   • Each monitor gets its own independent instance of a special workspace.
--   • `misc.close_special_on_empty` (§10e) controls auto-close behaviour.
--   • `on_created_empty` launches an app the first time the workspace is shown.
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
-- You can navigate to them with: hl.dsp.workspace.name("coding")
-- ==============================================================================
local named_workspaces = {
    -- { name = "coding",  monitor = "DP-1",  on_created_empty = "kitty",
    --   gaps_in = 0, gaps_out = 0, no_border = true, no_rounding = true, decorate = false },
    -- { name = "browser", monitor = "DP-2",  on_created_empty = "firefox" },
    -- { name = "gaming",  monitor = "desc:Chimei Innolux Corporation 0x150C",
    --   no_border = true, no_rounding = true, decorate = false,
    --   layout = "scrolling" },
    -- { name = "chat",    on_created_empty = "vesktop" },
    -- { name = "music",   on_created_empty = "spotify" },
    -- { name = "virt",    on_created_empty = "virt-manager" },
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
--
-- Monitor name formats:
--   "DP-1"                            → connector name (use `hyprctl monitors`)
--   "eDP-1"                           → built-in laptop display
--   "desc:Dell Inc. U2722D ABCD1234"  → description-based (survives cable swaps)
-- ==============================================================================

-- Set `true` to activate, then fill in the monitor_bindings table below.
local enable_monitor_bindings = false

local monitor_bindings = {
    -- { workspace = "1",  monitor = "DP-1",   default = true,  persistent = true },
    -- { workspace = "2",  monitor = "DP-1",   persistent = true },
    -- { workspace = "3",  monitor = "DP-1",   persistent = true },
    -- { workspace = "4",  monitor = "DP-1",   persistent = true },
    -- { workspace = "5",  monitor = "DP-1",   persistent = true },
    -- { workspace = "6",  monitor = "eDP-1",  default = true,  persistent = true },
    -- { workspace = "7",  monitor = "eDP-1",  persistent = true },
    -- { workspace = "8",  monitor = "eDP-1",  persistent = true },
    -- { workspace = "9",  monitor = "eDP-1",  persistent = true },
    -- { workspace = "10", monitor = "eDP-1",  persistent = true },
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
--
-- Available aesthetic keys:
--   gaps_in      (number | {top,bottom,left,right})  Inner gap override
--   gaps_out     (number | {top,bottom,left,right})  Outer gap override
--   border_size  (number)                            Border thickness in px
--   no_border    (bool)                              Disable borders entirely
--   no_rounding  (bool)                              Disable corner rounding
--   decorate     (bool)                              Toggle shadows / decorations
--   animation    (string)                            Animation style override
--                                                    "slide" | "slidevert" | "fade"
--                                                    "slidefade" | "slidefadevert"
--                                                    Add % for slidefade: "slidefade 20%"
--   default_name (string)                            Display name for bars / TUIs
-- ==============================================================================
local aesthetic_overrides = {
    -- Completely clean workspace — no distractions
    -- { workspace = "1",  gaps_in = 0, gaps_out = 0, no_border = true,
    --   no_rounding = true, decorate = false },

    -- Thick decorative border + vertical slide animation
    -- { workspace = "8",  border_size = 8, animation = "slidevert",
    --   default_name = "visuals" },

    -- Workspace 9 fades in/out (good for media/music workspaces)
    -- { workspace = "9",  animation = "fade" },

    -- Workspace 10 with custom inner/outer gaps per-side
    -- { workspace = "10", gaps_in = { top = 4, bottom = 4, left = 8, right = 8 },
    --   gaps_out = { top = 16, bottom = 16, left = 12, right = 12 } },
}

for _, override in ipairs(aesthetic_overrides) do
    hl.workspace_rule(override)
end


-- ==============================================================================
-- §8  PER-WORKSPACE LAYOUT OVERRIDES
-- Override the tiling layout on a per-workspace basis.
-- The global default layout is set in §10a.
--
-- Valid layout values:
--   "dwindle"     → Fibonacci/dwindle tiling (default in most configs)
--   "master"      → Master-stack layout
--   "scrolling"   → Scrolling tape layout (new in 0.54/0.55)
--   "lua:<name>"  → Custom layout defined with hl.layout.register() (new in 0.55)
--
-- layout_opts for "master":
--   orientation  (string)  "left" (default) | "right" | "top" | "bottom" | "center"
--
-- layout_opts for "scrolling":
--   direction    (string)  "right" (default) | "left" | "up" | "down"
-- ==============================================================================
local layout_overrides = {
    -- Master layout, master pane on top (horizontal split)
    -- { workspace = "2", layout = "master", layout_opts = { orientation = "top" } },

    -- Master layout, centered master pane
    -- { workspace = "3", layout = "master", layout_opts = { orientation = "center" } },

    -- Scrolling layout, tape grows to the right (default)
    -- { workspace = "4", layout = "scrolling", layout_opts = { direction = "right" } },

    -- Scrolling layout, tape grows downward
    -- { workspace = "5", layout = "scrolling", layout_opts = { direction = "down" } },

    -- Custom Lua layout (must be registered with hl.layout.register() elsewhere)
    -- { workspace = "6", layout = "lua:columns" },
}

for _, override in ipairs(layout_overrides) do
    hl.workspace_rule(override)
end


-- ==============================================================================
-- §9  RANGE RULES (Global Fallbacks)
-- Apply rules to a range of workspaces using the "r[X-Y]" selector.
-- These are evaluated for all workspaces in the range that EXIST at the time.
--
-- Common use cases:
--   • Set a different default layout for "overflow" workspaces (11+)
--   • Apply consistent gaps/aesthetics to a block of workspaces
--   • Mark a range as persistent
-- ==============================================================================
local enforce_global_fallbacks = false

if enforce_global_fallbacks then
    -- Example: workspaces 11–99 use scrolling layout by default
    hl.workspace_rule({
        workspace = "r[11-99]",
        layout    = "scrolling",
    })
end

-- Optional fine-grained range examples (toggle independently):

-- Make workspaces 1–5 use dwindle (left half of a dual-monitor spread)
local range_left_monitor = false
if range_left_monitor then
    hl.workspace_rule({ workspace = "r[1-5]",  layout = "dwindle" })
end

-- Make workspaces 6–10 use master (right half of a dual-monitor spread)
local range_right_monitor = false
if range_right_monitor then
    hl.workspace_rule({ workspace = "r[6-10]", layout = "master" })
end


-- ==============================================================================
-- §10  GLOBAL WORKSPACE BEHAVIOUR
-- hl.config() calls that affect workspace/window behaviour globally.
-- Multiple hl.config() calls are allowed; each one merges with existing config.
-- ==============================================================================


-- ----------------------------------------------------------------------------
-- §10a  General — global layout
-- (Note: gaps_in, gaps_out, and gaps_workspaces are configured visually in appearance.lua)
-- layout   : global default layout. "dwindle" | "master" | "scrolling" | "lua:*"
-- ----------------------------------------------------------------------------
hl.config({
    general = {
        layout          = "dwindle",  -- global default tiling layout
    },
})


-- ----------------------------------------------------------------------------
-- §10b  Dwindle layout
-- preserve_split : keep the split direction when toggling — KEEP THIS TRUE
--                  or the "toggle split" keybind will not behave as expected.
-- smart_split    : (bool) if true, splits based on window size rather than count.
-- smart_resizing : (bool) resize the side that is smaller rather than both.
-- force_split    : 0 = last window direction, 1 = always right/down, 2 = always left/up
-- pseudotile     : REMOVED in 0.55. Do not use.
-- ----------------------------------------------------------------------------
hl.config({
    dwindle = {
        preserve_split  = true,   -- required for toggle_split keybind to work
        smart_split     = false,  -- split based on window dimensions instead of count
        smart_resizing  = true,   -- resize the smaller side on manual resize
        force_split     = 0,      -- 0: last, 1: always right/down, 2: always left/up
    },
})


-- ----------------------------------------------------------------------------
-- §10c  Master layout
-- new_status        : "master" | "slave" | "inherit" — where new windows go.
--                     "slave"   = all new windows go to the slave stack (default)
--                     "master"  = new windows always become the master
--                     "inherit" = new window inherits the status of the focused one
-- new_on_top        : (bool) insert new slave windows at the TOP of the stack.
-- mfact             : (float 0.0-1.0) fraction of the screen the master pane takes.
-- orientation       : "left" | "right" | "top" | "bottom" | "center"
--                     Controls which side of the screen the master pane occupies.
--                     Use "center" for a centered master with stacks on both sides.
-- allow_small_split : (bool) allow adding extra master windows in horizontal-split
--                     style when there are multiple masters.
-- special_scale_factor : (float 0.0-1.0) scale factor for windows on special
--                        (scratchpad) workspaces when using master layout.
--
-- NOTE: always_center_master and inherit_fullscreen do NOT exist in stock
-- Hyprland. They originated in third-party plugins (hyprNStack etc.).
-- Per-workspace centering is set via layout_opts = { orientation = "center" }
-- in hl.workspace_rule() — see §8.
-- ----------------------------------------------------------------------------
hl.config({
    master = {
        new_status           = "slave",  -- new windows go into the slave stack
        new_on_top           = false,    -- append to bottom of slave stack
        mfact                = 0.55,     -- master pane takes 55% of the screen
        orientation          = "left",   -- master pane on the left
        allow_small_split    = false,    -- extra horizontal master splits
        special_scale_factor = 0.8,     -- scale of windows in special workspaces
    },
})


-- ----------------------------------------------------------------------------
-- §10d  Scrolling layout
-- Introduced in Hyprland 0.54, refined in 0.55.
--
-- fullscreen_on_one_column : (bool) when a workspace has only one column, treat
--                            that column as fullscreen (window fills monitor).
--                            Defaults to false.
--
-- IMPORTANT — keys that do NOT exist as global scrolling config:
--   • column_default_width  → set per-window via hl.window_rule scrolling_width
--   • reorder               → not a config option
--   • direction             → per-workspace only, via layout_opts = { direction = "right" }
--                             in hl.workspace_rule() — see §8
-- ----------------------------------------------------------------------------
hl.config({
    scrolling = {
        fullscreen_on_one_column = false,  -- single-column workspace fills screen
    },
})


-- ----------------------------------------------------------------------------
-- §10e  Misc — special workspace & focus behaviour
-- close_special_on_empty   : (bool) auto-close special workspace when last
--                            window in it is closed.
-- focus_on_activate        : (bool) focus a window that requests activation
--                            (e.g. urgency hint / xdg_activation).
-- on_focus_under_fullscreen : behaviour when a window is focused while another
--                             is fullscreen on the same workspace.
--                             0 = do nothing (new window stays behind)
--                             1 = new window takes over (unfullscreens current)
--                             2 = unfullscreen current, then fullscreen the new one
--
-- NOTE: this key was previously called new_window_takes_over_fullscreen and was
-- renamed to on_focus_under_fullscreen. The old name errors on 0.55+.
-- ----------------------------------------------------------------------------
hl.config({
    misc = {
        close_special_on_empty    = true,   -- clean up empty scratchpads
        focus_on_activate         = true,  -- steal focus on activation
        on_focus_under_fullscreen = 2,      -- 0 = stay behind | 1 = take over | 2 = swap fs
    },
})


-- ----------------------------------------------------------------------------
-- §10f  Binds — workspace navigation behaviour
-- workspace_back_and_forth     : (bool) re-dispatching to the active workspace
--                                switches back to the previously active one.
-- allow_workspace_cycles       : (bool) cycling past workspace 1 wraps to the
--                                highest-numbered, and vice versa.
-- workspace_center_on          : 0 = cursor stays, 1 = cursor moves to center of
--                                the new workspace's focused window,
--                                2 = cursor moves to center of the monitor.
-- hide_special_on_workspace_change : (bool) hide open special workspaces when
--                                    you switch to a different normal workspace.
-- movefocus_cycles_fullscreen  : (bool) movefocus wraps around into/out of
--                                fullscreen windows.
-- window_direction_monitor_fallback : (bool) moving a window past the edge of a
--                                    monitor moves it to the adjacent monitor.
-- ----------------------------------------------------------------------------
hl.config({
    binds = {
        workspace_back_and_forth          = false, -- toggle back on re-dispatch
        allow_workspace_cycles            = false, -- wrap around at ends
        workspace_center_on               = 0,     -- 0 = cursor stays in place
        hide_special_on_workspace_change  = false, -- keep scratchpad visible on switch
        movefocus_cycles_fullscreen       = true,  -- movefocus wraps around fullscreen
        window_direction_monitor_fallback = true,  -- cross-monitor window movement
    },
})
EOF
            ;;

        # ======================================================================
        "environment_variables.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: environment_variables.lua
-- ==============================================================================
-- Add your custom environment variables here.
-- These will override or add to the defaults found in:
--   ~/.config/hypr/source/environment_variables.lua
--
-- NOTE: It is strongly recommended to place environment variables in the
-- UWSM files at ~/.config/uwsm/{env,env-hyprland} instead, as those are
-- sourced before Hyprland starts and apply to the full session.
--
-- Syntax:
--   hl.env("XCURSOR_SIZE",    "24")
--   hl.env("HYPRCURSOR_SIZE", "24")
--
-- See: https://wiki.hypr.land/Configuring/Advanced-and-Cool/Environment-variables/
-- ==============================================================================

EOF
            ;;

        # ======================================================================
        "input.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: input.lua
-- ==============================================================================
-- Add your custom input settings here.
-- These will override or add to the defaults found in ~/.config/hypr/source/
-- This file can also be managed with dusky input from the rofi menu or
-- from dusky control center.

-- See: https://wiki.hypr.land/Configuring/Basics/Variables/
-- See: https://wiki.hypr.land/Configuring/Advanced-and-Cool/Devices/
-- -------------------------------------------------------------------------------------------------
-- 1. INPUT (KEYBOARD, MOUSE, TOUCHPAD, TABLET, VIRTUAL KEYBOARD)
-- -------------------------------------------------------------------------------------------------
hl.config({
    input = {
        -- --- Keyboard ---
        kb_model = "",                   -- Appropriate XKB keymap parameter.
        kb_layout = "us",                -- Appropriate XKB keymap parameter.
        kb_variant = "",                 -- Appropriate XKB keymap parameter.
        kb_options = "",                 -- Appropriate XKB keymap parameter.
        kb_rules = "",                   -- Appropriate XKB keymap parameter.
        kb_file = "",                    -- If you prefer, you can use a path to your custom .xkb file.
        numlock_by_default = false,      -- Engage numlock by default.
        resolve_binds_by_sym = false,    -- Determines how keybinds act when multiple layouts are used.
        repeat_rate = 35,                -- The repeat rate for held-down keys, in repeats per second.
        repeat_delay = 250,              -- Delay before a held-down key is repeated, in milliseconds.

        -- --- Mouse & Pointer ---
        sensitivity = 0.0,               -- Sets the mouse input sensitivity. Value is clamped to the range -1.0 to 1.0.
        accel_profile = "adaptive",      -- Sets the cursor acceleration profile. Can be one of adaptive, flat, or custom.
        force_no_accel = false,          -- Force no cursor acceleration. Bypasses most pointer settings to get a raw signal.
        rotation = 0,                    -- Sets the rotation of a device in degrees clockwise off the logical neutral position.
        left_handed = false,             -- Switches RMB and LMB.

        -- --- Scrolling ---
        scroll_points = "",              -- Sets the scroll acceleration profile, when accel_profile is set to custom.
        scroll_method = "2fg",           -- Sets the scroll method. Can be one of 2fg, edge, on_button_down, no_scroll.
        scroll_button = 0,               -- Sets the scroll button. 0 means default.
        scroll_button_lock = false,      -- Toggles the button lock logically holding it down to convert motion to scroll events.
        scroll_factor = 1.0,             -- Multiplier added to scroll movement for external mice.
        natural_scroll = false,          -- Inverts scrolling direction. Scrolling moves content directly.
        emulate_discrete_scroll = 1,     -- Emulates discrete scrolling from high resolution scrolling events (0: off, 1: non-standard, 2: all).

        -- --- Focus & Interaction Behavior ---
        follow_mouse = 1,                -- Specify if and how cursor movement should affect window focus.
        follow_mouse_shrink = 0,         -- Shrinks the inactive window hitboxes used for focus detection by pixels.
        follow_mouse_threshold = 0.0,    -- Smallest distance in logical pixels the mouse needs to travel to focus a window.
        focus_on_close = 0,              -- Controls window focus behavior when a window is closed (0: next, 1: under cursor, 2: recent).
        mouse_refocus = true,            -- If disabled, mouse focus won't switch unless crossing a window boundary when follow_mouse=1.
        float_switch_override_focus = 1, -- Focus changes to window under cursor when changing tiled-to-floating and vice versa.
        special_fallthrough = false,     -- Having only floating windows in special workspace will not block focusing in regular workspace.
        off_window_axis_events = 1,      -- Handles axis events around a focused window (0: ignores, 1: out-of-bounds, 2: fakes, 3: warps).

        -- --- Touchpad (Subcategory of Input) ---
        touchpad = {
            disable_while_typing = true,     -- Disable the touchpad while typing.
            natural_scroll = true,           -- Inverts scrolling direction. Scrolling moves content directly.
            scroll_factor = 1.0,             -- Multiplier applied to the amount of scroll movement.
            middle_button_emulation = false, -- Sending LMB and RMB simultaneously will be interpreted as a middle click.
            tap_button_map = "",             -- Sets the tap button mapping for touchpad button emulation (lrm or lmr).
            clickfinger_behavior = false,    -- Button presses with 1, 2, or 3 fingers will be mapped to LMB, RMB, and MMB respectively.
            tap_to_click = true,             -- Tapping on the touchpad with 1, 2, or 3 fingers will send LMB, RMB, and MMB respectively.
            drag_lock = 0,                   -- Lifting the finger off while dragging will not drop item (0: disabled, 1: timeout, 2: sticky).
            tap_and_drag = true,             -- Sets the tap and drag mode for the touchpad.
            flip_x = false,                  -- Inverts the horizontal movement of the touchpad.
            flip_y = false,                  -- Inverts the vertical movement of the touchpad.
            drag_3fg = 0                     -- Enables three finger drag (0: disabled, 1: 3 fingers, 2: 4 fingers).
        },

        -- --- Touchdevice (Subcategory of Input) ---
        touchdevice = {
            transform = -1,                  -- Transform the input from touchdevices. -1 means it’s unset.
            output = "[[Auto]]",             -- The monitor to bind touch devices. The default is auto-detection.
            enabled = true                   -- Whether input is enabled for touch devices.
        },

        -- --- Tablet (Subcategory of Input) ---
        tablet = {
            transform = -1,                  -- Transform the input from tablets. -1 means it’s unset.
            output = "",                     -- The monitor to bind tablets. Leave empty to map across all monitors.
            region_position = { 0, 0 },      -- Position of the mapped region in monitor layout relative to top left.
            absolute_region_position = false,-- Whether to treat the region_position as an absolute position in monitor layout.
            region_size = { 0, 0 },          -- Size of the mapped region.
            relative_input = false,          -- Whether the input should be relative.
            left_handed = false,             -- If enabled, the tablet will be rotated 180 degrees.
            active_area_size = { 0, 0 },     -- Size of tablet’s active area in mm.
            active_area_position = { 0, 0 }  -- Position of the active area in mm.
        },

        -- --- Virtual Keyboard (Subcategory of Input) ---
        virtualkeyboard = {
            share_states = 2,                -- Unify key down states and modifier states with other keyboards.
            release_pressed_on_close = false -- Release all pressed keys by virtual keyboard on close.
        }
    },

    -- ---------------------------------------------------------------------------------------------
    -- 2. CURSOR BEHAVIOR & RENDERING
    -- ---------------------------------------------------------------------------------------------
    cursor = {
        invisible = false,                   -- Don’t render cursors.
        sync_gsettings_theme = true,         -- Sync xcursor theme with gsettings.
        no_hardware_cursors = 2,             -- Disables hardware cursors. 0: use hw, 1: don't use hw, 2: auto.
        no_break_fs_vrr = 2,                 -- Disables scheduling new frames on cursor movement for fullscreen apps with VRR enabled.
        min_refresh_rate = 24,               -- Minimum refresh rate for cursor movement when no_break_fs_vrr is active.
        hotspot_padding = 1,                 -- The padding, in logical px, between screen edges and the cursor.
        inactive_timeout = 0.0,              -- In seconds, after how many seconds of cursor’s inactivity to hide it.
        no_warps = false,                    -- If true, will not warp the cursor in many cases (focusing, keybinds, etc).
        persistent_warps = false,            -- Cursor returns to its last position relative to that window, rather than to the centre.
        warp_on_change_workspace = 0,        -- Move the cursor to the last focused window after changing the workspace.
        warp_on_toggle_special = 0,          -- Move the cursor to the last focused window when toggling a special workspace.
        default_monitor = "[[EMPTY]]",       -- The name of a default monitor for the cursor to be set to on startup.
        zoom_factor = 1.0,                   -- The factor to zoom by around the cursor. Minimum 1.0.
        zoom_rigid = false,                  -- Whether the zoom should follow the cursor rigidly or loosely.
        zoom_detached_camera = true,         -- Detach the camera from the mouse when zoomed in, only ever moving to keep mouse in view.
        enable_hyprcursor = true,            -- Whether to enable hyprcursor support.
        hide_on_key_press = false,           -- Hides the cursor when you press any key until the mouse is moved.
        hide_on_touch = true,                -- Hides the cursor when the last input was a touch input until a mouse input is done.
        hide_on_tablet = true,               -- Hides the cursor when the last input was a tablet input until a mouse input is done.
        use_cpu_buffer = 2,                  -- Makes HW cursors use a CPU buffer. Required on Nvidia to have HW cursors.
        warp_back_after_non_mouse_input = false, -- Warp the cursor back to where it was after using a non-mouse input.
        zoom_disable_aa = false              -- Disable antialiasing when zooming, which means things will be pixelated.
    },

    -- ---------------------------------------------------------------------------------------------
    -- 3. GESTURE PHYSICS (Tuning)
    -- ---------------------------------------------------------------------------------------------
    gestures = {
        workspace_swipe_distance = 300,              -- In px, the distance of the touchpad gesture.
        workspace_swipe_touch = false,               -- Enable workspace swiping from the edge of a touchscreen.
        workspace_swipe_invert = true,               -- Invert the direction (touchpad only).
        workspace_swipe_touch_invert = false,        -- Invert the direction (touchscreen only).
        workspace_swipe_min_speed_to_force = 30,     -- Minimum speed in px per timepoint to force the change ignoring cancel_ratio.
        workspace_swipe_cancel_ratio = 0.5,          -- How much the swipe has to proceed in order to commence it.
        workspace_swipe_create_new = true,           -- Whether a swipe right on the last workspace should create a new one.
        workspace_swipe_direction_lock = true,       -- If enabled, switching direction will be locked when you swipe past the threshold.
        workspace_swipe_direction_lock_threshold = 10, -- In px, the distance to swipe before direction lock activates (touchpad only).
        workspace_swipe_forever = false,             -- If enabled, swiping will not clamp at the neighboring workspaces but continue.
        workspace_swipe_use_r = false,               -- If enabled, swiping will use the r prefix instead of the m prefix for finding workspaces.
        close_max_timeout = 1000                     -- The timeout for a window to close when using a 1:1 gesture, in ms.
    },

    -- ---------------------------------------------------------------------------------------------
    -- 4. NEW GESTURE BINDINGS (0.55+ Overhaul)
    -- ---------------------------------------------------------------------------------------------
    gesture = {
        -- --- 3-Finger Gestures (Navigation) ---
        
        -- Replicates native 1:1 smooth swiping between workspaces (Highly Intuitive)
        "3, horizontal, workspace",
        
        -- Swipe up for Overview / Mission Control (hyprexpo)
        "3, up, hyprexpo:expo, toggle",
        
        -- Swipe down to drop into a Special Workspace (Scratchpad/Terminal)
        "3, down, togglespecialworkspace",

        -- --- 4-Finger Gestures (Media & Brightness) ---
        
        -- Horizontal for Brightness
        "4, left, exec, brightnessctl -e4 -n2 set 10%-",
        "4, right, exec, brightnessctl -e4 -n2 set 10%+",

        -- Vertical for Volume
        "4, up, exec, wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ 10%+",
        "4, down, exec, wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%-"
    }
})
EOF
;;

        # ======================================================================
        "trackpad.lua")
            cat <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION: trackpad.lua
-- ==============================================================================
-- Add your custom trackpad gesture settings here.
-- -------------------------------------------------------------------------------------------------
-- TRACKPAD GESTURES
-- -------------------------------------------------------------------------------------------------
-- NOTE: Gestures fire once per recognized swipe, not continuously.
--       Volume/brightness step is 5% per swipe — do multiple quick swipes for larger changes.
--       Tap gestures are not supported by Hyprland natively (as of 0.55).
--       For your 3-finger tap QuickPanel: use ALT+V (already bound in keybinds).

-- ── 3-Finger Gestures ────────────────────────────────────────────────────────────────────────────

-- Left/Right: Native 1:1 smooth workspace switching (no plugin needed)
hl.gesture({
    fingers   = 3,
    direction = "horizontal",
    action    = "workspace",
})

hl.gesture({
    fingers   = 3,
    direction = "up",
    action    = function()
        hl.exec_cmd("notify-send 'overview coming soong'")
    end,
})

-- Down: Toggle media pause/play
hl.gesture({
    fingers   = 3,
    direction = "down",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --play-pause")
    end,
})

-- ── 4-Finger Gestures ────────────────────────────────────────────────────────────────────────────

-- Left/Right: Volume control (5% per swipe, capped at 150% to prevent distortion)
hl.gesture({
    fingers   = 4,
    direction = "left",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-down 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "right",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-up 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "up",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-up 10")
    end,
})

hl.gesture({
    fingers   = 4,
    direction = "down",
    action    = function()
        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-down 10")
    end,
})



-- -------------------------------------------------------------------------------------------------
-- GESTURE PHYSICS  (controls feel of the workspace swipe gesture, not gesture definitions)
-- -------------------------------------------------------------------------------------------------
hl.config({
    gestures = {
        workspace_swipe_distance           = 300,  -- Max swipe travel distance in px.
        workspace_swipe_invert             = true, -- Invert swipe direction.
        workspace_swipe_min_speed_to_force = 30,   -- Min px/timepoint speed to force workspace change (0 = disable).
        workspace_swipe_cancel_ratio       = 0.5,  -- Fraction of distance needed to commit (0.0–1.0).
        workspace_swipe_create_new         = true, -- Create a new workspace when swiping past the last one.
        workspace_swipe_direction_lock     = true, -- Lock swipe axis after passing direction threshold.
        workspace_swipe_direction_lock_threshold = 10, -- Distance in px before direction lock engages.
        workspace_swipe_forever            = false, -- Allow swiping past neighbouring workspaces without stopping.
        workspace_swipe_use_r              = false, -- Use 'r' prefix (relative) instead of 'm' prefix for workspaces.
        close_max_timeout                  = 1000, -- Max ms a 1:1 gesture window has to close, in ms.
    },
})
EOF
            ;;

        # ======================================================================
        *)
            # Fallback for any future files added to CONFIG_FILES
            printf '-- ==============================================================================\n'
            printf '-- USER CONFIGURATION: %s\n' "${filename}"
            printf '-- ==============================================================================\n'
            printf '-- Add your custom settings here.\n'
            printf '-- ==============================================================================\n\n'
            ;;
    esac
}

# ------------------------------------------------------------------------------
# 3. Privilege & Pre-flight Checks
# ------------------------------------------------------------------------------
if [[ "${EUID}" -eq 0 ]]; then
    log_error "This script must NOT be run as root."
    log_error "It modifies user configuration files in ${HOME}."
    exit 1
fi

# Ensure base directory structure exists FIRST
if [[ ! -d "${HYPR_DIR}" ]]; then
    log_info "Creating Hyprland config directory: ${HYPR_DIR}"
    mkdir -p -- "${HYPR_DIR}"
fi

if [[ ! -f "${MAIN_CONF}" ]]; then
    log_warn "Main Hyprland config not found at ${MAIN_CONF}."
    log_warn "Creating empty file. You will need to populate it with your base config."
    touch -- "${MAIN_CONF}"
fi

# ------------------------------------------------------------------------------
# 4. Handle Arguments
# ------------------------------------------------------------------------------
force_mode=false
declare -a target_files=()

while [[ $# -gt 0 ]]; do
    case "${1}" in
        --force)
            force_mode=true
            shift
            ;;
        --*)
            # Dynamic flag parsing based on CONFIG_FILES.
            # E.g. --monitors matches "monitors.lua" or "monitors"
            flag_name="${1#--}" 
            matched_file=""
            for file in "${CONFIG_FILES[@]}"; do
                if [[ "${file%.lua}" == "${flag_name}" || "${file}" == "${flag_name}" ]]; then
                    matched_file="${file}"
                    break
                fi
            done

            if [[ -n "${matched_file}" ]]; then
                target_files+=("${matched_file}")
            else
                log_error "Unknown flag or unsupported file: ${1}"
                log_info "Available file flags are (based on CONFIG_FILES):"
                for file in "${CONFIG_FILES[@]}"; do
                    log_info "  --${file%.lua}"
                done
                log_error "Usage: ${0##*/} [--force] [--<filename> ...]"
                exit 1
            fi
            shift
            ;;
        *)
            log_error "Unknown argument: ${1}"
            log_error "Usage: ${0##*/} [--force] [--<filename> ...]"
            exit 1
            ;;
    esac
done

# Determine if we are deploying everything or just selective files
all_files_targeted=false
if [[ ${#target_files[@]} -eq 0 ]]; then
    # No specific flags passed, deploy everything
    target_files=("${CONFIG_FILES[@]}")
    all_files_targeted=true
else
    # Remove duplicates if user accidentally typed the same flag twice
    readarray -t target_files < <(printf '%s\n' "${target_files[@]}" | sort -u)
fi

# Execute Force Mode backups
if [[ "${force_mode}" == true ]]; then
    # Bash 5.0+ builtin timestamp (no external 'date' command needed)
    printf -v backup_timestamp '%(%Y%m%d_%H%M%S)T' -1

    if [[ "${all_files_targeted}" == true && -d "${EDIT_DIR}" ]]; then
        # Traditional force mode: backup the entire directory
        backup_name="edit_here.bak_${backup_timestamp}"
        log_warn "Force mode (All Files): Backing up '${EDIT_DIR}' to '${HYPR_DIR}/${backup_name}'..."
        mv -- "${EDIT_DIR}" "${HYPR_DIR}/${backup_name}"
        log_success "Backup complete. Proceeding with clean regeneration."
    elif [[ "${all_files_targeted}" == false ]]; then
        # Targeted force mode: backup ONLY the specified files
        log_warn "Force mode (Targeted Files): Backing up specified files..."
        
        target_backup_dir="${EDIT_SOURCE_DIR}/backups"
        mkdir -p -- "${target_backup_dir}"
        
        for file in "${target_files[@]}"; do
            target_path="${EDIT_SOURCE_DIR}/${file}"
            if [[ -f "${target_path}" ]]; then
                backup_name="${file}.bak_${backup_timestamp}"
                mv -- "${target_path}" "${target_backup_dir}/${backup_name}"
                log_success "  - Backed up: ${file} -> backups/${backup_name}"
            fi
        done
    fi
fi

# ------------------------------------------------------------------------------
# 5. Main Logic: Create or Verify Overlay
# ------------------------------------------------------------------------------
log_info "Initializing/Verifying Hyprland user configuration overlay..."

# Ensure directory structure exists
if [[ ! -d "${EDIT_SOURCE_DIR}" ]]; then
    log_info "Creating directory: ${EDIT_SOURCE_DIR}"
    mkdir -p -- "${EDIT_SOURCE_DIR}"
else
    log_info "Directory exists: ${EDIT_SOURCE_DIR} (verifying contents...)"
fi

# Iterate and create missing files using the target_files array
for file in "${target_files[@]}"; do
    target_file="${EDIT_SOURCE_DIR}/${file}"

    if [[ -f "${target_file}" ]]; then
        log_info "  - Exists: ${file}"
    else
        log_warn "  - Missing: ${file} -> Creating with default template..."
        get_file_content "${file}" > "${target_file}"
        log_success "    Created: ${file}"
    fi
done

# Generate or update the user overlay loader: edit_here/hyprland.lua
if [[ -f "${NEW_CONF}" ]]; then
    log_info "Verifying loader file: ${NEW_CONF}"
    
    # "Healing pass": We ensure all targeted files are active in the loader.
    # We do NOT overwrite the whole file, preserving any manual edits to it.
    for file in "${target_files[@]}"; do
        if [[ "${file}" == "default_apps.lua" ]]; then
            continue
        fi
        
        module_name="${file%.lua}"
        
        if [[ -f "${EDIT_SOURCE_DIR}/${file}" ]]; then
            # If the require line exists but is commented out (e.g. -- require("edit_here.source.monitors"))
            if grep -Eq "^[[:space:]]*--[[:space:]]*(require\(\"edit_here\.source\.${module_name}\"\).*)" "${NEW_CONF}"; then
                # Strip the leading comment marker via sed to activate it
                sed -i -E "s/^[[:space:]]*--[[:space:]]*(require\(\"edit_here\.source\.${module_name}\"\).*)/\1/" "${NEW_CONF}"
                log_success "  - Activated ${file} in loader."
            
            # If the require line is completely missing
            elif ! grep -Fq "require(\"edit_here.source.${module_name}\")" "${NEW_CONF}"; then
                printf 'require("edit_here.source.%s")\n' "${module_name}" >> "${NEW_CONF}"
                log_success "  - Appended ${file} to loader."
            fi
        fi
    done
else
    log_warn "Loader file missing: ${NEW_CONF} -> Creating..."

    # Write header
    cat > "${NEW_CONF}" <<'EOF'
-- ==============================================================================
-- USER CONFIGURATION OVERLAY LOADER
-- ==============================================================================
-- This file is require()d at the bottom of hyprland.lua.
-- It loads all your custom configuration files from 'source/'.
-- Edit the specific files in 'source/' to apply your changes.
--
-- NOTE: 'default_apps.lua' is intentionally excluded here — it is require()d
-- directly at the top of hyprland.lua so its globals are available first.
-- ==============================================================================

EOF

    # Dynamically append require() lines (skip default_apps — handled separately)
    for file in "${CONFIG_FILES[@]}"; do
        if [[ "${file}" == "default_apps.lua" ]]; then
            continue
        fi
        
        module_name="${file%.lua}"
        
        # Guard: Only add active require() if the file actually exists, to prevent
        # Hyprland crashes on partial deployments (e.g. running just --monitors)
        if [[ -f "${EDIT_SOURCE_DIR}/${file}" ]]; then
            printf 'require("edit_here.source.%s")\n' "${module_name}" >> "${NEW_CONF}"
        else
            printf '-- require("edit_here.source.%s") -- File missing/not deployed yet\n' "${module_name}" >> "${NEW_CONF}"
        fi
    done

    log_success "Created loader: ${NEW_CONF}"
fi

# ------------------------------------------------------------------------------
# 6. Modify Main Configuration (hyprland.lua)
# ------------------------------------------------------------------------------
log_info "Verifying main configuration at '${MAIN_CONF}'..."

# A. Insert default_apps require() at the TOP of the file (priority — globals first)
#    Uses grep -Fq (fixed-string, quiet) to match the exact require() string.
if grep -Fq "${APPS_DEFAULTS_REQUIRE}" "${MAIN_CONF}"; then
    log_success "Main config already contains default_apps require()."
else
    # Robust prepend via temp file — handles empty files safely
    temp_file=$(mktemp)
    {
        printf '%s\n' "${APPS_DEFAULTS_REQUIRE}"
        cat "${MAIN_CONF}"
    } > "${temp_file}" && mv -- "${temp_file}" "${MAIN_CONF}"

    log_success "Prepended '${APPS_DEFAULTS_REQUIRE}' to the top of '${MAIN_CONF}'."
fi

# B. Insert overlay loader require() at the BOTTOM of the file (last override wins)
if grep -Fq "${OVERLAY_REQUIRE}" "${MAIN_CONF}"; then
    log_success "Main config already contains the overlay loader require()."
else
    printf '\n-- Source User Custom Config Overlay\n%s\n' "${OVERLAY_REQUIRE}" >> "${MAIN_CONF}"
    log_success "Appended '${OVERLAY_REQUIRE}' to '${MAIN_CONF}'."
fi

# ------------------------------------------------------------------------------
# 7. Completion
# ------------------------------------------------------------------------------
printf '\n'
log_success "Setup/Verification complete!"
log_info  "Your custom configs are located in: ${EDIT_DIR}"
log_info  "To apply changes, save any .lua file (auto-reload) or run 'hyprctl reload'."
