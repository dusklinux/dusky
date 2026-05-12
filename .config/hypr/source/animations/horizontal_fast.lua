-- -----------------------------------------------------
-- FAST PRESET: High Performance / Low Latency (Horizontal)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

hl.curve("linear",    { type = "bezier", points = { {0,    0},   {1,   1}   } })
hl.curve("md3_decel", { type = "bezier", points = { {0.05, 0.7}, {0.1, 1}   } })
hl.curve("instant",   { type = "bezier", points = { {0,    1},   {0,   1}   } })

-- Windows: Instant snap with barely visible deceleration (~30ms)
hl.animation({ leaf = "windows",     enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
-- Border: Instant feedback
hl.animation({ leaf = "border", enabled = true, speed = 1,   bezier = "linear"    })
hl.animation({ leaf = "fade",   enabled = true, speed = 2,   bezier = "md3_decel" })
-- Layers: Snappy but not jarring
hl.animation({ leaf = "layers", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
-- Workspaces: Fast horizontal slide (~200ms)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 2.5, bezier = "md3_decel", style = "slide"     })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 2.5, bezier = "md3_decel", style = "slidevert" })
