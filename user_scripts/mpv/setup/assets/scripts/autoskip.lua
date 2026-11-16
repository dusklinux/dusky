-- autoskip.lua - Event-driven, opt-in chapter & intro auto-skipping with 1-key undo
-- Target: mpv 0.42.0+ (purely reactive property observation, zero background polling)

local msg = require('mp.msg')

local enabled = false
local last_skip_origin = nil
local chain_target_chapter = nil
local skipped_chapters = {}
local suppressed_chapters = {}

local skip_patterns = {
    "^intro$", "^intro%s*[%d]+", "^intro%s*[%p]+", "^intro%s*theme",
    "^opening$", "^opening%s*[%d]+", "^opening%s*[%p]+", "^opening%s*theme", "^opening%s*credits", "^opening%s*song",
    "^op$", "^op%s*[%d]+", "^op%s*[%p]+", "^op%s*theme",
    "^ending$", "^ending%s*[%d]+", "^ending%s*[%p]+", "^ending%s*theme", "^ending%s*credits", "^ending%s*song",
    "^ed$", "^ed%s*[%d]+", "^ed%s*[%p]+", "^ed%s*theme",
    "^credits$", "^credits%s*[%d]+", "^credits%s*[%p]+",
    "^preview$", "^preview%s*[%d]+", "^preview%s*[%p]+",
    "^recap$", "^recap%s*[%d]+", "^recap%s*[%p]+",
}

local function should_skip_title(title)
    if not title or type(title) ~= "string" then return false end
    local lower = title:lower():match("^%s*(.-)%s*$")
    for _, pattern in ipairs(skip_patterns) do
        if lower:match(pattern) then
            return true
        end
    end
    return false
end

local function on_chapter_change(_, current_chapter)
    if not enabled or current_chapter == nil or current_chapter < 0 then return end
    if suppressed_chapters[current_chapter] or skipped_chapters[current_chapter] then return end

    local chapters = mp.get_property_native("chapter-list", {})
    if #chapters == 0 or not chapters[current_chapter + 1] then return end

    local cur = chapters[current_chapter + 1]
    local title = cur.title or ""

    if should_skip_title(title) then
        local next_chapter = current_chapter + 1
        if next_chapter < #chapters then
            local next_entry = chapters[next_chapter + 1]
            local target_time = next_entry.time
            if target_time then
                local chap_start = cur.time or 0
                local pos = mp.get_property_number("time-pos") or chap_start
                local origin_pos = (pos >= chap_start and pos < target_time) and pos or chap_start
                -- Start a new undo record unless we are chaining directly from the previous skip
                if current_chapter ~= chain_target_chapter or last_skip_origin == nil then
                    last_skip_origin = origin_pos
                    skipped_chapters = {}
                    suppressed_chapters = {}
                end
                skipped_chapters[current_chapter] = true
                chain_target_chapter = next_chapter
                mp.set_property_number("chapter", next_chapter)
                mp.osd_message(string.format("Auto-skipped '%s' (Press Backspace to undo)", title), 3)
                msg.info(string.format("Auto-skipped chapter %d ('%s') -> Chapter %d", current_chapter, title, next_chapter))
            end
        end
    else
        -- Landed on a normal chapter; end the active chain but keep last_skip_origin & skipped_chapters for Backspace undo
        chain_target_chapter = nil
    end
end

local function undo_skip()
    if last_skip_origin ~= nil then
        local origin = last_skip_origin
        suppressed_chapters = skipped_chapters
        skipped_chapters = {}
        last_skip_origin = nil
        chain_target_chapter = nil
        mp.commandv("seek", origin, "absolute+exact")
        mp.osd_message("Reverted auto-skip", 2)
        msg.info(string.format("Reverted auto-skip back to %.2fs", origin))
    else
        mp.commandv("set", "speed", "1.0")
    end
end

local function reset_file_state()
    last_skip_origin = nil
    chain_target_chapter = nil
    skipped_chapters = {}
    suppressed_chapters = {}
end

local function update_state()
    if enabled then
        mp.observe_property("chapter", "number", on_chapter_change)
        mp.osd_message("Chapter Auto-Skip: ON", 2)
        msg.info("Chapter Auto-Skip enabled")
    else
        mp.unobserve_property(on_chapter_change)
        reset_file_state()
        mp.osd_message("Chapter Auto-Skip: OFF", 2)
        msg.info("Chapter Auto-Skip disabled")
    end
end

local function toggle()
    enabled = not enabled
    update_state()
end

mp.add_key_binding(nil, "toggle", toggle)
mp.add_key_binding(nil, "undo", undo_skip)
mp.register_event("start-file", reset_file_state)
mp.register_event("end-file", reset_file_state)
