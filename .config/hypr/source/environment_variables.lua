-- =============================================================================
-- ENVIRONMENT VARIABLES
-- =============================================================================
-- NOTE: It is strongly advised to place environment variables in UWSM files:
--   ~/.config/uwsm/env          (compositor-agnostic variables)
--   ~/.config/uwsm/env-hyprland (Hyprland-specific variables)
--
-- This file only sets the single variable required to suppress a Hyprland
-- startup warning.
-- =============================================================================

hl.env("XDG_CURRENT_DESKTOP", "Hyprland")
