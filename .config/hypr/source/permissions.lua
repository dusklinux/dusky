-- =============================================================================
-- SYSTEM PERMISSIONS ("HARDENED MODE")
-- =============================================================================
-- By default, Hyprland allows apps to capture the screen via standard portals.
-- Uncomment the ecosystem block ONLY if you want to lock down your system and
-- manually whitelist every app that needs screen access.
--
-- hl.config({ ecosystem = { enforce_permissions = true } })
-- =============================================================================

-- --- Whitelist (only active if ecosystem enforcement is enabled above) ---

-- Allow standard screenshot tools
hl.permission({ binary = "/usr/(bin|local/bin)/grim",  type = "screencopy", mode = "allow" })
hl.permission({ binary = "/usr/(bin|local/bin)/slurp", type = "screencopy", mode = "allow" })

-- Allow the Portal (CRITICAL: this is what OBS uses)
hl.permission({ binary = "/usr/(lib|libexec|lib64)/xdg-desktop-portal-hyprland", type = "screencopy", mode = "allow" })

-- Allow Waybar (for workspace/window info modules)
hl.permission({ binary = "/usr/bin/waybar", type = "screencopy", mode = "allow" })

-- Hyprpm (plugin manager)
hl.permission({ binary = "/usr/(bin|local/bin)/hyprpm", type = "plugin", mode = "allow" })
