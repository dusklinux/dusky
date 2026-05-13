-- -----------------------------------------------------
-- AIR PRESET: Floaty, Soft, Ethereal (Vertical)
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- Slow start, slow end. Like a feather falling.
hl.curve("soft",   { type = "bezier", points = { {0.3, 0.3}, {0.2, 1} } })
hl.curve("softIn", { type = "bezier", points = { {0.4, 0},   {1,   1} } })

-- Windows: Drift in with opacity changes
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 8,  bezier = "soft",   style = "slidefade 15%"  })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 8,  bezier = "softIn", style = "slidefade 15%"  })
hl.animation({ leaf = "windowsMove", enabled = true, speed = 8,  bezier = "soft",   style = "slidefade 15%"  })
-- Border & Fade: Very slow transition
hl.animation({ leaf = "border", enabled = true, speed = 10, bezier = "soft" })
hl.animation({ leaf = "fade",   enabled = true, speed = 10, bezier = "soft" })
-- Layers: Gentle drift
hl.animation({ leaf = "layers", enabled = true, speed = 6, bezier = "soft", style = "slidefade 10%" })
-- Workspaces: Elevator in the Clouds (vertical)
hl.animation({ leaf = "workspaces",       enabled = true, speed = 10, bezier = "soft", style = "slidefadevert 40%" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 10, bezier = "soft", style = "slidefade 40%"     })
