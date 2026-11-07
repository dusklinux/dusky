-- Run with: lua user_scripts/mpv/tests/test_ui_and_topology.lua
local test_dir = arg[0]:match('^(.*[/])') or './'
local assets = test_dir .. '../setup/assets/scripts/'

dofile(assets .. 'dusky_player/lib/std.lua')
local props = {['loop-playlist'] = true}
mp = {
	get_property_native = function(k) return props[k] end,
	get_property = function() return '' end,
	get_time = function() return 0 end,
	add_timeout = function() return {kill = function() end, resume = function() end} end,
	observe_property = function() end,
	set_key_bindings = function() end,
	enable_key_bindings = function() end,
	disable_key_bindings = function() end,
}
utils = {split_path = function(p) return '/', p end}
msg = {warn = function() end, error = function() end, info = function() end}
state = {shuffle = false, render_delay = 1 / 60, render_last_time = 0}
options = {animation_duration = 100, time_precision = 0}
config = {render_delay = 1 / 60, cursor_leave_fadeout_elements = {}}
display = {width = 1920, height = 1080, initialized = false}
Elements = {update_proximities = function() end, trigger = function() end, proximity_trigger = function() end}
Menu = {is_open = function() return false end}

dofile(assets .. 'dusky_player/lib/utils.lua')
local list = {'/a.mkv', '/b.mkv', '/c.mkv'}
local idx, path = decide_navigation_in_list(list, 1, -1)
assert(idx == 3 and path == '/c.mkv', 'loop-playlist backward wrap failed')
idx, path = decide_navigation_in_list(list, 3, 1)
assert(idx == 1 and path == '/a.mkv', 'loop-playlist forward wrap failed')
print('PASS loop-playlist wrap-around math')

local enabled_groups = {}
mp.enable_key_bindings = function(name, flags) enabled_groups[name] = flags or 'forced' end
mp.disable_key_bindings = function(name) enabled_groups[name] = nil end
local cursor = dofile(assets .. 'dusky_player/lib/cursor.lua')
local dispose = cursor:once('primary_up', function() end)
assert(enabled_groups['mbtn_left'] ~= nil, 'global primary_up handler must keep mbtn_left enabled')
dispose()
assert(enabled_groups['mbtn_left'] == nil, 'unbinding primary_up must disable mbtn_left when no zones exist')

local clicked_id = nil
cursor.x, cursor.y = 50, 50
cursor:zone('secondary_click', {ax = 0, ay = 0, bx = 100, by = 100}, function(sc) clicked_id = sc.id end)
cursor:trigger('secondary_down', create_shortcut('secondary_down'))
cursor:trigger('secondary_up', create_shortcut('secondary_up'))
assert(clicked_id == 'secondary_click', 'secondary_click must pass secondary_click shortcut id')
print('PASS cursor global handler tracking and secondary_click shortcut dispatch')
