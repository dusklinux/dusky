-- =============================================================================
--
-- - Blocks are grouped in an intuitive order (global/general -> floating -> media -> apps
--   -> dialogs/pickers -> visual styling -> workspace rules -> misc/startup).
--
-- =============================================================================

-- -----------------------------------------------------------------------------
-- GENERAL / XWAYLAND / GLOBAL SETTINGS
-- -----------------------------------------------------------------------------
-- These are global tweaks that affect XWayland scaling or other fundamental behavior.
-- Keep them at the top so their effects are obvious when reading the file.
hl.config({
    xwayland = {
        force_zero_scaling = true
    }
})

-- Consolidation note: if you want fewer entries you can replace the three
-- separate 'yad' rules above with a single rule using a combined regex for
-- match.title, e.g. match = { title = "^(Hyprsunset|brightness|volume)$" }
-- I did NOT apply that consolidation so your tested blocks remain unmodified.

-- -----------------------------------------------------------------------------
-- APPLICATION-SPECIFIC FLOATS & VISUALS (Browsers, media, etc.)
-- -----------------------------------------------------------------------------
-- Purpose: Per-application exceptions and visual tweaks (opacity, pin, floating)
-- that aren't simple utilities. Kept grouped by app for readability.

--- Firefox: Float "About" Dialog ---
hl.window_rule({
    name = "float-firefox-about",
    match = { title = "^(About Mozilla Firefox)$" },
    float = true
})

--- Firefox: Float "Library" Window ---
-- Note: This block uses two match properties. Both must be true for the rule to trigger.
hl.window_rule({
    name = "float-firefox-library",
    match = {
        class = "^(firefox)$",
        title = "^(Library)$"
    },
    float = true
})

--- Firefox: YouTube Full Opacity ---
-- Forces 100% opacity (no transparency/dimming) specifically for YouTube
hl.window_rule({
    name = "opaque-firefox-youtube",
    match = {
        class = "^(firefox)$",
        title = ".*YouTube.*"
    },
    opaque = true
})

--- Firefox: Figma Full Opacity ---
-- Forces 100% opacity (no transparency/dimming) specifically for Figma (A web based UI Design tool)
hl.window_rule({
    name = "opaque-firefox-figma",
    match = {
        class = "^(firefox)$",
        title = ".*Figma.*"
    },
    opaque = true
})

--- Firefox: Pixabay Full Opacity ---
-- Forces 100% opacity (no transparency/dimming) specifically for Pixabay
hl.window_rule({
    name = "opaque-firefox-pixabay",
    match = {
        class = "^(firefox)$",
        title = ".*Pixabay.*"
    },
    opaque = true
})

--- Opaque Rules for Specific Apps (Commented Out) ---
-- These are kept as-is from your backup. Uncomment to force global opacity for
-- the matching app class.
-- hl.window_rule({
--     name = "opaque-firefox-global",
--     match = { class = "^(firefox)$" },
--     opaque = true
-- })
-- 
-- hl.window_rule({
--     name = "opaque-obsidian",
--     match = { class = "^(obsidian)$" },
--     opaque = true
-- })

--- MPV: Always Float & Small ---
-- Rationale: MPV is often used for small floating playback/PiP windows.
-- 'size' and 'center' make sure it opens at a reasonable 360p-ish size in the middle.
hl.window_rule({
    name = "float-mpv",
    match = { class = "^(mpv)$" },
    float = true,
    opaque = true,
    size = {640, 360},      -- Sets a small initial size (approx 360p)
    center = true,           -- Opens in the middle of the screen
    keep_aspect_ratio = true  -- Locks the window frame to the video's aspect ratio
})

-- wine/proton without plasma-workspace for tray icon
hl.window_rule({
    name = "xembedsniproxy",
    match = {
        class = "^$",
        title = "^$"
    },
    opacity = "0.0 override 0.0 override",
    no_blur = true
})

-- -----------------------------------------------------
-- qBittorrent: Floating Logic (Layered Approach)
-- -----------------------------------------------------

-- 1. Default Policy: Float ALL qBittorrent windows
hl.window_rule({
    name = "float_qbittorrent_all",
    match = { class = "^(org.qbittorrent.qBittorrent)$" },
    float = true,
    center = true,
    size = {650, 450}
})

-- 2. Exception: Force the Main Window to Tile
hl.window_rule({
    name = "tile_qbittorrent_main",
    match = {
        class = "^(org.qbittorrent.qBittorrent)$",
        title = "^(qBittorrent v).*$"
    },
    -- TRY THIS FIRST:
    float = false
    -- IF THAT FAILS (Window stays floating), COMMENT ABOVE AND USE:
    -- tile = true
})

-- -----------------------------------------------------
-- IDLE INHIBIT (Media Consumption)
-- -----------------------------------------------------

-- usign hypridle's native linstern to prevent sleep while audio/video is playing

-- 1. Dedicated Media Players (VLC & MPV)
-- Matches either VLC or MPV class
-- hl.window_rule({
--     name = "idle_media_players",
--     match = { class = "^(vlc|mpv)$" },
--     idle_inhibit = "focus"
-- })
--
-- -- 2. YouTube (Specific to Firefox)
-- -- Only inhibits idle if the title contains "YouTube" AND it is fullscreen
-- hl.window_rule({
--     name = "idle_youtube",
--     match = {
--         class = "^(firefox)$",
--         title = ".*YouTube.*"
--     },
--     idle_inhibit = "focus"
-- })

-- -------------------------------------------------
-- GAMES AND GAME LAUNCHERS
-- -------------------------------------------------

--- PrismLauncher (3rd party Minecraft Lancher) ---
hl.window_rule({
    name = "PrismLauncher",
    match = { class = "org.prismlauncher.PrismLauncher" },
    float = true
})

--- Minecraft (Game Window) ---
hl.window_rule({
    name = "Minecraft",
    match = { class = "com.mojang.minecraft.java-edition" },
    opaque = true
})

--- TETR.IO ---
hl.window_rule({
    name = "Tetr.io",
    match = { class = "tetrio-desktop" },
    opaque = true
})

