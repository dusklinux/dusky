-- ----------------------------------------------------- 
-- POP PRESET: Bouncy, Gelatinous, Fun
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

hl.curve("jelly", { type = "bezier", points = { {0.1, 0.9}, {0.1, 1.3} } })
hl.curve("bounce", { type = "bezier", points = { {0.1, 1.5}, {0.2, 1.1} } })

hl.animation({ leaf = "windowsIn", enabled = true, speed = 6, bezier = "jelly", style = "popin 60%" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 4, bezier = "bounce", style = "popin 60%" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 6, bezier = "jelly", style = "slide" })

hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "jelly" })
hl.animation({ leaf = "fade", enabled = true, speed = 5, bezier = "jelly" })
hl.animation({ leaf = "layers", enabled = true, speed = 6, bezier = "jelly", style = "popin 10%" })

-- Workspace direction is selected by hypr_anim.sh.
local vertical = false -- orientation
hl.animation({ leaf = "workspaces", enabled = true, speed = 7, bezier = "jelly", style = vertical and "slidevert" or "slide" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 7, bezier = "jelly", style = vertical and "slide" or "slidevert" })
