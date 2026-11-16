-- shader_presets.lua - Opt-in video upscaling shader preset switcher
-- Hardware optimized for Intel Iris Xe (Mesa EGL / vo=gpu-next / 1080p target)

local msg = require('mp.msg')

local presets = {
    {
        name = "Off",
        desc = "Native scaling (disabled)",
        shaders = {},
    },
    {
        name = "Fast (AMD CAS)",
        desc = "FidelityFX CAS - Ultra-low power / 120fps",
        shaders = { "~~/shaders/CAS-scaled.glsl" },
    },
    {
        name = "Live-Action (RAVU)",
        desc = "RAVU-Zoom-AR-R3 - Anti-ringing directional scaler",
        shaders = { "~~/shaders/ravu-zoom-ar-r3.hook" },
    },
    {
        name = "Anime (FSRCNNX)",
        desc = "FSRCNNX 8-0-4-1 + SSimDownscaler - High-fidelity neural reconstruction",
        shaders = { "~~/shaders/FSRCNNX_x2_8-0-4-1.glsl", "~~/shaders/SSimDownscaler.glsl" },
    },
}

local current_idx = 1

local function apply_preset(idx, notify)
    current_idx = ((idx - 1) % #presets) + 1
    local preset = presets[current_idx]

    mp.set_property_native('glsl-shaders', preset.shaders)

    if notify then
        local msg_text = string.format("Upscale Preset [%d/%d]: %s\n%s", current_idx, #presets, preset.name, preset.desc)
        mp.osd_message(msg_text, 2.5)
        msg.info("Applied shader preset: " .. preset.name)
    end
end

local function cycle_preset()
    apply_preset(current_idx + 1, true)
end

local function reset_preset()
    apply_preset(1, true)
end

mp.add_key_binding(nil, 'cycle-preset', cycle_preset)
mp.add_key_binding(nil, 'reset-preset', reset_preset)