--- SuperTuxKart ---
hl.window_rule({
    name = "SuperTuxKart",
    match = { class = "supertuxkart" },
    opaque = true
})

--- ROBLOX (via Sober) ---
hl.window_rule({
    name = "ROBLOX",
    match = { class = "org.vinegarhq.Sober" },
    opaque = true
})

-- -----------------------------------------------------------------------------
-- STEAM (Grouped: general, main, friends, idle_inhibit)
-- -----------------------------------------------------------------------------
-- Rationale: Steam spawns multiple independent windows. Grouping keeps those
-- behaviors clear and separate.

--- Steam: General Rules (Float & Opacity) ---
-- Applies to all windows with class 'steam'
hl.window_rule({
    name = "steam-general",
    match = { class = "^(steam)$" },
    float = true,
    opaque = true
})

--- Steam: Main Window Specifics ---
-- Targets the main library/store window for centering and specific size
hl.window_rule({
    name = "steam-main-window",
    match = {
        class = "^(steam)$",
        title = "^(Steam)$"
    },
    size = {1100, 600},
    center = true
})

--- Steam: Friends List Specifics ---
-- Targets the smaller friends list window
hl.window_rule({
    name = "steam-friends",
    match = {
        class = "^(steam)$",
        title = "^(Friends List)$"
    },
    size = {460, 580}
})

--- Steam: Idle Inhibit (Commented Out) ---
-- Prevents screen from sleeping when Steam is fullscreen
-- hl.window_rule({
--     name = "steam-idle",
--     match = { class = "^(steam)$" },
--     idle_inhibit = "fullscreen"
-- })

-- -----------------------------------------------------------------------------
-- COMMON DESKTOP / GNOME / QT UTILITIES (calculator, viewers, managers)
-- -----------------------------------------------------------------------------
-- Rationale: These are desktop utilities where a floating, centered layout is
-- the expected behavior for usability.

-- -----------------------------------------------------
-- Show Me The Key (Key Visualizer)
-- -----------------------------------------------------
hl.window_rule({
    name = "showmethekey-floating",
    match = {
        class = "^(showmethekey-gtk)$",
        title = "^(Floating Window - Show Me The Key)$"
    },
    float = true,
    pin = true,
    size = {470, 50},
    move = {"((monitor_w-window_w)/2)", "(monitor_h-window_h-20)"},
    -- Critical: Don't let it steal focus (so you can keep typing)
    -- no_focus = true,
    no_dim = true,
    border_size = 0,
    opaque = true
})

--- uGet ---
hl.window_rule({
    name = "uGet",
    match = { title = "^(uGet)$" },
    float = true,
    size = {889, 505},
    center = true
})

--- Calculator ---
hl.window_rule({
    name = "float-calculator",
    match = { class = "^(org.gnome.Calculator)$" },
    float = true,
    size = {360, 616},  -- Compact vertical layout
    center = true
})

--- Gnome Camera ---
hl.window_rule({
    name = "gnome-camera",
    match = { class = "^(org.gnome.Snapshot)$" },
    float = true,
    size = {528, 298},
    center = true
})

--- cameractrls ---
hl.window_rule({
    name = "float-cameractrls-viewfinder",
    match = {
        class = "^(hu.irl.cameractrls)$",
        title = "^(/dev/.*)$"
    },
    float = true,
    size = {624, 353},
    center = true
})

--- Loupe (Image Viewer) ---
hl.window_rule({
    name = "float-loupe",
    match = { class = "^(org.gnome.Loupe)$" },
    float = true,
    size = {900, 600},  -- Wide enough for standard photos
    center = true,
    opaque = true
})

--- GNOME Clocks ---
hl.window_rule({
    name = "float-clocks",
    match = { class = "^(org.gnome.clocks)$" },
    float = true,
    size = {602, 297},  -- Compact landscape for World Clocks/Alarms
    center = true
})

--- Gparted ---
hl.window_rule({
    name = "gparted",
    match = { class = "^(GParted)$" },
    float = true,
    size = {652, 431},
    center = true
})

--- grsync ---
hl.window_rule({
    name = "grsync",
    match = { class = "^(grsync)$" },
    float = true,
    size = {650, 458},
    center = true
})

--- Blueman Manager ---
hl.window_rule({
    name = "float-blueman",
    match = { class = "^(blueman-manager)$" },
    float = true,
    size = {530, 313}, -- Medium size for device lists
    center = true
})

--- handbrake ---
hl.window_rule({
    name = "handbrake",
    match = { class = "^(fr.handbrake.ghb)$" },
    float = true,
    size = {970, 698},
    center = true
})

--- Seahorse Gnome Passwords ---
hl.window_rule({
    name = "seahorse",
    match = { class = "^(org.gnome.seahorse.Application)$" },
    float = true,
    size = {827, 632},
    center = true
})

--- Bluetui ---
hl.window_rule({
    name = "bluetui",
    match = { class = "^(bluetui)$" },
    float = true,
    size = {551, 362},
    center = true
})

--- airmon_ng ---
hl.window_rule({
    name = "airmon_ng",
    match = { class = "^(airmon_ng.sh)$" },
    float = true,
    size = {775, 450},
    center = true
})

--- iphone_vnc.sh ---
hl.window_rule({
    name = "iphone_vnc.sh",
    match = { class = "^(iphone_vnc.sh)$" },
    float = true,
    size = {650, 423},
    center = true
})

--- btrfs_zstd_compression_stats.sh ---
hl.window_rule({
    name = "btrfs_zstd_compression_stats.sh",
    match = { class = "^(btrfs_zstd_compression_stats.sh)$" },
    float = true,
    size = {650, 423},
    center = true
})

--- tailscale_setup ---
hl.window_rule({
    name = "tailscale_setup",
    match = { class = "^(tailscale_setup)$" },
    float = true,
    size = {782, 676},
    center = true
})

--- tailscale_uninstall ---
hl.window_rule({
    name = "tailscale_uninstall",
    match = { class = "^(tailscale_uninstall)$" },
    float = true,
    size = {775, 450},
    center = true
})

--- Kew ---
hl.window_rule({
    name = "kew",
    match = { class = "^(kew)$" },
    float = true,
    size = {652, 576},
    center = true
})

