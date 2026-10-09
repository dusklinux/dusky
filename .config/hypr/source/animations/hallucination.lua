-- ----------------------------------------------------- 
-- TRIP PRESET: Psychedelic, Wavy, Disorienting
-- ----------------------------------------------------- 

hl.config({ animations = { enabled = true } })

hl.curve("hallucination", { type = "bezier", points = { {0.68, -0.55}, {0.265, 1.55} } })
hl.curve("dream", { type = "bezier", points = { {0.4, 0}, {0.2, 1} } })
hl.curve("linear", { type = "bezier", points = { {0, 0}, {1, 1} } })

hl.animation({ leaf = "windowsIn", enabled = true, speed = 9, bezier = "hallucination", style = "popin 0%" })
hl.animation({ leaf = "windowsOut", enabled = true, speed = 9, bezier = "hallucination", style = "popin 0%" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 9, bezier = "hallucination", style = "slide" })

hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "dream" })
-- Intentional continuous border rotation; this preset keeps rendering while idle.
hl.animation({ leaf = "borderangle", enabled = true, speed = 100, bezier = "linear", style = "loop" })
hl.animation({ leaf = "fade", enabled = true, speed = 10, bezier = "dream" })
hl.animation({ leaf = "layers", enabled = true, speed = 8, bezier = "dream", style = "popin 50%" })

-- Workspace direction is selected by hypr_anim.sh.
local vertical = false -- orientation
hl.animation({ leaf = "workspaces", enabled = true, speed = 12, bezier = "dream", style = vertical and "slidefadevert 80%" or "slidefade 80%" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 12, bezier = "dream", style = vertical and "slidefade 80%" or "slidefadevert 80%" })
