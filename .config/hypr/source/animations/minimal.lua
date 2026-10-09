-- ----------------------------------------------------- 
-- MINIMAL PRESET: Clean, Snappy, Functional
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

hl.curve("pro", { type = "bezier", points = { {0.05, 0.9}, {0.1, 1.0} } })
hl.curve("snap", { type = "bezier", points = { {0.05, 0.9}, {0.1, 1.05} } })

hl.animation({ leaf = "windows", enabled = true, speed = 3, bezier = "snap", style = "popin 80%" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 3, bezier = "snap", style = "popin 80%" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 3, bezier = "snap" })

hl.animation({ leaf = "layers", enabled = true, speed = 2, bezier = "pro", style = "slide" })
hl.animation({ leaf = "fade", enabled = true, speed = 2, bezier = "pro" })
hl.animation({ leaf = "border", enabled = true, speed = 3, bezier = "pro" })

-- Fix for screenshot gray capture (Disables the out animation completely)
hl.animation({ leaf = "layersOut", enabled = false })

-- Workspace direction is selected by hypr_anim.sh.
local vertical = false -- orientation
hl.animation({ leaf = "workspaces", enabled = true, speed = 3, bezier = "pro", style = vertical and "slidevert" or "slide" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 3, bezier = "pro", style = vertical and "slide" or "slidevert" })