--- file_manager_switcher ---
hl.window_rule({
    name = "file_manager_switcher",
    match = { class = "^(235_file_manager_switch.sh)$" },
    float = true,
    size = {634, 445},
    center = true
})

--- 236_browser_switcher.sh ---
hl.window_rule({
    name = "236_browser_switcher.sh",
    match = { class = "^(236_browser_switcher.sh)$" },
    float = true,
    size = {634, 445},
    center = true
})

--- 237_text_editer_switcher.sh ---
hl.window_rule({
    name = "237_text_editer_switcher.sh",
    match = { class = "^(237_text_editer_switcher.sh)$" },
    float = true,
    size = {634, 445},
    center = true
})

--- 238_terminal_switcher.sh ---
hl.window_rule({
    name = "238_terminal_switcher.sh",
    match = { class = "^(238_terminal_switcher.sh)$" },
    float = true,
    size = {634, 445},
    center = true
})

--- 356_dusky_plugin_manager.sh ---
hl.window_rule({
    name = "356_dusky_plugin_manager.sh",
    match = { class = "^(356_dusky_plugin_manager.sh)$" },
    float = true,
    size = {772, 554},
    center = true
})


--- 055_pacman_reflector.sh ---
hl.window_rule({
    name = "055_pacman_reflector.sh",
    match = { class = "^(055_pacman_reflector.sh)$" },
    float = true,
    size = {752, 576},
    center = true
})


--- ftp_setup_arch.sh ---
hl.window_rule({
    name = "ftp_setup_arch.sh",
    match = { class = "^(250_ftp_arch.sh)$" },
    float = true,
    size = {652, 576},
    center = true
})

--- Hosts file block ---
hl.window_rule({
    name = "325_hosts_files_block.sh",
    match = { class = "^(325_hosts_files_block.sh)$" },
    float = true,
    size = {780, 548},
    center = true
})

--- Dusky system locale ---
hl.window_rule({
    name = "locale_tui.sh",
    match = { class = "^(locale_tui.sh)$" },
    float = true,
    size = {780, 548},
    center = true
})


--- Dusky Main TUI App ---
hl.window_rule({
  name = "dusky_tui",
  match = { class = "^(dusky_tui)$" },
  float = true,
  size = {815,539},
  center = true
})

--- Dusky glance_mako_tui.sh ---
hl.window_rule({
    name = "glance_mako_tui.sh",
    match = { class = "^(glance_mako_tui.sh)$" },
    float = true,
    size = {780, 548},
    center = true
})

--- change_ftp_directory_server.sh ---
hl.window_rule({
    name = "change_ftp_directory_server.sh",
    match = { class = "^(change_ftp_directory_server.sh)$" },
    float = true,
    size = {652, 576},
    center = true
})

--- arp_scan.sh ---
hl.window_rule({
    name = "arp_scan.sh",
    match = { class = "^(arp_scan.sh)$" },
    float = true,
    size = {652, 576},
    center = true
})

--- ssh setup
hl.window_rule({
    name = "02_openssh_setup.py",
    match = {
        class = "^(02_openssh_setup\\.py)$",
    },
    float = true,
    size = {945, 731},
})

--- Clipbard_persistance ---
hl.window_rule({
    name = "390_clipboard_persistance.sh",
    match = { class = "^(390_clipboard_persistance.sh)$" },
    float = true,
    size = {805, 323},
    center = true
})

--- Clipbard_interface ---
hl.window_rule({
    name = "460_switch_clipboard.sh",
    match = { class = "^(460_switch_clipboard\\.sh)$" },
    float = true,
    size = {805, 323},
    center = true
})


--- Cache_purge ---
hl.window_rule({
    name = "cache_purge",
    match = { class = "^(cache_purge.sh)$" },
    float = true,
    size = {589, 529},
    center = true
})

--- Mouse_button_reverse ---
hl.window_rule({
    name = "mouse_button_reverse",
    match = { class = "^(mouse_button_reverse.sh)$" },
    float = true,
    size = {589, 529},
    center = true
})

--- Git_config ---
hl.window_rule({
    name = "300_git_config.sh",
    match = { class = "^(300_git_config.sh)$" },
    float = true,
    size = {726, 389},
    center = true
})

--- Dusky Backup Manager (Setup / Relink) ---
hl.window_rule({
    name = "dusky_backup_manager.py",
    match = { class = "^(dusky_backup_manager\\.py)$" },
    float = true,
    size = {726, 689},
    center = true
})


--- dusky_snapshot_manager.py ---
hl.window_rule({
    name = "dusky_snapshot_manager.py",
    match = { class = "^(dusky_snapshot_manager.py)$" },
    float = true,
    size = {"(monitor_w*0.95)", "(monitor_h*0.9)"},
    center = true
})

--- dusky_disk_monitor_io.py ---
hl.window_rule({
    name = "dusky_disk_monitor_io.py",
    match = { class = "^(dusky_disk_monitor_io.py)$" },
    float = true,
    size = {871, 607},
    center = true
})

--- terminal clipboard ---
hl.window_rule({
    name = "terminal_clipboard",
    match = { class = "^(terminal_clipboard.sh)$" },
    float = true,
    no_anim = true,
    size = {840, 520},
    center = true
})

--- asusctl script ---
hl.window_rule({
    name = "asusctl.sh",
    match = { class = "^(asusctl.sh)$" },
    float = true,
    size = {789, 534},
    center = true
})

