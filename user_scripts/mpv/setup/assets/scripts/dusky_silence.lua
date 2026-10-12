-- Pocket Casts-inspired silence trimming through stock mpv/libavfilter.
-- This is a moving-window approximation, not a port of its PCM buffer engine.
local mp = require 'mp'
local msg = require 'mp.msg'
local options = {enabled = true, level = 'max'}
local label = 'dusky_silence'
local loaded = false
local file_duration
local file_start = 0
local pending
local failed = false

-- Pocket Casts uses 1152-sample blocks, RMS thresholds below, and retains
-- 28/24/1 blocks in long gaps. silenceremove keeps stop_duration plus roughly
-- one RMS window. Max conservatively keeps four blocks to preserve short
-- pauses: unlike the original, this filter cannot retrospectively cut a gap.
-- Optional Max Plus retains roughly one window, shortening brief pauses too.
local levels = {
    mild = {rms = 0.0055, blocks = 28},
    medium = {rms = 0.00511, blocks = 24},
    max = {rms = 0.005, blocks = 4},
    ['max-plus'] = {rms = 0.005, blocks = 1},
}

local function validate_level()
    if not levels[options.level] then
        msg.warn('Unknown silence level; using max')
        options.level = 'max'
    end
end

local function apply(preload)
    local filters = mp.get_property_native('af', {})
    local desired
    local params = mp.get_property_native('audio-params')
    local rate = params and params.samplerate
    local duration = file_duration
    local audio = false
    local video = false
    local codec
    local audio_tracks = {}
    for _, track in ipairs(mp.get_property_native('track-list', {})) do
        if track.type == 'audio' then audio_tracks[#audio_tracks + 1] = track end
        if track.type == 'audio' and track.selected then
            audio = true
            codec = track.codec
            rate = rate or track['demux-samplerate']
        end
        -- Even with vid=no, a video file stays excluded. Cover art is allowed.
        if track.type == 'video' and not track.albumart and not track.image then
            video = true
        end
    end
    -- on_preloaded precedes track selection. A single track is unambiguous;
    -- multiple tracks wait for actual decoder parameters rather than guessing
    -- mpv's language/default/explicit-ID selection rules.
    if preload and not audio and #audio_tracks == 1 and
        mp.get_property('aid') ~= 'no' then
        audio = true
        codec = audio_tracks[1].codec
        rate = audio_tracks[1]['demux-samplerate']
    end
    local passthrough = false
    for name in mp.get_property('options/audio-spdif', ''):gmatch('[^,]+') do
        if not codec or name == codec or (codec == 'dts' and name == 'dts-hd') then
            passthrough = true
        end
    end
    local profile = levels[options.level]
    if loaded and not failed and options.enabled and profile and audio and
        not video and not passthrough and rate and rate > 0 and
        not (params and params.format or ''):match('^spdif%-') and
        (not duration or duration > 6) then
        local block = 1152 / rate
        local graph = string.format(
            'silenceremove=stop_periods=-1:stop_duration=%.9f:' ..
            'stop_threshold=%.8f:stop_mode=all:detection=rms:' ..
            'window=%.9f:timestamp=copy',
            (profile.blocks - 1) * block, profile.rms, block)
        -- Source uses integer seconds and protects roughly the last 5–6s.
        -- No final-duration assumption is possible for a live/unknown stream.
        if duration then
            graph = graph .. string.format(":enable='lt(t,%.6f)'", file_start + duration - 6)
        end
        -- Preserve source timestamps for seeking, chapters and resume positions.
        desired = {name = 'lavfi', label = label,
            params = {graph = graph, ['fix-pts'] = 'no'}}
    end

    local existing
    local next_filters = {}
    for _, filter in ipairs(filters) do
        if filter.label == label then
            existing = filter
        else
            next_filters[#next_filters + 1] = filter
        end
    end
    if not desired and not existing then return end
    if desired and existing and existing.enabled ~= false and
        existing.name == desired.name and
        (existing.params or {}).graph == desired.params.graph and
        (existing.params or {})['fix-pts'] == desired.params['fix-pts'] then return end
    if desired then table.insert(next_filters, 1, desired) end
    local ok, err = mp.set_property_native('af', next_filters)
    if not ok then
        failed = true
        msg.error('Cannot configure silence trimming: ' .. tostring(err))
    elseif desired then
        msg.info('Trim silence: ' .. options.level)
    end
end

local function schedule()
    if pending then return end
    pending = mp.add_timeout(0, function()
        pending = nil
        apply()
    end)
end

require('mp.options').read_options(options, 'dusky_silence', function()
    validate_level()
    failed = false
    schedule()
end)
validate_level()

local function announce()
    local text = options.enabled and ('Trim silence: ' .. options.level) or
        'Trim silence: off'
    mp.osd_message(text, 2)
end

mp.add_key_binding(nil, 'toggle', function()
    options.enabled = not options.enabled
    failed = false
    schedule()
    announce()
end)
mp.add_key_binding(nil, 'cycle', function()
    if not options.enabled then
        options.enabled = true
        options.level = 'mild'
    elseif options.level == 'mild' then
        options.level = 'medium'
    elseif options.level == 'medium' then
        options.level = 'max'
    elseif options.level == 'max' then
        options.level = 'max-plus'
    else
        options.enabled = false
    end
    failed = false
    schedule()
    announce()
end)
mp.register_event('start-file', function()
    loaded = false
    file_duration = nil
    file_start = 0
    failed = false
    schedule()
end)
-- Use the hook supported by the installed mpv 0.41 package. Newer source has
-- an on_loaded hook too, but that interface is not part of our baseline.
mp.add_hook('on_preloaded', 50, function()
    loaded = true
    -- Snapshot metadata duration once. A pipe/live stream's duration can later
    -- become a growing estimate of buffered data, not its final length. Using
    -- those updates would repeatedly rebuild the filter and lose trimming.
    file_duration = mp.get_property_number('duration')
    if file_duration and file_duration <= 0 then file_duration = nil end
    if not mp.get_property_native('options/rebase-start-time', true) then
        file_start = mp.get_property_number('demuxer-start-time', 0)
    end
    apply(true)
end)
mp.register_event('file-loaded', function() apply() end)
mp.register_event('end-file', function()
    loaded = false
    schedule()
end)
mp.observe_property('audio-params', 'native', schedule)
mp.observe_property('track-list', 'native', schedule)
