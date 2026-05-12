-- -----------------------------------------------------
-- FADE PRESET: Pure Opacity / Ethereal
-- -----------------------------------------------------

hl.config({ animations = { enabled = true } })

hl.curve("sine",      { type = "bezier", points = { {0.5,  0.5}, {0.5,  0.5} } })
hl.curve("sharpFade", { type = "bezier", points = { {0.33, 1},   {0.68, 1}   } })
hl.curve("linear",    { type = "bezier", points = { {0,    0},   {1,    1}   } })

-- Windows: popin 100% (no scaling) so the actual visual fade comes from 'fade'
hl.animation({ leaf = "windows",     enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsIn",   enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
hl.animation({ leaf = "windowsOut",  enabled = true, speed = 3, bezier = "sharpFade", style = "popin 100%" })
-- Windows Move: Needs to slide to feel natural
hl.animation({ leaf = "windowsMove", enabled = true, speed = 4, bezier = "sine",      style = "slide"      })
-- Border: Pulse effect
hl.animation({ leaf = "border",        enabled = true, speed = 5, bezier = "sine"                  })
hl.animation({ leaf = "fade",          enabled = true, speed = 5, bezier = "sine"                  })
-- Layers: Dissolve in
hl.animation({ leaf = "layers",        enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersIn",      enabled = true, speed = 4, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 2, bezier = "sharpFade", style = "fade" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 3, bezier = "sharpFade"               })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 2, bezier = "sharpFade"               })
-- Workspaces: Cross-Dissolve
hl.animation({ leaf = "workspaces",       enabled = true, speed = 6, bezier = "sine", style = "fade" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 6, bezier = "sine", style = "fade" })
