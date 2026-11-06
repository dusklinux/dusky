-- Dusky Player vector icons, drawn directly by libass. No font assets required.
-- Coordinates use a 24 x 24 view box. Paths are cached after their first use.
local icons = {}
local function polygon(points)
    return {points = points}
end
local function line(points)
    return {points = points, stroke = true}
end
icons.menu = {line({4,6,20,6}), line({4,12,20,12}), line({4,18,20,18})}
icons.play_arrow = {polygon({7,3,21,12,7,21})}
icons.pause = {polygon({5,4,9,4,9,20,5,20}), polygon({15,4,19,4,19,20,15,20})}
icons.list_alt = {line({3,3,21,3,21,21,3,21,3,3}), line({8,7,18,7}), line({8,12,18,12}), line({8,17,18,17}), line({5,7,6,7}), line({5,12,6,12}), line({5,17,6,17})}
icons.subtitles = {line({2,5,22,5,22,19,2,19,2,5}), line({5,11,9,11}), line({12,11,19,11}), line({5,15,14,15}), line({17,15,19,15})}
icons.graphic_eq = {line({4,9,4,15}), line({8,5,8,19}), line({12,2,12,22}), line({16,6,16,18}), line({20,9,20,15})}
icons.bookmark = {line({6,3,18,3,18,21,12,17,6,21,6,3})}
icons.bookmarks = {line({3,6,3,21,9,18}), line({7,2,20,2,20,20,13,16,7,20,7,2})}
icons.arrow_back_ios = {line({16,3,7,12,16,21})}
icons.arrow_forward_ios = {line({8,3,17,12,8,21})}
icons.first_page = {line({5,3,5,21}), line({18,3,9,12,18,21})}
icons.last_page = {line({19,3,19,21}), line({6,3,15,12,6,21})}
icons.crop_free = {line({3,9,3,3,9,3}), line({15,3,21,3,21,9}), line({21,15,21,21,15,21}), line({9,21,3,21,3,15})}
icons.fullscreen_exit = {line({3,9,9,9,9,3}), line({15,3,15,9,21,9}), line({21,15,15,15,15,21}), line({9,21,9,15,3,15})}
icons.close = {line({5,5,19,19}), line({19,5,5,19})}
icons.crop_square = {line({4,4,20,4,20,20,4,20,4,4})}
icons.minimize = {line({4,17,20,17})}
icons.search = {line({19,19,15,15}), line({16,9,15,5,11,3,6,4,3,8,4,13,8,16,13,15,16,11,16,9})}
icons.help = {line({7,7,8,4,12,3,16,5,17,8,15,11,12,13,12,15}), line({12,19,12,20})}
icons.arrow_upward = {line({12,21,12,3}), line({4,11,12,3,20,11})}
icons.arrow_downward = {line({12,3,12,21}), line({4,13,12,21,20,13})}
icons.vertical_align_top = {line({3,3,21,3}), line({12,21,12,7}), line({6,13,12,7,18,13})}
icons.delete = {line({5,6,6,21,18,21,19,6}), line({3,5,21,5}), line({8,5,8,2,16,2,16,5}), line({9,9,9,17}), line({15,9,15,17})}
icons.refresh = {line({19,7,16,4,11,3,6,5,3,10,4,16,8,20,14,21,19,18}), polygon({15,7,22,7,22,1})}
icons['repeat'] = {line({3,10,3,5,20,5}), polygon({16,1,22,5,16,9}), line({21,14,21,19,4,19}), polygon({8,15,2,19,8,23})}
icons.shuffle = {line({3,4,7,4,17,20,21,20}), polygon({17,16,23,20,17,24}), line({3,20,7,20,17,4,21,4}), polygon({17,0,23,4,17,8})}
icons.file_open = {line({2,7,2,21,22,21,22,10,12,10,9,7,2,7}), line({13,5,21,5}), line({17,1,21,5,17,9})}
icons.speaker = {line({5,2,19,2,19,22,5,22,5,2}), line({8,10,16,10,16,18,8,18,8,10}), line({10,5,14,5})}
icons.theaters = {line({3,3,21,3,21,21,3,21,3,3}), line({6,3,6,21}), line({18,3,18,21}), line({3,8,6,8}), line({18,8,21,8}), line({3,16,6,16}), line({18,16,21,16})}
icons.high_quality = {line({2,5,22,5,22,19,2,19,2,5}), line({6,8,6,16}), line({6,12,10,12}), line({10,8,10,16}), line({14,8,18,8,18,15,14,15,14,8}), line({16,13,20,17})}
icons.playlist_add = {line({2,5,15,5}), line({2,11,15,11}), line({2,17,10,17}), line({18,12,18,22}), line({13,17,23,17})}
local speaker = polygon({2,9,6,9,12,4,12,20,6,15,2,15})
icons.volume_up = {speaker, line({15,7,18,10,18,14,15,17}), line({18,3,22,8,23,12,22,16,18,21})}
icons.volume_down = {speaker, line({15,7,18,10,18,14,15,17})}
icons.volume_mute = {speaker}
icons.volume_off = {speaker, line({16,8,23,16}), line({23,8,16,16})}
icons.check = {line({3,12,9,18,21,5})}
icons.history = {
    line({5,7,8,4,13,3,18,5,21,10,20,16,16,20,10,21,5,18}),
    polygon({2,1,2,7,9,7}),
    line({12,7,12,13}),
    line({12,12,16,15}),
}
icons.replay = {
    line({5,7,8,4,13,3,18,5,21,10,20,16,16,20,10,21,5,18}),
    polygon({2,1,2,7,9,7}),
}
icons.folder = {
    line({2,6,2,20,22,20,22,8,12,8,9,6,2,6}),
}
icons.folder_delete = {
    line({2,6,2,20,22,20,22,8,12,8,9,6,2,6}),
    line({10,11,16,17}),
    line({16,11,10,17}),
}
icons.playlist_remove = {
    line({2,5,15,5}),
    line({2,11,15,11}),
    line({2,17,10,17}),
    line({14,13,21,20}),
    line({21,13,14,20}),
}
icons.delete_sweep = {
    line({15,16,19,16}),
    line({15,12,21,12}),
    line({15,8,22,8}),
    line({3,8,4,20,12,20,13,8}),
    line({2,6,14,6}),
    line({6,6,6,4,10,4,10,6}),
}
icons.delete_forever = {
    line({5,6,6,21,18,21,19,6}),
    line({3,5,21,5}),
    line({8,5,8,2,16,2,16,5}),
    line({9,10,15,16}),
    line({15,10,9,16}),
}
icons.check_box_outline_blank = icons.crop_square
icons.check_box = {
    line({4,4,20,4,20,20,4,20,4,4}),
    line({7,12,10,15,17,8}),
}
icons.help_outline = icons.help
icons.help_center = icons.help
icons.chevron_right = icons.arrow_forward_ios
icons.not_started = icons.play_arrow
icons.play_circle_outline = icons.play_arrow
icons.hdr_auto = icons.high_quality
icons.repeat_one = icons['repeat']
icons.autorenew = icons.refresh

