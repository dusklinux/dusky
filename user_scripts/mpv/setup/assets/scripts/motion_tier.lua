--[[
  motion_tier.lua - Two-Tier Real-Time Motion Controller for mpv 0.42.0+
  Manages atomic transitions between:
    - "tier1"         : Native display-resample + tscale=oversample (Zero-copy hwdec preserved)
    - "tier2_auto"    : VapourSynth Auto (RIFE Vulkan -> MVTools fallback) + hwdec=auto-copy
    - "tier2_rife"    : VapourSynth RIFE Vulkan + hwdec=auto-copy
    - "tier2_mvtools" : VapourSynth MVTools AVX2 + hwdec=auto-copy
    - "off"           : Interpolation disabled, native zero-copy hwdec restored
]]

local mp = require("mp")
local msg = require("mp.msg")

local FILTER_LABEL = "@motion_tier2"
local VPY_PATH = "~~/vapoursynth/motion_interpolate.vpy"

local function is_rife_installed()
    local static_paths = {
        "/usr/lib/vapoursynth/librife.so",
        "/usr/local/lib/vapoursynth/librife.so",
    }
    for _, p in ipairs(static_paths) do
        local f = io.open(p, "r")
        if f then
            f:close()
            return true
        end
    end

    -- Dynamically discover plugin in Python site-packages without hardcoding version numbers
    local pipe = io.popen("find /usr/lib/python* -maxdepth 4 -name librife.so 2>/dev/null", "r")
    if pipe then
        local found = pipe:read("*l")
        pipe:close()
        if found and found ~= "" then
            return true
        end
    end

    return false
end

local RIFE_AVAILABLE = is_rife_installed()

local function resolve_vpy_path()
    local opt = mp.get_opt("motion_tier-vpy_path")
    if opt and opt ~= "" then
        return opt
    end

    local expanded = mp.command_native({"expand-path", VPY_PATH})
    if expanded and expanded:sub(1, 1) == "/" then
        local f = io.open(expanded, "r")
        if f then
            f:close()
            return expanded
        end
    end

    local sdir = mp.get_script_directory()
    if sdir and sdir ~= "" then
        local candidate = sdir .. "/../vapoursynth/motion_interpolate.vpy"
        local f = io.open(candidate, "r")
        if f then
            f:close()
            return candidate
        end
    end

    local user_cfg = mp.command_native({"expand-path", "~/.config/mpv/vapoursynth/motion_interpolate.vpy"})
    if user_cfg then
        local f = io.open(user_cfg, "r")
        if f then
            f:close()
            return user_cfg
        end
    end

    return expanded or VPY_PATH
end

local state = {
    mode = "tier1",
    target = "display",
    base_hwdec = nil,
}

local function publish_state()
    mp.set_property("user-data/motion-tier/mode", state.mode)
    mp.set_property("user-data/motion-tier/target", state.target)
end

local function capture_base_hwdec()
    if state.base_hwdec == nil then
        local current = mp.get_property("hwdec", "auto-safe")
        if current and current ~= "" and current ~= "auto-copy" then
            state.base_hwdec = current
        else
            state.base_hwdec = "vaapi"
        end
    end
end

local function has_real_video_track()
    local vtrack = mp.get_property_native("current-tracks/video")
    if not vtrack then
        return false
    end
    if vtrack.image or vtrack.albumart then
        return false
    end
    return true
end

local function has_tier2_filter()
    local vf_list = mp.get_property_native("vf", {})
    if type(vf_list) ~= "table" then
        return false
    end
    for _, vf in ipairs(vf_list) do
        if vf.label == "motion_tier2" then
            return true
        end
    end
    return false
end

local function remove_tier2_filter()
    local guard = 0
    while has_tier2_filter() and guard < 8 do
        mp.commandv("vf", "remove", FILTER_LABEL)
        guard = guard + 1
    end
end

local function apply_off(quiet)
    capture_base_hwdec()
    remove_tier2_filter()
    if state.base_hwdec then
        mp.set_property("hwdec", state.base_hwdec)
    end
    mp.set_property("interpolation", "no")
    state.mode = "off"
    publish_state()
    if not quiet then
        mp.osd_message("[Motion] Off (Native Cadence)", 2.0)
    end
end

local function apply_tier1(quiet)
    capture_base_hwdec()
    remove_tier2_filter()
    if state.base_hwdec then
        mp.set_property("hwdec", state.base_hwdec)
    end
    mp.set_property("video-sync", "display-resample")
    mp.set_property("tscale", "oversample")
    mp.set_property("tscale-clamp", "0.0")
    mp.set_property("interpolation", "yes")
    state.mode = "tier1"
    publish_state()
    if not quiet then
        mp.osd_message("[Motion] Tier 1: Display-Sync (tscale=oversample)", 2.0)
    end
end

