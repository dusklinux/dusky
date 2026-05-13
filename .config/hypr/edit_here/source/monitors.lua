
-- Global Settings
hl.config({ debug = { vfr = true } })

-- Monitor Rules
hl.monitor({ output = "DP-1", mode = "1920x1080@144.00", position = "0x0", scale = 1.00, bitdepth = 10 })
hl.monitor({ output = "HEADLESS-2", disabled = true })