--- neovim reset script ---
hl.window_rule({
    name = "01_reset_neovim.sh",
    match = { class = "^(01_reset_neovim.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- neovim plugins sync script ---
hl.window_rule({
    name = "02_cli_plugins_download.sh",
    match = { class = "^(02_cli_plugins_download.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- neovim manager for dusky ---
hl.window_rule({
    name = "dusky_neovim_manager.sh",
    match = { class = "^(dusky_neovim_manager.sh)$" },
    float = true,
    size = {532, 475},
    center = true
})

--- Dusky optional pacakges ---
hl.window_rule({
    name = "090_paru_packages_optional.sh",
    match = { class = "^(090_paru_packages_optional\\.sh)$" },
    float = true,
    size = {831, 572}
})

--- dusky zram configurator ---
hl.window_rule({
    name = "205_zram_configuration.sh",
    match = { class = "^(205_zram_configuration.sh)$" },
    float = true,
    size = {907, 377},
    center = true
})

--- step 1 limine script ---
hl.window_rule({
    name = "01_limine_setup.sh",
    match = { class = "^(01_limine_setup.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- step 2 Snapper Isolation Subvolume script ---
hl.window_rule({
    name = "02_snapper_isolation_subvolume.sh",
    match = { class = "^(02_snapper_isolation_subvolume.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- step 3 Snapper Pacman Hooks script ---
hl.window_rule({
    name = "03_snapper_pacman_hooks.sh",
    match = { class = "^(03_snapper_pacman_hooks.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- dusky swapiness ---
hl.window_rule({
    name = "210_zram_optimize_swappiness.sh",
    match = { class = "^(210_zram_optimize_swappiness\\.sh)$" },
    float = true,
    size = {938, 500},
    center = true
})

--- dusky gpu env setter ---
hl.window_rule({
    name = "000_configure_uwsm_gpu.sh",
    match = {
        class = "^(000_configure_uwsm_gpu\\.sh)$"
        -- title = "^(sh)$"
    },
    float = true,
    size = {702, 492},
    center = true
})

--- Dusky Wayclick ---
hl.window_rule({
    name = "dusky_wayclick.sh",
    match = { class = "^(dusky_wayclick.sh)$" },
    float = true,
    size = {831, 572}
})

--- Dusky Wayclick TUI ---
hl.window_rule({
    name = "dusky_tui_wayclick.sh",
    match = { class = "^(dusky_tui_wayclick.sh)$" },
    float = true,
    size = {780, 510}
})

--- wayclick_soundpacks_download.sh ---
hl.window_rule({
    name = "wayclick_soundpacks_download.sh",
    match = { class = "^(wayclick_soundpacks_download.sh)$" },
    float = true,
    size = {580, 810}
})

--- autologin script ---
hl.window_rule({
    name = "285_tty_autologin.sh",
    match = { class = "^(285_tty_autologin.sh)$" },
    float = true,
    size = {730, 454},
    center = true
})

--- monitor_wizard.py script ---
hl.window_rule({
    name = "monitor_wizard.py",
    match = { class = "^(monitor_wizard.py)$" },
    float = true,
    size = {802, 469},
    center = true
})

--- dusky_keybinds.sh script ---
hl.window_rule({
    name = "dusky_keybinds.sh",
    match = { class = "^(dusky_keybinds.sh)$" },
    float = true,
    size = {"(monitor_w*0.9)", "(monitor_h*0.9)"},
    move = {"(monitor_w*0.05)", "(monitor_h*0.05)"}
})



--- dusky_packages.sh script ---
hl.window_rule({
    name = "dusky_packages.sh",
    match = { class = "^(dusky_packages.sh)$" },
    float = true,
    size = {"(monitor_w*0.9)", "(monitor_h*0.9)"},
    move = {"(monitor_w*0.05)", "(monitor_h*0.05)"}
})

--- dusky_appearances.sh script ---
hl.window_rule({
    name = "dusky_appearances.sh",
    match = { class = "^(dusky_appearances.sh)$" },
    float = true,
    size = {781, 507},
    center = true
})

--- dusky_workspace_manager.sh script ---
hl.window_rule({
    name = "dusky_workspace_manager.sh",
    match = { class = "^(dusky_workspace_manager.sh)$" },
    float = true,
    size = {781, 507},
    center = true
})

--- dusky_matugen_presets.sh script ---
hl.window_rule({
    name = "dusky_matugen_presets.sh",
    match = { class = "^(dusky_matugen_presets.sh)$" },
    float = true,
    size = {820, 620},
    center = true
})

--- dusky_input.sh script ---
hl.window_rule({
    name = "dusky_input.sh",
    match = { class = "^(dusky_input.sh)$" },
    float = true,
    size = {781, 507},
    center = true
})

--- tui_mako.sh script ---
hl.window_rule({
    name = "tui_mako.sh",
    match = { class = "^(tui_mako.sh)$" },
    float = true,
    size = {781, 507},
    center = true
})

--- dusky_gsettings.sh script ---
hl.window_rule({
    name = "dusky_gsettings.sh",
    match = { class = "^(dusky_gsettings.sh)$" },
    float = true,
    size = {781, 507},
    center = true
})

--- dconf Editor ---
hl.window_rule({
    name = "cadesrtdconf-editor",
    match = { class = "^(ca\\.desrt\\.dconf-editor)$" },
    float = true,
    size = {979, 642}
})

--- dusky_power.sh script ---
hl.window_rule({
    name = "dusky_power.sh",
    match = { class = "^(dusky_power.sh)$" },
    float = true,
    size = {790, 530},
    center = true
})

--- dusky_battery_tui.sh ---
hl.window_rule({
    name = "dusky_battery_notify.sh",
    match = { class = "^(dusky_battery_notify.sh)$" },
    float = true,
    size = {790, 530},
    center = true
})

--- batery notify setup script ---
hl.window_rule({
    name = "135_battery_notify_service.sh",
    match = { class = "^(135_battery_notify_service.sh)$" },
    float = true,
    size = {504, 501},
    center = true
})

--- dusky_hypridle.sh script ---
hl.window_rule({
    name = "dusky_hypridle.sh",
    match = { class = "^(dusky_hypridle.sh)$" },
    float = true,
    size = {784, 529},
    center = true
})

--- fastfetch ---
hl.window_rule({
    name = "fastfetch",
    match = { class = "^(fastfetch)$" },
    float = true,
    size = {943, 393},
    center = true
})

-- dusky_window_rules.sh
hl.window_rule({
    name = "kitty",
    match = { class = "^(dusky_window_rules.sh)$" },
    float = true,
    size = {1000, 750},
    center = true
})

--- dysk ---
hl.window_rule({
    name = "dysk",
    match = { class = "^(dysk)$" },
    float = true,
    size = {1005, 298},
    center = true
})

--- Performance script ---
hl.window_rule({
    name = "performance.sh",
    match = { class = "^(performance.sh)$" },
    float = true,
    size = {566, 569},
    center = true
})

--- Kokoro_GPU script ---
hl.window_rule({
    name = "kokoro",
    match = { class = "^(kokoro)$" },
    float = true,
    pin = true,
    size = {254, 90},
    move = {"(monitor_w-window_w-8)", "(monitor_h-window_h-8)"},
    no_dim = true,
    opaque = true
})

--- Kokoro Setup ---
hl.window_rule({
    name = "kokoro_installer.sh",
    match = { class = "^(kokoro_installer.sh)$" },
    float = true,
    pin = true,
    size = {876, 601}
})

--- parakeet Setup ---
hl.window_rule({
    name = "parakeet_installer.sh",
    match = { class = "^(parakeet_installer.sh)$" },
    float = true,
    pin = true,
    size = {876, 601}
})

--- Peaclock-tui-time ---
hl.window_rule({
    name = "peaclock",
    match = { class = "^(peaclock)$" },
    float = true,
    center = true,
    size = {406, 179}
})

--- wifitui ---
hl.window_rule({
    name = "wifitui_float",
    match = { class = "^(wifitui)$" },
    float = true,
    size = {596, 318},
    center = true
})

---res_mon---
hl.window_rule({
    name = "res_mon",
    match = {
        class = "^(res_mon)$",
    },
    float = true,

    size = {699, 458},
    -- size = {"monitor_w * 0.3641", "monitor_h * 0.4241"},

    --    move = {740, 158},
    -- move = {"monitor_w * 0.3854", "monitor_h * 0.1463"},
    -- move = {"monitor_w - window_w - 20", "monitor_h - window_h - 20"},
 
    animation = "slide bottom",   -- always slide in from the bottom
})


--- nmcli script ---
hl.window_rule({
    name = "tui_dusky_network.py",
    match = { class = "^(tui_dusky_network.py)$" },
    float = true,
    size = {780,530},
    center = true
})

--- Tray-tui ---
hl.window_rule({
    name = "tray-tui",
    match = { class = "^(tray-tui)$" },
    float = true,
    size = {791, 488},
    center = true
})

--- Cava Music visvualiser ---
hl.window_rule({
    name = "cava",
    match = { class = "^(cava)$" },
    float = true,
    size = {791, 488},
    center = true
})

--- resouce monitor ---
hl.window_rule({
    name = "htop",
    match = { class = "^(htop)$" },
    float = true,
    size = {1080, 607},
    center = true
})

--- resouce monitor ---
hl.window_rule({
    name = "dgop",
    match = { class = "^(dgop)$" },
    float = true,
    size = {1080, 607},
    center = true
})

--- resouce monitor ---
hl.window_rule({
    name = "btop",
    match = { class = "^(btop)$" },
    float = true,
    size = {1080, 607},
    center = true
})

--- nvim ---
hl.window_rule({
    name = "nvim",
    match = { class = "^(nvim)$" },
    float = true,
    size = {455, 549},
    center = true
})

--- dusky CC hypr config text editor ---
hl.window_rule({
    name = "org.gnome.TextEditor",
    match = { class = "^(org.gnome.TextEditor)$" },
    float = true,
    size = {"(monitor_w*0.65)", "(monitor_h*0.92)"},
    move = {"(monitor_w*0.05)", "(monitor_h*0.05)"},
    center = true
})

--  Mousepad --
hl.window_rule({
    name = "orgxfcemousepad",
    match = {
        class = "^(org\\.xfce\\.mousepad)$",
    },
    float = true,
    size = {"monitor_w * 0.4", "monitor_h * 0.7"},
    animation = "popin 60%",      -- scale in starting from 60% size
})

--- erroands-gnome ---
hl.window_rule({
    name = "errands",
    match = { title = "^(Errands)$" },
    float = true,
    size = {519, 614},
    center = true
})

--- backup dir open from cc ---
hl.window_rule({
    name = "backups_dusky",
    match = {
        class = "^(thunar)$",
        title = "^(dusky_backups - Thunar)$"
    },
    float = true,
    size = {"(monitor_w*0.5612)", "(monitor_h*0.8)"},
    center = true
})


-- Reset user configs ---
hl.window_rule({
    name = "reset_configs",
    match = {
        class = "^(reset_configs)$",
    },
    float = true,
    size = {913, 579},
    -- size = {"monitor_w * 0.4755", "monitor_h * 0.5361"},
--     move = {507, 224},
     move = {"monitor_w * 0.2641", "monitor_h * 0.2074"},
    -- move = {"monitor_w - window_w - 20", "monitor_h - window_h - 20"},
})


-- Backup Viewer yazi
hl.window_rule({
    name = "yazi",
    match = {
        class = "^(yazi)$",
        title = "^(Backup Viewer)$"
    },
    float = true,
    size = {"(monitor_w*0.6283)", "(monitor_h*0.8600)"},
    center = true
})

-- Dusky Control Center
hl.window_rule({
    name = "controlcenter",
    match = {
        class = "^(com\\.github\\.dusky\\.controlcenter)$",
    },
    float = true,
    size = {"monitor_w * 0.3958", "monitor_h * 0.9093"},
    animation = "slide up"
})

--- Dusky_QuickPanal Script ---
hl.window_rule({
    name = "dusky_quickpanalpy",
    match = {
        class = "^(dusky_quickpanal\\.py)$",
        -- title = "^(dusky_quickpanal\\.py)$",
    },
    float = true,
    animation = "slide right",
    no_dim = true,
    rounding = 20,
    move = {"(monitor_w-window_w-20)", "(monitor_h-window_h-20)"},
    border_size = 0
})

--- Audio Router Popup ---
hl.window_rule({
    name = "audiorouter-popup",
    match = { class = "^(dev\\.audiorouter\\.popup)$" },
    float = true,
    center = true,
    size = {"(monitor_w*0.4557)", "(monitor_h*0.9324)"}
})

--- gnome-disks ---
hl.window_rule({
    name = "disks",
    match = {
        title = "^(Disks)$",
        class = "^(org.gnome.DiskUtility)$"
    },
    float = true,
    size = {890, 512},
    center = true
})

--- gnome-disk-analyzer-baobab ---
hl.window_rule({
    name = "baobab",
    match = { class = "^(org.gnome.baobab)$" },
    float = true,
    size = {1152, 648},
    center = true
})

--- Thunar rename dialog ---
hl.window_rule({
    name = "thunar_rename_dialog",
    match = {
        class = "^(thunar|Thunar)$",
        title = "^Rename.*$"
    },
    float = true,
    center = true,
})

--- Thunar file operation progress ---
hl.window_rule({
    name = "thunar_file_operation_progress",
    match = {
        class = "^(thunar|Thunar)$",
        title = "^File Operation Progress$"
    },
    float = true,
    center = true,
})

--- Thunar confirm replace files dialog ---
hl.window_rule({
    name = "thunar_confirm_replace_files",
    match = {
        class = "^(thunar|Thunar)$",
        title = "^Confirm to replace files$"
    },
    float = true,
    center = true,
    move = {"((monitor_w-window_w)/2)", "((monitor_h-window_h)/2) + 80"},
})


-- Mousepad save dialog ---
hl.window_rule({
    name = "mousepad_save_dialog",
    match = {
        class = "^(org\\.xfce\\.mousepad)$",
        title = "^Save( .*)?$"
    },
    float = true,
    center = true,
})


--- System benchmarking script ---
hl.window_rule({
    name = "sysbench_benchmark.py",
    match = { class = "^(sysbench_benchmark.py)$" },
    float = true,
    size = {567, 658},
    center = true
})

--- ntfs_fix.sh ---
hl.window_rule({
    name = "ntfs_fix.sh",
    match = { class = "^(ntfs_fix.sh)$" },
    float = true,
    size = {766, 485},
    center = true
})

--- POwer saving script ---
hl.window_rule({
    name = "power_saver.sh",
    match = { class = "^(power_saver.sh)$" },
    float = true,
    size = {737, 628},
    center = true
})

--- Turning off Power saving script ---
hl.window_rule({
    name = "power_saver_off.sh",
    match = { class = "^(power_saver_off.sh)$" },
    float = true,
    size = {568, 456},
    center = true
})

--- 080_aur_paru_fallback_yay.sh script ---
hl.window_rule({
    name = "080_aur_paru_fallback_yay.sh",
    match = { class = "^(080_aur_paru_fallback_yay.sh)$" },
    float = true,
    size = {567, 658},
    center = true
})

--- 085_warp.py script ---
hl.window_rule({
    name = "085_warp.py",
    match = { class = "^(085_warp.py)$" },
    float = true,
    size = {567, 658},
    center = true
})

--- 335_preload_config.sh script ---
hl.window_rule({
    name = "335_preload_config.sh",
    match = { class = "^(335_preload_config.sh)$" },
    float = true,
    size = {889, 669},
    center = true
})

--- 465_sddm_setup.sh script ---
hl.window_rule({
    name = "465_sddm_setup.sh",
    match = { class = "^(465_sddm_setup.sh)$" },
    float = true,
    size = {889, 669},
    center = true
})

--- update_dusky.sh script ---
hl.window_rule({
    name = "update_dusky.sh",
    match = { class = "^(update_dusky.sh)$" },
    float = true,
    size = {1192, 710},
    center = true
})

--- system_update.sh script ---
hl.window_rule({
    name = "system_update.sh",
    match = { class = "^(system_update.sh)$" },
    pin = true,
    float = true,
    size = {1192, 710},
    center = true
})

--- Orchestra script ---
hl.window_rule({
    name = "ORCHESTRA.sh",
    match = { class = "^(ORCHESTRA.sh)$" },
    float = true,
    size = {"(monitor_w*0.9)", "(monitor_h*0.9)"},
    move = {"(monitor_w*0.05)", "(monitor_h*0.05)"}
})

--- deploy_dotfiles script ---
hl.window_rule({
    name = "deploy_dotfiles.sh",
    match = { class = "^(deploy_dotfiles.sh)$" },
    float = true,
    size = {"(monitor_w*0.9)", "(monitor_h*0.9)"},
    move = {"(monitor_w*0.05)", "(monitor_h*0.05)"}
})

--- restore_stash.sh script ---
hl.window_rule({
    name = "restore_stash.sh",
    match = { class = "^(restore_stash.sh)$" },
    float = true,
    size = {1192, 710},
    center = true
})

--- send_logs.sh script ---
hl.window_rule({
    name = "send_logs.sh",
    match = { class = "^(send_logs.sh)$" },
    float = true,
    size = {500, 250},
    center = true
})

--- about_dusky.sh script ---
hl.window_rule({
    name = "about_dusky.sh",
    match = { class = "^(about_dusky.sh)$" },
    float = true,
    size = {503, 264},
    center = true
})

--- Ollama sidebar script ---
hl.window_rule({
    name = "ollama_terminal.sh",
    match = { class = "^(ollama_terminal.sh)$" },
    float = true,
    -- size = {409, 710},
    -- move = {50, "(monitor_h*0.5 - window_h*0.5)"},
    size = {"(monitor_w*0.28)", "(monitor_h*0.88)"},
    animation = "slide left",
    rounding = 9,
    move = {"(monitor_w*0.038)", "(monitor_h*0.5 - window_h*0.5)"}
})

--- dusky_service_toggle.sh script ---
hl.window_rule({
    name = "dusky_service_toggle.sh",
    match = { class = "^(dusky_service_toggle.sh)$" },
    float = true,
    size = {840, 598},
    center = true
})

--- music recognition script ---
hl.window_rule({
    name = "music_recognition.sh",
    match = { class = "^(music_recognition.sh)$" },
    float = true,
    size = {409, 147},
    center = true
})

--- dusky_hyprlock_switcher.sh script ---
hl.window_rule({
    name = "dusky_hyprlock_switcher.sh",
    match = { class = "^(dusky_hyprlock_switcher.sh)$" },
    float = true,
    size = {821, 508},
    center = true
})

--- waybar tui ---
hl.window_rule({
    name = "waybar_tui",
    match = {
        class = "^(waybar_tui)$",
    },
    float = true,
    size = {720, 690},
    -- size = {"monitor_w * 0.5523", "monitor_h * 0.9583"},
})

--- Zathura (PDF Viewer) ---
hl.window_rule({
    name = "float-zathura",
    match = { class = "^(org.pwmt.zathura)$" },
    float = true,
    size = {655, 526}, -- Portrait ratio for reading documents
    center = true
})

--- Waypaper ---
hl.window_rule({
    name = "float-waypaper",
    match = { class = "^(waypaper)$" },
    float = true,
    size = {786, 492}, -- Large enough to preview wallpapers comfortably
    center = true
})


--- Dusky Wallpaper Selector---
hl.window_rule({
    name = "wallpaper_selectorpy",
    match = {
        class = "^(wallpaper_selector\\.py)$",
    },
    float = true,
    size = {784, 553},
    -- size = {"monitor_w * 0.49", "monitor_h * 0.6144"},

    animation = "popin 60%",      -- scale in starting from 60% size
})


--- Hyprland Share Picker ---
hl.window_rule({
    name = "float-share-picker",
    match = { class = "^(hyprland-share-picker)$" },
    float = true,
    size = {500, 300},  -- Small dialog box
    center = true
})

--- NWG Look (GTK Theming) ---
hl.window_rule({
    name = "float-nwg-look",
    match = { class = "^(nwg-look)$" },
    float = true,
    size = {627, 464},  -- Standard window size for settings
    center = true
})

--- Kvantum Manager (Qt Theming) ---
hl.window_rule({
    name = "float-kvantum",
    match = { class = "^(kvantummanager)$" },
    float = true,
    size = {585, 512},
    center = true
})

--- Qt6 Configuration Tool ---
hl.window_rule({
    name = "float-qt6ct",
    match = { class = "^(qt6ct)$" },
    float = true,
    size = {700, 609},
    center = true
})

--- Qt5 Configuration Tool ---
hl.window_rule({
    name = "float-qt5ct",
    match = { class = "^(qt5ct)$" },
    float = true,
    size = {636, 665},
    center = true
})

--- Guifetch ---
hl.window_rule({
    name = "float-guifetch",
    match = { class = "^(guifetch)$" },
    float = true,
    size = {800, 500},
    center = true
})

--- Pavucontrol (Volume Control) ---
hl.window_rule({
    name = "float-pavucontrol",
    match = { class = "^(pavucontrol|org.pulseaudio.pavucontrol)$" },
    float = true,
    size = {643, 422},
    center = true
})

--- Network Connection Editor ---
hl.window_rule({
    name = "float-nm-editor",
    match = { class = "^(nm-connection-editor)$" },
    float = true,
    size = {432, 423},
    center = true
})

--- Virt-Manager vm window ---
hl.window_rule({
    name = "float_vm_viewer",
    match = {
        class = "^(virt-manager)$",
        title = "^(.* on QEMU/KVM)$"
    },
    float = true,
    center = true,
    size = {1043, 634}
})

-- -----------------------------------------------------------------------------
-- PICTURE-IN-PICTURE (PiP) & PINNED WINDOWS
-- -----------------------------------------------------------------------------
-- Purpose: Special handling for PiP-style windows—float, pin, size and place
-- them in the bottom-right with no dimming, and ensure pinned windows have
-- a consistent visual style (border, no dimming).

--- Picture-in-Picture (PiP) ---
hl.window_rule({
    name = "pip-global",
    match = { title = "^([Pp]icture[-\\s]?[Ii]n[-\\s]?[Pp]icture)(.*)$" },
    
    -- 1. Set State
    float = true,
    pin = true,

    -- 2. Preserve native aspect ratio during resizing
    keep_aspect_ratio = true,

    -- 3. Move to Bottom-Right
    -- standard syntax: (screen_width - window_width - margin)
    size = {"monitor_w * 0.1617", "monitor_h * 0.1611"},
    --move = {"monitor_w - window_w - 20", "monitor_h - window_h - 20"},
    move = {"monitor_w * 0.8258", "monitor_h * 0.8125"},

    no_initial_focus = true,            -- do not focus when first opened
    -- Visuals
    no_dim = true,
    opaque = true
})

--- Pinned Window Styling ---
-- Applies automatically to ANY window that is pinned (on all workspaces).
hl.window_rule({
    name = "style-pinned-windows",
    match = { pin = true },

    -- 1. Prevent Dimming (keep it bright)
    no_dim = true,

    -- 2. Visual Distinction (Green Border)
    -- Helps you instantly identify which window is pinned
    border_color = inverse_primary,

    -- 3. Thicker Border
    border_size = 2,

    animation = "slide down"
})

-- -----------------------------------------------------------------------------
-- GLOBAL WINDOW BEHAVIORS
-- -----------------------------------------------------------------------------
-- Purpose: Global behavior rules that should apply broadly and consistently.

--- Global: Persistent Size for Floating Windows ---
-- Ensures floating windows remember their size when reopened.
hl.window_rule({
    name = "global-persistent-size",
    match = { float = true },
    persistent_size = true
})

--- Global: Prevent Maximize Events ---
-- Forces all apps to respect tiling/floating rules instead of maximizing.
hl.window_rule({
    name = "global-suppress-maximize",
    match = { class = ".*" },
    suppress_event = "maximize"
})

-- -----------------------------------------------------------------------------
-- XWAYLAND / PHANTOM WINDOW FIXES
-- -----------------------------------------------------------------------------
-- Purpose: Workaround for invisible XWayland windows (tooltips/drag previews)
-- that can erroneously take focus. This matches empty-class/title XWayland
-- floating windows that are not fullscreen and not pinned, and prevents focus.
hl.window_rule({
    name = "fix-xwayland-phantom",
    match = {
        class = "^$",
        title = "^$",
        xwayland = true,
        float = true,
        fullscreen = false,
        pin = false
    },
    no_focus = true
})

-- -----------------------------------------------------------------------------
-- FULLSCREEN & VISUAL STYLING
-- -----------------------------------------------------------------------------
-- Purpose: Visual style tweaks for fullscreen windows and special workspaces.

--- Fullscreen Window Customization ---
-- Applies automatically whenever a window enters fullscreen mode.
hl.window_rule({
    name = "style-fullscreen",
    match = { fullscreen = true },

    -- 1. Custom color Border
    border_color = "rgb(E2971F)",

    -- 2. Force Border Size to 2 (Ensures border is visible in fullscreen)
    border_size = 4,

    -- 3. Remove Rounded Corners (Sharp edges for fullscreen)
    rounding = 0
})

-- -----------------------------------------------------------------------------
-- COMMON DIALOGS / FILE PICKERS
-- -----------------------------------------------------------------------------
-- Rationale: Some apps use title-based dialogs while others use class-based
-- portal windows. We keep both rules so title-based and class-based dialogs are
-- handled reliably.

--- Common Dialogs (Title Based) ---
-- Floats windows based on their Title text.
hl.window_rule({
    name = "float-dialogs-title",
    -- Regex Logic: Matches specific phrases OR any title containing "dialog"
    match = { title = "^(Open|Open File|Select a File|Choose wallpaper|Open Folder|Save As|Library|File Upload|Authentication Required|Add Folder to Workspace|Choose Files)(.*)$|^(.*dialog.*)$" },
    float = true,
    center = true,
    size = {816, 537}
})

--- Common Dialogs (Class Based) ---
-- Floats windows based on their Class ID.
hl.window_rule({
    name = "float-dialogs-class",
    -- Matches FileRoller, XDG Portal (GTK), or any class containing "dialog"
    match = { class = "^(org.gnome.FileRoller|[Xx]dg-desktop-portal-gtk|.*dialog.*)$" },
    float = true,
    center = true,
    size = {816, 537}
})

-- -- satty ---
hl.window_rule({
    name = "comgabmsatty",
    match = { class = "^(com\\.gabm\\.satty)$" },
    center = true,
    float = true,
    opaque = true,
    animation = "slide down",
    size = {1115, 624}
})

-- -----------------------------------------------------------------------------
-- Layer Rules like rofi, awww, wlogout (check using `hyprctl layers`)
-- -----------------------------------------------------------------------------

--rofi rule
hl.layer_rule({
    name = "blur-rofi",
    match = { namespace = "rofi" },
    blur = true,
    dim_around = true,
    ignore_alpha = 0.0
    -- animation = "slide down"
})

--mako rule
hl.layer_rule({
    name = "mako",
    match = { namespace = "notifications" },
    blur = true,
    ignore_alpha = 0.0
})

--wlogout
hl.layer_rule({
    name = "logout_dialog_style",
    match = { namespace = "logout_dialog" },
    blur = true,
    ignore_alpha = 0.0
})

-- selection for screenshot
hl.layer_rule({
    name = "selection_white menu",
    match = { namespace = "selection" },
    blur = false,
    no_anim = true
})

--waybar
hl.layer_rule({
    name = "waybar_blur",
    match = { namespace = "waybar" },
    blur = true,
    blur_popups = true,
    xray = true,
    ignore_alpha = 0.54
})



-- SMART GAPS (Disabled / Examples)
-- -----------------------------------------------------------------------------
-- These blocks are retained from your config as commented examples. They show
-- the approach for dynamic gaps/border removal when there is only one tiled
-- window or when a workspace is in a maximized layout state. Uncomment and
-- adapt if/when you want to enable smart gaps for specific workspaces/layouts.

--------------------------------------------------------------------------------
-- SMART GAPS (No Gaps/Borders when only one window is visible)
-- this is in appearance.lua
--------------------------------------------------------------------------------

--- 1. Behavior for Single Tiled Window (w[tv1]) ---
-- A. Workspace Rule: Remove gaps when only 1 tiled window is visible
-- hl.workspace_rule({ name = "w[tv1]", gapsout = 0, gapsin = 0 })

-- B. Window Rule: Remove border & rounding for windows on that workspace
-- hl.window_rule({
--     name = "smart-gaps-single",
--     match = {
--         workspace = "w[tv1]",
--         float = false
--     },
--     border_size = 0,
--     rounding = 0
-- })

--- 2. Behavior for Maximized Layout State (f[1]) ---

-- A. Workspace Rule: Remove gaps when workspace is maximized
-- hl.workspace_rule({ name = "f[1]", gapsout = 0, gapsin = 0 })

-- B. Window Rule: Remove border & rounding for windows on maximized workspace
-- hl.window_rule({
--     name = "smart-gaps-maximized",
--     match = {
--         workspace = "f[1]",
--         float = false
--     },
--     border_size = 0,
--     rounding = 0
-- })

-- -----------------------------------------------------------------------------
-- MISCELLANEOUS / STARTUP & EXAMPLES
-- -----------------------------------------------------------------------------

--- background apps open in the foreground when fullscreened
hl.config({
    misc = {
        -- 0 = Do Nothing: New window opens behind the fullscreen app (you won't see it).
        -- 1 = Overlay: New window opens ON TOP, but the background app STAYS fullscreen.
        -- 2 = Unfullscreen: Exits fullscreen entirely and switches focus to the new window.
        on_focus_under_fullscreen = 2,

        -- REQUIRED: Force new windows to spawn on the *current* workspace.
        -- Without this, the new window might open "behind" or on a different 
        -- workspace (value 0 or 1 or 2), failing to trigger the unfullscreen logic.
        initial_workspace_tracking = 1,

        -- OPTIONAL: Useful if the app (like Steam) asks to be focused but Hyprland ignores it.
        focus_on_activate = true
    }
})

-- --- Example Window Rule (Converted to Block Syntax) ---
-- Kept as an explicit example in the backup. Useful as a template for new rules.
-- hl.window_rule({
--     name = "example-kitty-float",
--     match = {
--         class = "^(kitty)$",
--         title = "^(kitty)$"
--     },
--     float = true,
--     size = {1135, 634},
--     center = true
-- })

--- Calendar (ikhal): Float with Default Size ---
hl.window_rule({
    name = "float-calendar",
    match = {
        class = "^(ikhal)$"
    },
    float = true,
    size = {650, 700},
    center = true
})

-- =============================================================================
-- END OF FILE
-- =============================================================================
