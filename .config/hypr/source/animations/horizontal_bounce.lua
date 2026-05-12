-- -----------------------------------------------------
-- POP PRESET: Bouncy, Gelatinous, Fun (Horizontal)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- High bounce (1.3 = 30% overshoot). Very exaggerated.
hl.curve("jelly",  { type = "bezier", points = { {0.1, 0.9}, {0.1, 1.3} } })
hl.curve("bounce", { type = "bezier", points = { {0.1, 1.5}, {0.2, 1.1} } })

-- Windows: Pop in from the center like bubbles
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 6, bezier = "jelly",  style = "popin 60%" })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 4, bezier = "bounce", style = "popin 60%" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 6, bezier = "jelly",  style = "slide"     })
-- Border
hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "jelly" })
hl.animation({ leaf = "fade",   enabled = true, speed = 5,  bezier = "jelly" })
-- Layers
hl.animation({ leaf = "layers", enabled = true, speed = 6, bezier = "jelly", style = "popin 10%" })
-- Workspaces: Trampoline effect (horizontal)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 7, bezier = "jelly", style = "slide"     })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 7, bezier = "jelly", style = "slidevert" })
