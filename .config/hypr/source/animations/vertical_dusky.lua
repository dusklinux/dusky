-- -----------------------------------------------------
-- FLUID Dusky: The "Showcase" Edition (Vertical)
-- -----------------------------------------------------
-- Tuned daily driving: Slower, cinematic, and
-- perfectly fluid.
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

-- Goes past the target (1.1) and snaps back.
hl.curve("overshot",   { type = "bezier", points = { {0.05, 0.9}, {0.1,  1.1}  } })
-- Standard ease-out. No bounce, just a clean stop.
hl.curve("fluid",      { type = "bezier", points = { {0.25, 1},   {0,    1}    } })
-- A slightly tighter curve than fluid, good for closing things.
hl.curve("snap",       { type = "bezier", points = { {0.5,  0.9}, {0.1,  1.05} } })
-- Starts fast, decelerates very slowly.
hl.curve("menu_decel", { type = "bezier", points = { {0.1,  1},   {0,    1}    } })
-- Constant speed, no acceleration.
hl.curve("liner",      { type = "bezier", points = { {1,    1},   {1,    1}    } })

hl.animation({ leaf = "windowsIn",     enabled = true,  speed = 7,  bezier = "overshot",  style = "popin 80%" })
hl.animation({ leaf = "windowsOut",    enabled = true,  speed = 5,  bezier = "snap",      style = "popin 80%" })
hl.animation({ leaf = "windowsMove",   enabled = true,  speed = 7,  bezier = "overshot",  style = "slide"     })
hl.animation({ leaf = "border",        enabled = true,  speed = 2,  bezier = "liner"                          })
hl.animation({ leaf = "borderangle",   enabled = true,  speed = 40, bezier = "liner",     style = "once"      })
hl.animation({ leaf = "fade",          enabled = true,  speed = 5,  bezier = "fluid"                          })
hl.animation({ leaf = "layersIn",      enabled = true,  speed = 6,  bezier = "overshot",  style = "popin 70%" })
hl.animation({ leaf = "layersOut",     enabled = false, speed = 0                                              })
hl.animation({ leaf = "fadeLayersIn",  enabled = true,  speed = 5,  bezier = "menu_decel"                     })
hl.animation({ leaf = "fadeLayersOut", enabled = true,  speed = 4,  bezier = "menu_decel"                     })
-- Vertical workspaces
hl.animation({ leaf = "workspaces",    enabled = true,  speed = 8,  bezier = "overshot",  style = "slidevert" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 8, bezier = "overshot", style = "slide"     })
