
HOME = os.getenv("HOME")

dusky_scripts = HOME .. "/user_scripts/"



-- Source: /home/dusk/Pictures/wallpapers/dusk_default.jpg

image = "/home/dusk/Pictures/wallpapers/dusk_default.jpg"


background = "rgba(0a1612ff)"

error = "rgba(ffb4abff)"

error_container = "rgba(93000aff)"

inverse_on_surface = "rgba(27332eff)"

inverse_primary = "rgba(006b56ff)"

inverse_surface = "rgba(d8e6dfff)"

on_background = "rgba(d8e6dfff)"

on_error = "rgba(690005ff)"

on_error_container = "rgba(ffdad6ff)"

on_primary = "rgba(00382cff)"

on_primary_container = "rgba(17ffd1ff)"

on_primary_fixed = "rgba(002018ff)"

on_primary_fixed_variant = "rgba(005140ff)"

on_secondary = "rgba(003734ff)"

on_secondary_container = "rgba(bcece6ff)"

on_secondary_fixed = "rgba(00201eff)"

on_secondary_fixed_variant = "rgba(1f4e4aff)"

on_surface = "rgba(d8e6dfff)"

on_surface_variant = "rgba(bccac3ff)"

on_tertiary = "rgba(00363bff)"

on_tertiary_container = "rgba(a8eef6ff)"

on_tertiary_fixed = "rgba(002022ff)"

on_tertiary_fixed_variant = "rgba(004f55ff)"

outline = "rgba(86948eff)"

outline_variant = "rgba(3d4945ff)"

primary = "rgba(00e0b7ff)"

primary_container = "rgba(005140ff)"

primary_fixed = "rgba(17ffd1ff)"

primary_fixed_dim = "rgba(00e0b7ff)"

scrim = "rgba(000000ff)"

secondary = "rgba(a0d0caff)"

secondary_container = "rgba(1f4e4aff)"

secondary_fixed = "rgba(bcece6ff)"

secondary_fixed_dim = "rgba(a0d0caff)"

shadow = "rgba(000000ff)"

source_color = "rgba(106552ff)"

surface = "rgba(0a1612ff)"

surface_bright = "rgba(2f3c37ff)"

surface_container = "rgba(16221eff)"

surface_container_high = "rgba(202c28ff)"

surface_container_highest = "rgba(2b3733ff)"

surface_container_low = "rgba(121e1aff)"

surface_container_lowest = "rgba(05100dff)"

surface_dim = "rgba(0a1612ff)"

surface_tint = "rgba(00e0b7ff)"

surface_variant = "rgba(3d4945ff)"

tertiary = "rgba(8cd2d9ff)"

tertiary_container = "rgba(004f55ff)"

tertiary_fixed = "rgba(a8eef6ff)"

tertiary_fixed_dim = "rgba(8cd2d9ff)"



terminal    = "foot"
fileManager = "thunar"
menu        = "rofi -show drun"
browser     = "firefox"
textEditor  = "mousepad"



hl.bind(
    "SUPER + Q",
    hl.dsp.exec_cmd(terminal),
    { description = "Launch Terminal", submap_universal = true }
)

hl.bind(
    "SUPER + W",
    hl.dsp.exec_cmd(browser),
    { description = "Launch Browser", submap_universal = true }
)

hl.bind(
    "SUPER + E",
    hl.dsp.exec_cmd(fileManager),
    { description = "File Manager", submap_universal = true }
)

hl.bind(
    "SUPER + R",
    hl.dsp.exec_cmd(textEditor),
    { description = "Open Text Editor", submap_universal = true }
)

hl.on("hyprland.start", function()


    hl.exec_cmd("dusky-run -- sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type text --watch cliphist store'")
    hl.exec_cmd("dusky-run -- sh -c '. $HOME/.config/dusky/settings/cliphist_db_env && exec wl-paste --type image --watch cliphist store'")

    hl.exec_cmd("dusky-run -- wl-clip-persist --clipboard regular")

    -- --- OPTIONAL / USER INTERFACE ---
    hl.exec_cmd("systemctl --user import-environment $(env | cut -d'=' -f 1)")
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")

    -- --- dusky glance ---
    -- EG: dusky glance (uncomment only one at a time)
    hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --cpu")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --ram")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --temp")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --battery")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --network")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --uptime")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --workspace")
    -- hl.exec_cmd("~/user_scripts/rofi/dusky_glance.sh --clock")
    hl.exec_cmd('foot --hold --title "Dusky Orchestra" bash -c "~/user_scripts/arch_setup_scripts/ORCHESTRA_iso.sh"')

end)



hl.config({
    misc = {
        disable_hyprland_logo = true, -- Disables random anime girl background
        disable_splash_rendering = true, -- Disables splash rendering
        force_default_wallpaper = 1, -- Enforce default wallpapers (-1 random, 0/1 disables anime)
        animate_manual_resizes = false, -- Animate manual window resizes/moves
        animate_mouse_windowdragging = false, -- Animate windows being dragged by mouse
        background_color = background, -- Custom background color
        render_unfocused_fps = 5, -- Max FPS limit for unfocused background windows
        enable_anr_dialog = true -- Enable "App Not Responding" dialog
    },

    opengl = {
        nvidia_anti_flicker = true -- Reduces flickering on nvidia (ignored on others)
    },

})


hl.monitor({
    output   = "",          -- "" = match any output not covered by a specific rule
    mode     = "preferred", -- use the display's advertised native resolution & rate
    position = "auto",      -- auto-place to the right of other monitors
    scale    = "auto",      -- let Hyprland decide based on PPI
})



hl.gesture({
    fingers   = 3,
    direction = "horizontal",
    action    = "workspace",
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



-- ----------------------------------------------------- 
-- FADE PRESET: Pure Opacity / Ethereal
-- ----------------------------------------------------- 

-- --- Curves for Fading ---
hl.curve("sine", { type = "bezier", points = { {0.5, 0.5}, {0.5, 0.5} } })
hl.curve("sharpFade", { type = "bezier", points = { {0.33, 1}, {0.68, 1} } })
hl.curve("linear", { type = "bezier", points = { {0, 0}, {1, 1} } })

-- --- Animation Configs ---

-- Windows: Popin 100% (no scaling) fast enough so the fade handles the visual transition
hl.animation({ leaf = "windows", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsIn", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })

-- Windows Move: Needs to slide to feel natural
hl.animation({ leaf = "windowsMove", enabled = true, speed = 4, bezier = "sine", style = "slide" })

-- Border: Pulse effect
hl.animation({ leaf = "border", enabled = true, speed = 5, bezier = "sine" })
hl.animation({ leaf = "fade", enabled = true, speed = 5, bezier = "sine" })

-- Layers (Waybar, etc.): Dissolve in
hl.animation({ leaf = "layers", enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersIn", enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersOut", enabled = true, speed = 2, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "fadeLayersIn", enabled = true, speed = 3, bezier = "sharpFade" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 2, bezier = "sharpFade" })

-- Workspaces: Cross-Dissolve
hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "sine", style = "fade" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 6, bezier = "sine", style = "fade" })



require("source.window_rules")

-- 7. KEYBINDINGS
-- Loaded after everything else so all dispatchers (plugin or standard)
-- and rules are already available. Uses globals from default_apps.lua.
require("source.keybinds")


-- gpu 
pcall(require, "gpu")
