-- ----------------------------------------------------- 
-- FAST PRESET: High Performance / Low Latency
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

hl.curve("linear", { type = "bezier", points = { {0, 0}, {1, 1} } })
hl.curve("md3_decel", { type = "bezier", points = { {0.05, 0.7}, {0.1, 1} } })

hl.animation({ leaf = "windows", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsIn", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })

hl.animation({ leaf = "border", enabled = true, speed = 1, bezier = "linear" })
hl.animation({ leaf = "fade", enabled = true, speed = 2, bezier = "md3_decel" })
hl.animation({ leaf = "layers", enabled = true, speed = 2, bezier = "md3_decel", style = "slide" })

-- Workspace direction is selected by hypr_anim.sh.
local vertical = false -- orientation
hl.animation({ leaf = "workspaces", enabled = true, speed = 2.5, bezier = "md3_decel", style = vertical and "slidevert" or "slide" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 2.5, bezier = "md3_decel", style = vertical and "slide" or "slidevert" })
