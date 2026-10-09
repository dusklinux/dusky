-- ----------------------------------------------------- 
-- AIR PRESET: Floaty, Soft, Ethereal
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

hl.curve("soft", { type = "bezier", points = { {0.3, 0.3}, {0.2, 1} } })
hl.curve("softIn", { type = "bezier", points = { {0.4, 0}, {1, 1} } })

hl.animation({ leaf = "windowsIn", enabled = true, speed = 8, bezier = "soft", style = "slide" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 8, bezier = "softIn", style = "slide" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 8, bezier = "soft", style = "slide" })

hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "soft" })
hl.animation({ leaf = "fade", enabled = true, speed = 10, bezier = "soft" })
hl.animation({ leaf = "layers", enabled = true, speed = 6, bezier = "soft", style = "slide" })

-- Workspace direction is selected by hypr_anim.sh.
local vertical = false -- orientation
hl.animation({ leaf = "workspaces", enabled = true, speed = 10, bezier = "soft", style = vertical and "slidefadevert 40%" or "slidefade 40%" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 10, bezier = "soft", style = vertical and "slidefade 40%" or "slidefadevert 40%" })
