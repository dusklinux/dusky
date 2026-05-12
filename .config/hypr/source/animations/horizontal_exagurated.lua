-- -----------------------------------------------------
-- GELATIN PRESET: Maximum Wobble (Horizontal)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- 1.7 overshoot is absolutely ridiculous.
hl.curve("boing",     { type = "bezier", points = { {0.4,  0.8},  {0.2, 1.7} } })
-- A curve that dips below zero for "anticipation"
hl.curve("slingshot", { type = "bezier", points = { {0.4, -0.4},  {0,   1.2} } })

-- Windows: Pull back (slingshot) then fly in and wobble (boing)
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 8,  bezier = "boing", style = "popin 10%" })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 8,  bezier = "boing", style = "popin 80%" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 8,  bezier = "boing", style = "slide"     })
-- Border: Even the borders pulse
hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "boing" })
hl.animation({ leaf = "fade",   enabled = true, speed = 5,  bezier = "boing" })
-- Layers: Rubber Waybar
hl.animation({ leaf = "layers", enabled = true, speed = 10, bezier = "boing", style = "slide" })
-- Workspaces: The entire screen is a trampoline (horizontal)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 10, bezier = "boing", style = "slide"     })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 10, bezier = "boing", style = "slidevert" })