local arc = {}
for i = 0, 64 do
    local angle = i / 64 * math.pi * 1.5
    arc[#arc + 1] = 12 + 10 * math.cos(angle)
    arc[#arc + 1] = 12 + 10 * math.sin(angle)
end
for i = 64, 0, -1 do
    local angle = i / 64 * math.pi * 1.5
    arc[#arc + 1] = 12 + 8 * math.cos(angle)
    arc[#arc + 1] = 12 + 8 * math.sin(angle)
end
icons.spinner = {polygon(arc)}
for _, center in ipairs({{21, 12}, {12, 3}}) do
    local cap = {}
    for i = 0, 23 do
        local angle = i / 24 * math.pi * 2
        cap[#cap + 1] = center[1] + math.cos(angle)
        cap[#cap + 1] = center[2] + math.sin(angle)
    end
    icons.spinner[#icons.spinner + 1] = polygon(cap)
end

local cache = {}
local function path_for(name)
    if cache[name] then return cache[name] end
    local pieces = {}
    local function append(points)
        local path = {'m', tostring(points[1]), tostring(points[2]), 'l'}
        for i = 3, #points do path[#path + 1] = tostring(points[i]) end
        path[#path + 1], path[#path + 2] = tostring(points[1]), tostring(points[2])
        pieces[#pieces + 1] = table.concat(path, ' ')
    end
    for _, shape in ipairs(icons[name] or icons.help) do
        local p = shape.points
        if shape.stroke then
            for i = 1, #p - 2, 2 do
                local x1, y1, x2, y2 = p[i], p[i + 1], p[i + 2], p[i + 3]
                local dx, dy = x2 - x1, y2 - y1
                local length = math.sqrt(dx * dx + dy * dy)
                if length > 0 then
                    local nx, ny = -dy / length, dx / length
                    append({x1 + nx, y1 + ny, x2 + nx, y2 + ny, x2 - nx, y2 - ny, x1 - nx, y1 - ny})
                end
            end
        else
            append(p)
        end
    end
    cache[name] = table.concat(pieces, ' ')
    return cache[name]
end
return path_for
