-- One small shared palette for both the custom UI and mpv's native OSD.
-- Compare contents rather than mtimes: quick same-size theme changes must work.
local mp = require('mp')
local utils = require('mp.utils')
local Theme = {}
Theme.__index = Theme

local keys = {
	'foreground', 'foreground_text', 'background', 'background_text',
	'window_border', 'curtain', 'success', 'error', 'error_text', 'match',
	'heatmap', 'chapter_openings', 'chapter_endings', 'chapter_ads',
}

function Theme.new(path)
	local self = setmetatable({path = path ~= '' and mp.command_native({'expand-path', path}) or '', colors = {}}, Theme)
	self:reload()
	return self
end

function Theme:reload()
	if self.path == '' then return end
	local file = io.open(self.path, 'r')
	if not file then return end
	local raw = file:read('*a')
	file:close()
	if not raw or raw == self.raw then return end
	local palette = utils.parse_json(raw)
	if type(palette) ~= 'table' then return end
	local colors = {}
	for _, key in ipairs(keys) do
		local hex = palette[key]
		-- Accept only a complete palette; retain the last good theme during writes.
		if type(hex) ~= 'string' or not hex:match('^#%x%x%x%x%x%x$') then return end
		colors[key] = hex:sub(6, 7) .. hex:sub(4, 5) .. hex:sub(2, 3)
	end
	self.raw, self.colors = raw, colors
	for prop, key in pairs({
		['background-color'] = 'curtain',
		['osd-color'] = 'background_text',
		['osd-outline-color'] = 'background',
		['osd-back-color'] = 'background',
		['osd-selected-color'] = 'foreground',
		['osd-selected-outline-color'] = 'background',
	}) do
		local current = mp.get_property(prop)
		mp.set_property(prop, current:sub(1, 3) .. palette[key]:sub(2))
	end
	-- Built-in menus and statistics have their own documented script options.
	-- Merge into the live map so unrelated scripts keep their settings.
	local script_opts = mp.get_property_native('options/script-opts') or {}
	for name, key in pairs({
		menu_outline_color = 'window_border', focused_color = 'foreground_text',
		focused_back_color = 'foreground', match_color = 'match',
		selected_color = 'foreground_text', selected_back_color = 'foreground',
	}) do
		script_opts['console-' .. name] = palette[key]
	end
	for name, key in pairs({
		font_color = 'background_text', border_color = 'background', shadow_color = 'background',
		plot_bg_border_color = 'window_border', plot_bg_color = 'background', plot_color = 'foreground',
	}) do
		script_opts['stats-' .. name] = colors[key]
	end
	mp.set_property_native('options/script-opts', script_opts)
	if self.on_change then self.on_change() end
end

function Theme:watch(on_change)
	self.on_change = on_change
	if self.path == '' then return end
	-- No subprocess, per-frame file access, or user IPC socket required.
	self.timer = mp.add_periodic_timer(2, function() self:reload() end)
	mp.register_event('shutdown', function() self.timer:kill() end)
end

return Theme