local function apply_tier2(engine, quiet)
    capture_base_hwdec()
    remove_tier2_filter()

    -- If no real video stream is loaded (e.g. audio-only track), record desired mode without injecting vf
    if mp.get_property("path") and not has_real_video_track() then
        if state.base_hwdec then
            mp.set_property("hwdec", state.base_hwdec)
        end
        mp.set_property("interpolation", "no")
        state.mode = "tier2_" .. engine
        publish_state()
        return true
    end

    local resolved_vpy = resolve_vpy_path()
    local use_mvt_queues = (engine == "mvtools") or (engine == "auto" and not RIFE_AVAILABLE)
    local buf_frames = use_mvt_queues and 12 or 8
    local conc_frames = use_mvt_queues and 8 or 3

    -- 1. Disable Tier 1 tscale to prevent double-interpolation blur
    mp.set_property("video-sync", "display-resample")
    mp.set_property("interpolation", "no")

    -- 2. Switch to copy-back hwdec so VapourSynth receives CPU-accessible planes
    mp.set_property("hwdec", "auto-copy")

    -- 3. Inject labeled VapourSynth filter with user-data config
    local user_data = string.format("engine=%s,target=%s", engine, state.target)
    local filter_spec = string.format(
        '%s:vapoursynth=file="%s":buffered-frames=%d:concurrent-frames=%d:user-data="%s"',
        FILTER_LABEL,
        resolved_vpy,
        buf_frames,
        conc_frames,
        user_data
    )

    local ok, err = mp.commandv("vf", "add", filter_spec)
    if not ok or not has_tier2_filter() then
        msg.warn("Tier 2 filter injection failed (" .. tostring(err) .. "), reverting to Tier 1.")
        apply_tier1(true)
        if not quiet then
            mp.osd_message("[Motion] Tier 2 unavailable -> Reverted to Tier 1", 3.0)
        end
        return false
    end

    state.mode = "tier2_" .. engine
    publish_state()
    if not quiet then
        local c_fps = mp.get_property_number("container-fps", 0)
        local d_fps = mp.get_property_number("display-fps", 60)
        local src_str = c_fps > 0 and string.format("%.1f fps", c_fps) or "source"
        local dst_str = (state.target == "display") and string.format("%.0f Hz", d_fps)
            or (state.target == "2x" and (c_fps > 0 and string.format("%.1f fps", c_fps * 2) or "2x")
            or state.target .. " fps")
        local label = (engine == "auto") and (RIFE_AVAILABLE and "GPU RIFE (Vulkan AI)" or "CPU AVX2 (MVTools)")
            or (engine == "rife" and "GPU RIFE (Vulkan AI)" or "CPU AVX2 (MVTools)")
        mp.osd_message(string.format("[Motion] Tier 2: %s\n%s -> %s", label, src_str, dst_str), 2.5)
    end
    return true
end

local function set_mode(mode_name, quiet)
    mode_name = tostring(mode_name or "tier1"):lower()
    if mode_name == "off" or mode_name == "none" then
        apply_off(quiet)
    elseif mode_name == "tier1" or mode_name == "tscale" then
        apply_tier1(quiet)
    elseif mode_name == "tier2" or mode_name == "tier2_auto" or mode_name == "auto" then
        apply_tier2("auto", quiet)
    elseif mode_name == "tier2_rife" or mode_name == "rife" then
        apply_tier2("rife", quiet)
    elseif mode_name == "tier2_mvtools" or mode_name == "mvtools" then
        apply_tier2("mvtools", quiet)
    else
        msg.warn("Unknown motion_tier mode: " .. mode_name)
    end
end

local function toggle_engine(engine)
    local desired = "tier2_" .. engine
    if state.mode == desired then
        apply_tier1(false)
    else
        apply_tier2(engine, false)
    end
end

local function cycle_tier()
    local order = RIFE_AVAILABLE
        and {"tier1", "tier2_auto", "tier2_mvtools", "off"}
        or {"tier1", "tier2_auto", "off"}
    local next_idx = 1
    for i, m in ipairs(order) do
        if m == state.mode then
            next_idx = (i % #order) + 1
            break
        end
    end
    set_mode(order[next_idx], false)
end

local function toggle_tier1()
    if state.mode == "tier1" then
        apply_off(false)
    else
        apply_tier1(false)
    end
end

local function cycle_target()
    local targets = {"display", "2x", "60", "120"}
    local next_idx = 1
    for i, t in ipairs(targets) do
        if t == state.target then
            next_idx = (i % #targets) + 1
            break
        end
    end
    state.target = targets[next_idx]
    publish_state()
    if state.mode:find("^tier2_") then
        local eng = state.mode:gsub("^tier2_", "")
        apply_tier2(eng, false)
    else
        mp.osd_message(string.format("[Motion] Target FPS: %s", state.target), 2.0)
    end
end

-- Initialize Tier 1 state on load
mp.register_event("file-loaded", function()
    capture_base_hwdec()
    if not has_real_video_track() then
        remove_tier2_filter()
        if state.base_hwdec then
            mp.set_property("hwdec", state.base_hwdec)
        end
        mp.set_property("interpolation", "no")
        publish_state()
        return
    end

    if state.mode == "tier1" then
        apply_tier1(true)
    elseif state.mode:find("^tier2_") then
        local eng = state.mode:gsub("^tier2_", "")
        apply_tier2(eng, true)
    elseif state.mode == "off" then
        apply_off(true)
    end
end)

capture_base_hwdec()
publish_state()

-- Keybindings & Script Messages for IPC / input.conf
mp.add_key_binding(nil, "toggle-tier2-auto", function() toggle_engine("auto") end)
mp.add_key_binding(nil, "toggle-tier2-rife", function() toggle_engine("rife") end)
mp.add_key_binding(nil, "toggle-tier2-mvtools", function() toggle_engine("mvtools") end)
mp.add_key_binding(nil, "cycle-motion-tier", cycle_tier)
mp.add_key_binding(nil, "toggle-tier1-sync", toggle_tier1)
mp.add_key_binding(nil, "cycle-target", cycle_target)

mp.register_script_message("set-mode", function(m) set_mode(m, false) end)
mp.register_script_message("set-target", function(t)
    state.target = tostring(t or "display"):lower()
    publish_state()
    if state.mode:find("^tier2_") then
        local eng = state.mode:gsub("^tier2_", "")
        apply_tier2(eng, false)
    end
end)
mp.register_script_message("cycle-tier", cycle_tier)
mp.register_script_message("cycle-target", cycle_target)
