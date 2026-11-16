-- shader_presets.lua - Opt-in video upscaling shader preset switcher
-- Universal GLSL shader presets for modern GPUs (AMD, NVIDIA, Intel - OpenGL 4.3+ / vo=gpu-next)

local msg = require('mp.msg')

local presets = {
    {
        name = "Off",
        desc = "Native Spline36 scaling",
        shaders = {},
    },
    {
        name = "Live-Action (RAVU)",
        desc = "RAVU-Zoom-AR-R3 - Anti-ringing directional reconstruction",
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
        local status_note = ""
        if #preset.shaders > 0 then
            local is_fs = mp.get_property_bool("fullscreen", false)
            local win_scale = mp.get_property_number("current-window-scale", 1.0)
            if is_fs or (win_scale and win_scale > 1.05) then
                status_note = " [Active: Upscaling]"
            else
                status_note = " [Ready: Active in Fullscreen or 2x Scale (Alt+2)]"
            end
        end
        local msg_text = string.format("Upscale Preset [%d/%d]: %s%s\n%s", current_idx, #presets, preset.name, status_note, preset.desc)
        mp.osd_message(msg_text, 3.0)
        msg.info("Applied shader preset: " .. preset.name)
    end
end

local function toggle_preset()
    if current_idx == 1 then
        apply_preset(2, true)
    else
        apply_preset(1, true)
    end
end

local function cycle_preset()
    apply_preset(current_idx + 1, true)
end

local function reset_preset()
    apply_preset(1, true)
end

mp.add_key_binding(nil, 'toggle-preset', toggle_preset)
mp.add_key_binding(nil, 'cycle-preset', cycle_preset)
mp.add_key_binding(nil, 'reset-preset', reset_preset)
