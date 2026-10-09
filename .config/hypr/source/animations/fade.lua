-- ----------------------------------------------------- 
-- FADE PRESET: Pure Opacity / Ethereal
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

-- --- Curves for Fading ---
hl.curve("linear", { type = "bezier", points = { {0, 0}, {1, 1} } })
hl.curve("sharpFade", { type = "bezier", points = { {0.33, 1}, {0.68, 1} } })

-- --- Animation Configs ---

-- Windows: Popin 100% (no scaling) fast enough so the fade handles the visual transition
hl.animation({ leaf = "windows", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsIn", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })

-- Windows Move: Needs to slide to feel natural
hl.animation({ leaf = "windowsMove", enabled = true, speed = 4, bezier = "linear", style = "slide" })

-- Border and opacity: constant-rate transitions
hl.animation({ leaf = "border", enabled = true, speed = 5, bezier = "linear" })
hl.animation({ leaf = "fade", enabled = true, speed = 5, bezier = "linear" })

-- Layers (Waybar, etc.): Dissolve in
hl.animation({ leaf = "layers", enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersIn", enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersOut", enabled = true, speed = 2, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "fadeLayersIn", enabled = true, speed = 3, bezier = "sharpFade" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 2, bezier = "sharpFade" })

-- Workspaces: Cross-Dissolve
hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "linear", style = "fade" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 6, bezier = "linear", style = "fade" })
