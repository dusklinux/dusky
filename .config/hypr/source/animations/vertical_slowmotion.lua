-- -----------------------------------------------------
-- DRAMA PRESET: Slow Motion Cinematic (Vertical)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- Starts incredibly slow, rushes in the middle, slows down again.
hl.curve("slowmo", { type = "bezier", points = { {0.85, 0}, {0.15, 1} } })

-- Windows: ~1.5 seconds. It feels like forever.
hl.animation({ leaf = "windows",     enabled = true, speed = 15, bezier = "slowmo", style = "slide" })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 15, bezier = "slowmo", style = "slide" })
-- Border: A slow, ominous color change
hl.animation({ leaf = "border", enabled = true, speed = 20, bezier = "slowmo" })
hl.animation({ leaf = "fade",   enabled = true, speed = 20, bezier = "slowmo" })
-- Layers
hl.animation({ leaf = "layers", enabled = true, speed = 12, bezier = "slowmo", style = "slide" })
-- Workspaces: Grand, sweeping scene transition (vertical)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 20, bezier = "slowmo", style = "slidevert" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 20, bezier = "slowmo", style = "slide"     })
