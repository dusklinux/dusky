-- -----------------------------------------------------
-- RIGID PRESET: Mechanical, Stiff, Precision (Vertical)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- The "Block" - constant speed then instant stop.
hl.curve("hard",   { type = "bezier", points = { {0,   1},   {0,   1}   } })
-- The "Piston" - fast acceleration, hard brake.
hl.curve("piston", { type = "bezier", points = { {0.5, 0},   {0.5, 1}   } })

hl.animation({ leaf = "windows",     enabled = true, speed = 4, bezier = "hard",   style = "slide" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 4, bezier = "piston", style = "slide" })
hl.animation({ leaf = "border",      enabled = true, speed = 1, bezier = "hard"                    })
hl.animation({ leaf = "fade",        enabled = true, speed = 2, bezier = "hard"                    })
hl.animation({ leaf = "layers",      enabled = true, speed = 3, bezier = "hard",   style = "slide" })
-- Workspaces: Hydraulic Lift (vertical)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 5, bezier = "hard", style = "slidevert" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 5, bezier = "hard", style = "slide"     })
