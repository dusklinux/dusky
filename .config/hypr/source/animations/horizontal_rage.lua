-- -----------------------------------------------------
-- SLAP PRESET: Aggressive, Instant Impact (Horizontal)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- Starts at 0, goes straight to 1. No easing.
hl.curve("linear", { type = "bezier", points = { {0, 0},   {1,   1}   } })
-- Accelerate only
hl.curve("accel",  { type = "bezier", points = { {1, 0},   {1,   0.5} } })

-- Windows: Fast entry (3), hard stop.
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 3, bezier = "accel",  style = "slide" })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 3, bezier = "accel",  style = "slide" })
-- Windows Move: Zero smoothing.
hl.animation({ leaf = "windowsMove", enabled = true, speed = 2, bezier = "linear", style = "slide" })
-- Border
hl.animation({ leaf = "border", enabled = true, speed = 1, bezier = "linear" })
hl.animation({ leaf = "fade",   enabled = true, speed = 2, bezier = "linear" })
-- Layers
hl.animation({ leaf = "layers", enabled = true, speed = 2, bezier = "accel", style = "slide" })
-- Workspaces: Fast horizontal slam
hl.animation({ leaf = "workspaces",       enabled = true, speed = 3, bezier = "accel", style = "slide"     })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 3, bezier = "accel", style = "slidevert" })
