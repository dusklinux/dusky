local player_version = '1.0.0'

mp.commandv('script-message', 'dusky_player-version', player_version)

mp.set_property('osc', 'no')

assdraw = require('mp.assdraw')
opt = require('mp.options')
utils = require('mp.utils')
msg = require('mp.msg')
osd = mp.create_osd_overlay('ass-events')
QUARTER_PI_SIN = math.sin(math.pi / 4)

require('lib/std')

--[[ OPTIONS ]]

local config_home = os.getenv('XDG_CONFIG_HOME')
if not config_home or config_home == '' then config_home = os.getenv('HOME') .. '/.config' end

defaults = {
	timeline_style = 'line',
	timeline_line_width = 2,
	timeline_size = 40,
	progress = 'windowed',
	progress_size = 2,
	progress_line_width = 20,
	timeline_persistency = '',
	timeline_border = 1,
	timeline_step = '5',
	timeline_cache = true,
	timeline_heatmap = 'overlay',
	timeline_waveform = false,
	timeline_mbtn_right = '',

	controls =
	'menu,gap,<video,audio>subtitles,<has_many_audio>audio,<has_many_video>video,<has_many_edition>editions,<stream>stream-quality,gap,space,<video,audio>speed,space,shuffle,loop-playlist,loop-file,gap,prev,items,next,gap,fullscreen',
	controls_size = 32,
	controls_margin = 8,
	controls_spacing = 2,
	controls_persistency = '',

	volume = 'right',
	volume_size = 40,
	volume_persistency = '',
	volume_border = 1,
	volume_step = 1,

	speed_persistency = '',
	speed_step = 0.1,
	speed_step_is_factor = false,

	menu_item_height = 36,
	menu_min_width = 260,
	menu_padding = 4,
	menu_type_to_search = true,

	top_bar = 'no-border',
	top_bar_size = 40,
	top_bar_persistency = '',
	top_bar_controls = 'right',
	top_bar_title = 'yes',
	top_bar_alt_title = '',
	top_bar_alt_title_place = 'below',
	top_bar_flash_on = 'video,audio',

	window_border_size = 1,

	autoload = true,
	shuffle = false,
	media_keys_mode = 'seek',
	media_keys_step = '30',
	dir_sort = 'natural',
	dir_sort_asc = true,
	auto_clear_eof_watch_later = true,

	scale = 1,
	scale_fullscreen = 1.3,
	font_scale = 1,
	text_border = 1.2,
	border_radius = 4,
	color = '',
	theme_path = config_home .. '/matugen/generated/mpv.json',
	opacity = '',
	animation_duration = 100,
	refine = '',
	flash_duration = 1000,
	proximity_in = 40,
	proximity_out = 120,
	total_time = false, -- deprecated by below
	destination_time = 'playtime-remaining',
	time_precision = 0,
	font_bold = false,
	autohide = false,
	buffered_time_threshold = 60,
	pause_indicator = 'flash',
	stream_quality_options = '4320,2160,1440,1080,720,480,360,240,144',
	video_types =
	'3g2,3gp,asf,avi,f4v,flv,h264,h265,m2ts,m4v,mkv,mov,mp4,mp4v,mpeg,mpg,ogm,ogv,rm,rmvb,ts,vob,webm,wmv,y4m',
	audio_types =
	'aac,ac3,aiff,ape,au,cue,dsf,dts,flac,m4a,mid,midi,mka,mp3,mp4a,oga,ogg,opus,spx,tak,tta,wav,weba,wma,wv',
	image_types = 'apng,avif,bmp,gif,j2k,jp2,jfif,jpeg,jpg,jxl,mj2,png,svg,tga,tif,tiff,webp',
	subtitle_types = 'aqt,ass,gsub,idx,jss,lrc,mks,pgs,pjs,psb,rt,sbv,slt,smi,sub,sup,srt,ssa,ssf,ttxt,txt,usf,vt,vtt',
	playlist_types = 'm3u,m3u8,pls,url,cue',
	load_types = 'video,audio,image',
	default_directory = '~/',
	show_hidden_files = false,
	use_trash = false,
	adjust_osd_margins = true,
	chapter_ranges = 'openings:30abf964,endings:30abf964,ads:c54e4e80',
	chapter_range_patterns = 'openings:オープニング;endings:エンディング',
	disable_elements = '',
}
options = table_copy(defaults)
function handle_options(changed_options)
	if changed_options.time_precision then
		timestamp_zero_rep_clear_cache()
	end
	update_config()
	update_human_times()
	Manager:disable('user', options.disable_elements)
	Elements:trigger('options')
	Elements:update_proximities()
	request_render()
end
opt.read_options(options, 'dusky_player', handle_options)
-- Normalize values
options.proximity_out = math.max(options.proximity_out, options.proximity_in + 1)
if options.chapter_ranges:sub(1, 4) == '^op|' then options.chapter_ranges = defaults.chapter_ranges end
if options.total_time and options.destination_time == 'playtime-remaining' then
	msg.warn('`total_time` is deprecated. Use `destination_time` instead.')
	options.destination_time = 'total'
elseif not itable_index_of({'total', 'playtime-remaining', 'time-remaining'}, options.destination_time) then
	options.destination_time = 'playtime-remaining'
end
if not itable_index_of({'left', 'right'}, options.top_bar_controls) then
	options.top_bar_controls = options.top_bar_controls == 'yes' and 'right' or nil
end

--[[ INTERNATIONALIZATION ]]
function t(text, value)
	return text and string.format(text, value or '') or ''
end
fzy = require('lib/fzy')

--[[ CONFIG ]]
local theme = require('lib/theme').new(options.theme_path)
local config_defaults = {
	color = {
		foreground = serialize_rgba('ffffff').color,
		foreground_text = serialize_rgba('000000').color,
		background = serialize_rgba('000000').color,
		background_text = serialize_rgba('ffffff').color,
		window_border = serialize_rgba('000000').color,
		curtain = serialize_rgba('111111').color,
		success = serialize_rgba('a5e075').color,
		error = serialize_rgba('ff616e').color,
		error_text = serialize_rgba('ffffff').color,
		match = serialize_rgba('69c5ff').color,
		heatmap = serialize_rgba('00adee').color,
		chapter_openings = serialize_rgba('30abf9').color,
		chapter_endings = serialize_rgba('30abf9').color,
		chapter_ads = serialize_rgba('c54e4e').color,
	},
	opacity = {
		timeline = 0.9,
		position = 1,
		chapters = 0.8,
		slider = 0.9,
		slider_gauge = 1,
		controls = 0,
		speed = 0.6,
		menu = 1,
		submenu = 0.4,
		border = 1,
		title = 1,
		tooltip = 1,
		thumbnail = 1,
		curtain = 0.8,
		idle_indicator = 0.8,
		audio_indicator = 0.5,
		buffering_indicator = 0.3,
		playlist_position = 0.8,
		heatmap = 0.4,
	},
}
config = {
	version = player_version,
	-- sets max rendering frequency in case the
	-- native rendering frequency could not be detected
	render_delay = 1 / 60,
	font = mp.get_property('options/osd-font'),
	osd_margin_x = mp.get_property('osd-margin-x'),
	osd_margin_y = mp.get_property('osd-margin-y'),
	osd_alignment_x = mp.get_property('osd-align-x'),
	osd_alignment_y = mp.get_property('osd-align-y'),
	refine = create_set(comma_split(options.refine)),
	types = {
		video = comma_split(options.video_types),
		audio = comma_split(options.audio_types),
		image = comma_split(options.image_types),
		subtitle = comma_split(options.subtitle_types),
		playlist = comma_split(options.playlist_types),
		media = comma_split(options.video_types
			.. ',' .. options.audio_types
			.. ',' .. options.image_types
			.. ',' .. options.playlist_types),
		load = {}, -- populated by update_load_types() below
	},
	stream_quality_options = comma_split(options.stream_quality_options),
	top_bar_flash_on = comma_split(options.top_bar_flash_on),
	chapter_ranges = (function()
		---@type table<string, string[]> Alternative patterns.
		local alt_patterns = {}
		if options.chapter_range_patterns and options.chapter_range_patterns ~= '' then
			for _, definition in ipairs(split(options.chapter_range_patterns, ';+ *')) do
				local name_patterns = split(definition, ' *:')
				local name, patterns = name_patterns[1], name_patterns[2]
				if name and patterns then alt_patterns[name] = split(patterns, ',') end
			end
		end

		---@type table<string, {color: string; opacity: number; patterns?: string[]}>
		local ranges = {}
		if options.chapter_ranges and options.chapter_ranges ~= '' then
			for _, definition in ipairs(split(options.chapter_ranges, ' *,+ *')) do
				local name_color = split(definition, ' *:+ *')
				local name, color = name_color[1], name_color[2]
				if name and color
					and name:match('^[a-zA-Z0-9_]+$') and color:match('^[a-fA-F0-9]+$')
					and (#color == 6 or #color == 8) then
					local range = serialize_rgba(name_color[2])
					if options.chapter_ranges == defaults.chapter_ranges then range.theme_color = 'chapter_' .. name end
					range.patterns = alt_patterns[name]
					ranges[name_color[1]] = range
				end
			end
		end
		return ranges
	end)(),
	color = table_copy(config_defaults.color),
	opacity = table_copy(config_defaults.opacity),
	cursor_leave_fadeout_elements = {'timeline', 'volume', 'top_bar', 'controls'},
	timeline_step = 5,
	timeline_step_flag = '',
	media_keys_mode = 'seek',
	media_keys_step = 30,
	media_keys_step_flag = '',
}

function update_load_types()
	local extensions = {}
	local types = create_set(comma_split(options.load_types:lower()))

	if types.same then
		types.same = nil
		if state and state.type then types[state.type] = true end
	elseif options.load_types == defaults.load_types and state and (state.is_video or state.is_audio) then
		-- Prevent static images (e.g. cover.jpg) from halting video/audio directory auto-play
		types.image = nil
	end

	for _, name in ipairs(table_keys(types)) do
		local type_extensions = config.types[name]
		if type(type_extensions) == 'table' then
			itable_append(extensions, type_extensions)
		else
			msg.warn('Unknown load type: ' .. name)
		end
	end

	config.types.load = extensions
end

-- Updates config with values dependent on options
function update_config()
	-- Required environment config
	if options.autoload then
		mp.commandv('set', 'keep-open', 'yes')
		mp.commandv('set', 'keep-open-pause', 'no')
	end

	-- Adds `{element}_persistency` config properties with forced visibility states (e.g.: `{paused = true}`)
	for _, name in ipairs({'timeline', 'controls', 'volume', 'top_bar', 'speed'}) do
		local option_name = name .. '_persistency'
		local value, flags = options[option_name], {}
		if type(value) == 'string' then
			for _, state in ipairs(comma_split(value)) do flags[state] = true end
		end
		config[option_name] = flags
	end

	-- Opacity
	config.opacity = table_assign({}, config_defaults.opacity, serialize_key_value_list(options.opacity,
		function(value, key)
			return clamp(0, tonumber(value) or config.opacity[key], 1)
		end
	))

	-- Color
	config.color = table_assign({}, config_defaults.color, theme.colors, serialize_key_value_list(options.color, function(value)
		return serialize_rgba(value).color
	end))

	-- Global color shorthands
	fg, bg = config.color.foreground, config.color.background
	fgt, bgt = config.color.foreground_text, config.color.background_text

	-- Timeline step
	do
		local is_exact = options.timeline_step:sub(-1) == '!'
		config.timeline_step = tonumber(is_exact and options.timeline_step:sub(1, -2) or options.timeline_step)
		config.timeline_step_flag = is_exact and 'exact' or ''
	end

	-- Media keys step & mode
	do
		local mode = options.media_keys_mode or 'seek'
		local step_str = tostring(options.media_keys_step or '30')
		if mode:find('^seek:') then
			step_str = mode:sub(6)
			mode = 'seek'
		end
		config.media_keys_mode = mode
		local is_exact = step_str:sub(-1) == '!'
		config.media_keys_step = tonumber(is_exact and step_str:sub(1, -2) or step_str) or 30
		config.media_keys_step_flag = is_exact and 'exact' or ''
	end

	-- Other
	update_load_types()
end
update_config()

-- Compact fallback menu, also used when input.conf has no menu entries.
function create_default_menu_items()
	return {
		{title = 'Open file', value = 'script-binding dusky_player/open-file'},
		{title = 'Playlist / Files', value = 'script-binding dusky_player/items'},
		{title = 'Chapters', value = 'script-binding dusky_player/chapters'},
		{title = 'Subtitles', value = 'script-binding dusky_player/subtitles'},
		{title = 'Audio tracks', value = 'script-binding dusky_player/audio'},
		{title = 'Audio device', value = 'script-binding dusky_player/audio-device'},
		{title = 'Screenshot', value = 'async screenshot'},
		{title = 'Quit', value = 'quit'},
	}
end

--[[ STATE ]]

display = {ax = 0, ay = 0, bx = 1280, by = 720, width = 1280, height = 720, initialized = false}
cursor = require('lib/cursor')
state = {
	cwd = mp.get_property('working-directory'),
	path = nil, -- current file path or URL
	history = {}, -- history of last played files stored as full paths
	time = nil, -- current media playback time
	speed = 1,
	---@type number|nil
	duration = nil, -- current media duration
	max_seconds = nil, -- max seconds the time in timeline is expected to reach, accounted for speed
	time_human = nil, -- current playback time in human format
	destination_time_human = nil, -- depends on options.destination_time
	pause = mp.get_property_native('pause'),
	ime_active = mp.get_property_native('input-ime'),
	chapters = {},
	chapter_ranges = {},
	border = mp.get_property_native('border'),
	title_bar = mp.get_property_native('title-bar'),
	fullscreen = mp.get_property_native('fullscreen'),
	maximized = mp.get_property_native('window-maximized'),
	fullormaxed = mp.get_property_native('fullscreen') or mp.get_property_native('window-maximized'),
	render_timer = nil,
	render_last_time = 0,
	volume = mp.get_property_native('volume'),
	volume_max = mp.get_property_native('volume-max'),
	mute = nil,
	type = nil, -- video,image,audio
	is_idle = false,
	is_video = false,
	is_audio = false, -- true if file is audio only (mp3, etc)
	is_image = false,
	is_stream = false,
	has_image = false,
	has_audio = false,
	has_sub = false,
	has_chapter = false,
	has_playlist = false,
	shuffle = options.shuffle,
	---@type nil|{pos: number; paths: string[]}
	shuffle_history = nil,
	on_shuffle = function() state.shuffle_history = nil end,
	mouse_bindings_enabled = false,
	uncached_ranges = nil,
	cache = nil,
	cache_buffering = 100,
	cache_underrun = false,
	cache_duration = nil,
	core_idle = false,
	eof_reached = false,
	render_delay = config.render_delay,
	playlist_count = 0,
	playlist_pos = 0,
	playlist_duration = nil,
	playlist_duration_human = nil,
	playlist_durations = {},
	refresh_playlist_menu = nil,
	refresh_open_file_menu = nil,
	dir_sort_by = options.dir_sort or 'natural',
	dir_sort_asc = options.dir_sort_asc ~= false,
	margin_top = 0,
	margin_bottom = 0,
	margin_left = 0,
	margin_right = 0,
	hidpi_scale = 1,
	scale = 1,
	radius = 0,
}
buttons = require('lib/buttons')
thumbnail = {width = 0, height = 0, disabled = false}
external = {} -- Properties set by external scripts
key_binding_overwrites = {} -- Table of key_binding:mpv_command
Elements = require('elements/Elements')
Menu = require('elements/Menu')

-- State dependent utilities
require('lib/utils')
require('lib/text')
require('lib/ass')
require('lib/menus')

--[[ STATE UPDATERS ]]

function update_display_dimensions()
	state.scale = (state.hidpi_scale or 1) * (state.fullormaxed and options.scale_fullscreen or options.scale)
	state.radius = round(options.border_radius * state.scale)
	local real_width, real_height = mp.get_osd_size()
	if real_width <= 0 then return end
	display.bx, display.width, display.by, display.height = real_width, real_width, real_height, real_height
	display.initialized = true

	-- Tell elements about this
	Elements:trigger('display')

	-- Some elements probably changed their rectangles as a reaction to `display`
	Elements:update_proximities()
	request_render()
end

function update_fullormaxed()
	state.fullormaxed = state.fullscreen or state.maximized
	update_display_dimensions()
	Elements:trigger('prop_fullormaxed', state.fullormaxed)
	cursor:leave()
end

-- Ensure MPV IPC socket is active so osd_router.sh / media_ipc.py can always reach dusky_player
do
	mp.set_property_native('user-data/dusky_player/enabled', true)
	if mp.get_property('input-ipc-server', '') == '' then
		local runtime_dir = os.getenv('XDG_RUNTIME_DIR') or '/tmp'
		local ipc_path = join_path(runtime_dir, 'mpv-ipc-' .. tostring(utils.getpid()))
		mp.set_property('input-ipc-server', ipc_path)
		mp.register_event('shutdown', function() os.remove(ipc_path) end)
	end
end

---@type table<string, {duration: number|false, title: string|false}|false>
local media_meta_cache = {}
local probe_queue = {}
local probe_queued_set = {}
local probe_in_flight = {}
local active_probes = 0
local MAX_CONCURRENT_PROBES = 4

---@param path string
---@return {duration: number|false, title: string|false}|false|nil
function get_media_meta(path)
	if not path then return nil end
	return media_meta_cache[normalize_path(path)]
end

local function process_probe_queue()
	while active_probes < MAX_CONCURRENT_PROBES and #probe_queue > 0 do
		local path = table.remove(probe_queue, 1)
		probe_queued_set[path] = nil
		if media_meta_cache[path] == nil and not probe_in_flight[path] then
			probe_in_flight[path] = true
			active_probes = active_probes + 1
			mp.command_native_async({
				name = 'subprocess',
				playback_only = false,
				capture_stdout = true,
				capture_stderr = false,
				args = {
					'ffprobe', '-v', 'error',
					'-show_entries', 'format=duration:format_tags=title',
					'-of', 'json',
					'--', path,
				},
			}, function(success, res)
				probe_in_flight[path] = nil
				active_probes = active_probes - 1
				local dur, title = false, false
				if success and res and res.status == 0 and res.stdout then
					local parsed = utils.parse_json(res.stdout)
					local fmt = type(parsed) == 'table' and parsed.format or nil
					if type(fmt) == 'table' then
						local d = tonumber(fmt.duration)
						if d and d > 0 then dur = d end
						local t_tag = type(fmt.tags) == 'table' and (fmt.tags.title or fmt.tags.TITLE) or nil
						if type(t_tag) == 'string' and #trim(t_tag) > 0 then title = trim(t_tag) end
					end
				end
				media_meta_cache[path] = {duration = dur, title = title}
				if state.has_playlist then update_playlist_duration(true) end
				if state.refresh_open_file_menu and Menu:is_open('open-file') then
					state.refresh_open_file_menu()
				end
				process_probe_queue()
			end)
		end
	end
end

---@param paths string[]
function request_media_probe(paths)
	for _, raw_path in ipairs(paths) do
		local key = normalize_path(raw_path)
		if key and media_meta_cache[key] == nil and not probe_in_flight[key] and not probe_queued_set[key] then
			if not is_protocol(raw_path) and not has_any_extension(raw_path, config.types.image) then
				probe_queued_set[key] = true
				probe_queue[#probe_queue + 1] = key
			else
				media_meta_cache[key] = false
			end
		end
	end
	process_probe_queue()
end

---@param refresh_menu? boolean
function update_playlist_duration(refresh_menu)
	if not state.has_playlist then
		state.playlist_durations = {}
		state.playlist_duration_human = nil
		set_state('playlist_duration', nil)
		return
	end

	local playlist = mp.get_property_native('playlist')
	if type(playlist) ~= 'table' or #playlist <= 1 then
		state.playlist_durations = {}
		state.playlist_duration_human = nil
		set_state('playlist_duration', nil)
		return
	end

	local total = 0
	local item_durations = {}
	local to_probe = {}

	for index, item in ipairs(playlist) do
		local raw_path = item.filename
		local key = raw_path and normalize_path(raw_path) or raw_path
		local cached = key and media_meta_cache[key] or nil
		local dur = nil

		if item.current and state.duration and state.duration > 0 then
			dur = state.duration
			if key then
				local prev_title = type(cached) == 'table' and cached.title or mp.get_property_native('media-title') or false
				media_meta_cache[key] = {duration = dur, title = prev_title}
			end
		elseif type(item.duration) == 'number' and item.duration > 0 then
			dur = item.duration
			if key and type(cached) ~= 'table' then
				media_meta_cache[key] = {duration = dur, title = item.title or false}
			end
		elseif type(cached) == 'table' and type(cached.duration) == 'number' then
			dur = cached.duration
		elseif key and cached == nil then
			to_probe[#to_probe + 1] = key
		end

		if dur and dur > 0 then
			total = total + dur
			item_durations[index] = dur
		end
	end

	if #to_probe > 0 then request_media_probe(to_probe) end

	state.playlist_durations = item_durations
	state.playlist_duration_human = total > 0 and format_time(total, total) or nil
	set_state('playlist_duration', total > 0 and total or nil)

	if refresh_menu and state.refresh_playlist_menu and Menu:is_open('playlist') then
		state.refresh_playlist_menu()
	end
	request_render()
end

function update_duration()
	local duration = state._duration and ((state.rebase_start_time == false and state.start_time)
		and (state._duration + state.start_time) or state._duration)
	if state.path and duration and duration > 0 then
		local prev = media_meta_cache[state.path]
		media_meta_cache[state.path] = {
			duration = duration,
			title = (type(prev) == 'table' and prev.title) or mp.get_property_native('media-title') or false,
		}
	end
	set_state('duration', duration)
	update_human_times()
	if state.has_playlist then update_playlist_duration(true) end
end

function update_human_times()
	state.speed = state.speed or 1
	if state.time then
		if state.duration then
			if options.destination_time == 'playtime-remaining' then
				state.destination_time_human = format_time((state.time - state.duration) / state.speed, state.duration)
			elseif options.destination_time == 'total' then
				state.destination_time_human = format_time(state.duration, state.duration)
			else
				state.destination_time_human = format_time(state.time - state.duration, state.duration)
			end
		else
			state.destination_time_human = nil
		end
		state.time_human = format_time(state.time, state.duration or state.time)
	else
		state.time_human, state.destination_time_human = nil, nil
	end
end

-- Notifies other scripts such as console about where the unoccupied parts of the screen are.
function update_margins()
	if display.height == 0 then return end

	local function causes_margin(element)
		return element and element.enabled and (element:is_persistent() or element.min_visibility > 0.5)
	end
	local timeline, top_bar, controls, volume = Elements.timeline, Elements.top_bar, Elements.controls, Elements.volume
	-- margins are normalized to window size
	local left, right, top, bottom = 0, 0, 0, 0

	if causes_margin(controls) then
		bottom = (display.height - controls.ay) / display.height
	elseif causes_margin(timeline) then
		bottom = (display.height - timeline.ay) / display.height
	end

	if causes_margin(top_bar) then top = top_bar.title_by / display.height end

	if causes_margin(volume) then
		if options.volume == 'left' then
			left = volume.bx / display.width
		elseif options.volume == 'right' then
			right = volume.ax / display.width
		end
	end

	if top == state.margin_top and bottom == state.margin_bottom and
		left == state.margin_left and right == state.margin_right then
		return
	end

	state.margin_top = top
	state.margin_bottom = bottom
	state.margin_left = left
	state.margin_right = right

	if utils.shared_script_property_set then
		utils.shared_script_property_set('osc-margins', string.format('%f,%f,%f,%f', 0, 0, top, bottom))
	end
	mp.set_property_native('user-data/osc/margins', {l = left, r = right, t = top, b = bottom})

	if not options.adjust_osd_margins then return end
	local osd_margin_y, osd_margin_x, osd_factor_x = 0, 0, display.width / display.height * 720
	if config.osd_alignment_y == 'bottom' then
		osd_margin_y = round(bottom * 720)
	elseif config.osd_alignment_y == 'top' then
		osd_margin_y = round(top * 720)
	end
	if config.osd_alignment_x == 'left' then
		osd_margin_x = round(left * osd_factor_x)
	elseif config.osd_alignment_x == 'right' then
		osd_margin_x = round(right * osd_factor_x)
	end
	mp.set_property_native('osd-margin-y', osd_margin_y + config.osd_margin_y)
	mp.set_property_native('osd-margin-x', osd_margin_x + config.osd_margin_x)
end
function create_state_setter(name, callback)
	return function(_, value)
		set_state(name, value)
		if callback then callback() end
		request_render()
	end
end

function set_state(name, value)
	state[name] = value
	local state_event = state['on_' .. name]
	if state_event then state_event(value) end
	Elements:trigger('prop_' .. name, value)
end

---@param path? string
function clear_file_watch_later(path)
	local target = path or state.path
	if target and not is_protocol(target) then
		mp.commandv('delete-watch-later-config', target)
		itable_delete_value(state.history, target)
	else
		mp.commandv('delete-watch-later-config')
	end
end

function handle_file_end()
	if options.auto_clear_eof_watch_later and state.path then
		clear_file_watch_later(state.path)
	end
	local resume = false
	if not state.loop_file then
		if state.has_playlist then
			resume = state.shuffle and navigate_playlist(1)
		else
			resume = options.autoload and navigate_directory(1)
		end
	end
	-- Resume when navigation happened; otherwise pause cleanly if sitting at EOF
	if resume then
		mp.command('set pause no')
	elseif state.eof_reached then
		mp.command('set pause yes')
	end
end
local file_end_timer = mp.add_timeout(1, handle_file_end)
file_end_timer:kill()

function load_file_index_in_current_directory(index)
	if not state.path or is_protocol(state.path) then return end

	local serialized = serialize_path(state.path)
	if serialized and serialized.dirname then
		local files, _dirs, error = read_directory(serialized.dirname, {
			types = config.types.load,
			hidden = options.show_hidden_files,
		})

		if error then
			msg.error(error)
			return
		end

		sort_directory_files(serialized.dirname, files, state.dir_sort_by, state.dir_sort_asc)
		if index < 0 then index = #files + index + 1 end

		if files[index] then
			mp.commandv('loadfile', join_path(serialized.dirname, files[index]))
		end
	end
end

function update_render_delay(name, fps)
	if fps then state.render_delay = 1 / fps end
end

function observe_display_fps(name, fps)
	if fps then
		mp.unobserve_property(update_render_delay)
		mp.unobserve_property(observe_display_fps)
		mp.observe_property('display-fps', 'native', update_render_delay)
	end
end

--[[ STATE HOOKS ]]

mp.register_event('file-loaded', function()
	local path = normalize_path(mp.get_property_native('path'))
	itable_delete_value(state.history, path)
	state.history[#state.history + 1] = path
	set_state('path', path)

	-- Anti-cascade guard: if mpv restored a watch_later position at the very end of the file,
	-- reset it to 00:00 and delete the stale watch-later entry so it plays from the start.
	if options.auto_clear_eof_watch_later and path and not is_protocol(path) then
		local pos = mp.get_property_number('time-pos')
		local dur = mp.get_property_number('duration')
		if (dur and dur > 10 and pos and pos >= dur - math.min(15, dur * 0.05))
			or mp.get_property_bool('eof-reached') then
			mp.commandv('delete-watch-later-config', path)
			mp.commandv('seek', 0, 'absolute+exact')
			mp.set_property_bool('pause', false)
		end
	end

	-- Flash top bar on requested file types
	for _, type in ipairs(config.top_bar_flash_on) do
		if state['is_' .. type] then
			Elements:flash({'top_bar'})
			break
		end
	end
end)

mp.add_hook('on_unload', 50, function()
	if options.auto_clear_eof_watch_later and state.path and not is_protocol(state.path) then
		if state.eof_reached or (state.duration and state.duration > 30 and state.time
				and (state.duration - state.time) <= math.min(20, state.duration * 0.05)) then
			mp.commandv('delete-watch-later-config', state.path)
		end
	end
end)
mp.register_event('end-file', function(event)
	if event.reason == 'eof' then
		file_end_timer:kill()
		handle_file_end()
	end
	set_state('path', nil)
end)
mp.observe_property('playback-time', 'number', create_state_setter('time', function()
	-- Pre-EOF timer is only needed to intercept native sequential playlist progression when shuffle is active
	file_end_timer:kill()
	if state.duration and state.time and not state.pause and (state.has_playlist and state.shuffle) then
		local remaining = (state.duration - state.time) / state.speed
		if remaining < 5 then
			local timeout = remaining - 0.02
			if timeout > 0 then
				file_end_timer.timeout = timeout
				file_end_timer:resume()
			else
				handle_file_end()
			end
		end
	end

	update_human_times()
end))
mp.observe_property('rebase-start-time', 'bool', create_state_setter('rebase_start_time', update_duration))
mp.observe_property('demuxer-start-time', 'number', create_state_setter('start_time', update_duration))
mp.observe_property('duration', 'number', create_state_setter('_duration', update_duration))
mp.observe_property('speed', 'number', create_state_setter('speed', update_human_times))
mp.observe_property('track-list', 'native', function(name, value)
	-- checks the file dispositions
	local types = {sub = 0, image = 0, audio = 0, video = 0}
	for _, track in ipairs(value) do
		if track.type == 'video' then
			if track.image or track.albumart then
				types.image = types.image + 1
			else
				types.video = types.video + 1
			end
		elseif types[track.type] then
			types[track.type] = types[track.type] + 1
		end
	end
	set_state('is_audio', types.video == 0 and types.audio > 0)
	set_state('is_image', types.image > 0 and types.video == 0 and types.audio == 0)
	set_state('has_image', types.image > 0)
	set_state('has_audio', types.audio > 0)
	set_state('has_many_audio', types.audio > 1)
	set_state('has_sub', types.sub > 0)
	set_state('has_many_sub', types.sub > 1)
	set_state('is_video', types.video > 0)
	set_state('has_many_video', types.video > 1)
	set_state('type', state.is_video and 'video' or state.is_audio and 'audio' or state.is_image and 'image' or nil)
	update_load_types()
	Elements:trigger('dispositions')
end)
mp.observe_property('editions', 'number', function(_, editions)
	if editions then set_state('has_many_edition', editions > 1) end
	Elements:trigger('dispositions')
end)
mp.observe_property('chapter-list', 'native', function(_, chapters)
	local chapters, chapter_ranges = serialize_chapters(chapters), {}
	if chapters then chapters, chapter_ranges = serialize_chapter_ranges(chapters) end
	set_state('chapters', chapters)
	set_state('chapter_ranges', chapter_ranges)
	set_state('has_chapter', #chapters > 0)
	Elements:trigger('dispositions')
end)
mp.observe_property('border', 'bool', create_state_setter('border'))
mp.observe_property('title-bar', 'bool', create_state_setter('title_bar'))
mp.observe_property('loop-file', 'native', create_state_setter('loop_file'))
mp.observe_property('ab-loop-a', 'number', create_state_setter('ab_loop_a'))
mp.observe_property('ab-loop-b', 'number', create_state_setter('ab_loop_b'))
mp.observe_property('playlist-pos-1', 'number', create_state_setter('playlist_pos'))
mp.observe_property('playlist-count', 'number', function(_, value)
	set_state('playlist_count', value)
	set_state('has_playlist', value > 1)
	update_playlist_duration(false)
	Elements:trigger('dispositions')
end)
mp.observe_property('playlist', 'native', function()
	update_playlist_duration(false)
end)
mp.observe_property('fullscreen', 'bool', create_state_setter('fullscreen', update_fullormaxed))
mp.observe_property('window-maximized', 'bool', create_state_setter('maximized', update_fullormaxed))
mp.observe_property('idle-active', 'bool', function(_, idle)
	set_state('is_idle', idle)
	Elements:trigger('dispositions')
	mp.commandv('script-message-to', 'dusky_thumbnails', 'clear')
end)
mp.observe_property('pause', 'bool', create_state_setter('pause', function() file_end_timer:kill() end))
mp.observe_property('volume', 'number', create_state_setter('volume'))
mp.observe_property('volume-max', 'number', create_state_setter('volume_max'))
mp.observe_property('mute', 'bool', create_state_setter('mute'))
mp.observe_property('osd-dimensions', 'native', function(name, val)
	update_display_dimensions()
	request_render()
end)
mp.observe_property('display-hidpi-scale', 'native', create_state_setter('hidpi_scale', update_display_dimensions))
mp.observe_property('cache', 'string', create_state_setter('cache'))
mp.observe_property('cache-buffering-state', 'number', create_state_setter('cache_buffering'))
mp.observe_property('demuxer-via-network', 'native', create_state_setter('is_stream', function()
	Elements:trigger('dispositions')
end))
mp.observe_property('demuxer-cache-state', 'native', function(prop, cache_state)
	local cached_ranges, bof, eof, uncached_ranges = nil, nil, nil, nil
	if cache_state then
		cached_ranges, bof, eof = cache_state['seekable-ranges'], cache_state['bof-cached'], cache_state['eof-cached']
		set_state('cache_underrun', cache_state['underrun'])
		set_state('cache_duration', not cache_state.eof and cache_state['cache-duration'] or nil)
	else
		cached_ranges = {}
		set_state('cache_underrun', false)
	end

	if not (state.duration and (#cached_ranges > 0 or state.cache == 'yes' or
			(state.cache == 'auto' and state.is_stream))) then
		if state.uncached_ranges then set_state('uncached_ranges', nil) end
		set_state('cache_duration', nil)
		return
	end

	-- Normalize
	local ranges = {}
	for _, range in ipairs(cached_ranges) do
		ranges[#ranges + 1] = {
			math.max(range['start'] or 0, 0),
			math.min(range['end'] or state.duration --[[@as number]], state.duration),
		}
	end
	table.sort(ranges, function(a, b) return a[1] < b[1] end)
	if bof then ranges[1][1] = 0 end
	if eof then ranges[#ranges][2] = state.duration end
	-- Invert cached ranges into uncached ranges, as that's what we're rendering
	local inverted_ranges = {{0, state.duration}}
	for _, cached in pairs(ranges) do
		inverted_ranges[#inverted_ranges][2] = cached[1]
		inverted_ranges[#inverted_ranges + 1] = {cached[2], state.duration}
	end
	uncached_ranges = {}
	local last_range = nil
	for _, range in ipairs(inverted_ranges) do
		if last_range and last_range[2] + 0.5 > range[1] then -- fuse ranges
			last_range[2] = range[2]
		else
			if range[2] - range[1] > 0.5 then -- skip short ranges
				uncached_ranges[#uncached_ranges + 1] = range
				last_range = range
			end
		end
	end

	set_state('uncached_ranges', uncached_ranges)
end)
mp.observe_property('display-fps', 'native', observe_display_fps)
mp.observe_property('estimated-display-fps', 'native', update_render_delay)
mp.observe_property('eof-reached', 'native', create_state_setter('eof_reached', function()
	if state.eof_reached then
		file_end_timer:kill()
		handle_file_end()
	end
end))
mp.observe_property('core-idle', 'native', create_state_setter('core_idle'))

--[[ KEY BINDS ]]

-- Adds a key binding that respects rerouting set by `key_binding_overwrites` table.
---@param name string
---@param callback fun(event: table)
---@param flags nil|string
function bind_command(name, callback, flags)
	mp.add_key_binding(nil, name, function(...)
		if key_binding_overwrites[name] then
			mp.command(key_binding_overwrites[name])
		else
			callback(...)
		end
	end, flags)
end

bind_command('toggle-ui', function() Elements:toggle({'timeline', 'controls', 'volume', 'top_bar'}) end)
bind_command('flash-ui', function() Elements:flash({'timeline', 'controls', 'volume', 'top_bar'}) end)
bind_command('flash-timeline', function() Elements:flash({'timeline'}) end)
bind_command('flash-top-bar', function() Elements:flash({'top_bar'}) end)
bind_command('flash-volume', function() Elements:flash({'volume'}) end)
bind_command('flash-speed', function() Elements:flash({'speed'}) end)
bind_command('flash-pause-indicator', function() Elements:flash({'pause_indicator'}) end)
bind_command('flash-progress', function() Elements:flash({'progress'}) end)
bind_command('toggle-progress', function() Elements:maybe('timeline', 'toggle_progress') end)
bind_command('toggle-waveform', function() Elements:maybe('timeline', 'toggle_waveform') end)
bind_command('toggle-title', function() Elements:maybe('top_bar', 'toggle_title') end)
bind_command('decide-pause-indicator', function() Elements:maybe('pause_indicator', 'decide') end)
bind_command('menu', function() toggle_menu_with_items() end)
bind_command('menu-blurred', function() toggle_menu_with_items({mouse_nav = true}) end)
bind_command('keybinds', function()
	if Menu:is_open('keybinds') then
		Menu:close()
	else
		open_command_menu({type = 'keybinds', items = get_keybinds_items(), search_style = 'palette'})
	end
end)
bind_command('load-subtitles', create_track_loader_menu_opener({
	prop = 'sub',
	title = t('Load subtitles'),
	loaded_message = t('Loaded subtitles'),
	allowed_types = itable_join(config.types.video, config.types.subtitle),
}))
bind_command('load-audio', create_track_loader_menu_opener({
	prop = 'audio',
	title = t('Load audio'),
	loaded_message = t('Loaded audio'),
	allowed_types = itable_join(config.types.video, config.types.audio),
}))
bind_command('load-video', create_track_loader_menu_opener({
	prop = 'video',
	title = t('Load video'),
	loaded_message = t('Loaded video'),
	allowed_types = config.types.video,
}))
bind_command('subtitles', create_select_tracklist_type_menu_opener({
	title = t('Subtitles'),
	type = 'sub',
	prop = 'sid',
	enable_prop = 'sub-visibility',
	secondary = {prop = 'secondary-sid', icon = 'vertical_align_top', enable_prop = 'secondary-sub-visibility'},
	load_command = 'script-binding dusky_player/load-subtitles',
}))
bind_command('audio', create_select_tracklist_type_menu_opener({
	title = t('Audio'), type = 'audio', prop = 'aid', load_command = 'script-binding dusky_player/load-audio',
}))
bind_command('video', create_select_tracklist_type_menu_opener({
	title = t('Video'), type = 'video', prop = 'vid', load_command = 'script-binding dusky_player/load-video',
}))
bind_command('playlist', create_self_updating_menu_opener({
	title = t('Playlist'),
	type = 'playlist',
	list_prop = 'playlist',
	footnote = t('Paste path or url to add.') .. ' ' .. t('%s to reorder.', 'ctrl+up/down/pgup/pgdn/home/end'),
	serializer = function(playlist)
		local items = {}
		local force_filename = mp.get_property_native('osd-playlist-entry') == 'filename'
		for index, item in ipairs(playlist) do
			local title = type(item.title) == 'string' and #item.title > 0 and item.title or false
			local item_dur = state.playlist_durations and state.playlist_durations[index]
				or (type(item.duration) == 'number' and item.duration > 0 and item.duration)
				or nil
			local dur_human = item_dur and format_time(item_dur, item_dur) or nil
			items[index] = {
				title = (not force_filename and title) and title
					or (is_protocol(item.filename) and item.filename or serialize_path(item.filename).basename),
				hint = dur_human and (index .. ' · ' .. dur_human) or tostring(index),
				active = item.current,
				value = index,
			}
		end
		local menu_title = state.playlist_duration_human
			and (t('Playlist') .. ' (' .. state.playlist_duration_human .. ')')
			or t('Playlist')
		return items, nil, menu_title
	end,
	on_activate = function(event) mp.commandv('set', 'playlist-pos-1', tostring(event.value)) end,
	on_paste = function(event) mp.commandv('loadfile', tostring(event.value), 'append') end,
	on_key = function(event)
		if event.id == 'ctrl+c' and event.selected_item then
			local payload = mp.get_property_native('playlist/' .. (event.selected_item.value - 1) .. '/filename')
			set_clipboard(payload)
		end
	end,
	on_move = function(event)
		local from, to = event.from_index, event.to_index
		mp.commandv('playlist-move', tostring(from - 1), tostring(to - (to > from and 0 or 1)))
	end,
	on_remove = function(event) mp.commandv('playlist-remove', tostring(event.value - 1)) end,
}))

function clear_directory_watch_later(dir_path)
	local target_dir = dir_path
	if not target_dir and state.path and not is_protocol(state.path) then
		local s = serialize_path(state.path)
		target_dir = s and s.dirname or nil
	end
	if not target_dir then
		mp.osd_message(t('No local directory active'))
		return
	end
	local files = read_directory(target_dir, {types = config.types.media, hidden = options.show_hidden_files})
	local count = 0
	for _, file in ipairs(files or {}) do
		local full_path = join_path(target_dir, file)
		mp.commandv('delete-watch-later-config', full_path)
		itable_delete_value(state.history, full_path)
		count = count + 1
	end
	state.shuffle_history = nil
	if state.path and not is_protocol(state.path) then
		mp.commandv('delete-watch-later-config', state.path)
		mp.commandv('seek', 0, 'absolute+exact')
	end
	mp.osd_message(t('Cleared playback memory for %s files in folder', count), 3)
end

function clear_all_watch_later()
	local wl_dir = mp.get_property_native('watch-later-directory')
	if not wl_dir or wl_dir == '' then
		wl_dir = mp.command_native({'expand-path', '~~state/watch_later'})
	else
		wl_dir = mp.command_native({'expand-path', wl_dir})
	end
	local count = 0
	if wl_dir and wl_dir ~= '' then
		local entries = utils.readdir(wl_dir, 'files')
		for _, entry in ipairs(entries or {}) do
			if os.remove(join_path(wl_dir, entry)) then count = count + 1 end
		end
	end
	if state.path then mp.commandv('delete-watch-later-config', state.path) end
	state.history = state.path and {state.path} or {}
	state.shuffle_history = nil
	mp.osd_message(t('Cleared all watch-later history (%s entries)', count), 3)
end

function open_clear_history_menu()
	if Menu:is_open('clear-history') then
		Menu:close()
		return
	end
	local menu
	local function build_items()
		local save_on_quit = mp.get_property_bool('save-position-on-quit', true)
		local items = {
			{
				title = t('Clear folder memory & restart from 00:00'),
				hint = t('current directory'),
				icon = 'folder_delete',
				value = 'clear_dir',
				bold = true,
			},
			{
				title = t('Clear current file & restart from 00:00'),
				hint = t('current track'),
				icon = 'replay',
				value = 'clear_current',
			},
		}
		if state.has_playlist then
			items[#items + 1] = {
				title = t('Clear playlist memory'),
				hint = tostring(state.playlist_count) .. ' ' .. t('items'),
				icon = 'playlist_remove',
				value = 'clear_playlist',
			}
		end
		items[#items + 1] = {
			title = t('Clear ALL saved watch-later history'),
			hint = t('entire player'),
			icon = 'delete_sweep',
			value = 'clear_all',
			separator = true,
		}
		items[#items + 1] = {
			title = t('Remember position on quit'),
			hint = save_on_quit and t('ON') or t('OFF'),
			icon = save_on_quit and 'check_box' or 'check_box_outline_blank',
			value = 'toggle_save_on_quit',
			active = save_on_quit,
			keep_open = true,
		}
		return items
	end

	menu = Menu:open({
		type = 'clear-history',
		title = t('Playback Memory & History'),
		items = build_items(),
		selected_index = 1,
	}, function(event)
		if event.type == 'activate' then
			if event.value == 'clear_dir' then
				clear_directory_watch_later()
				menu:close()
			elseif event.value == 'clear_current' then
				if state.path then
					clear_file_watch_later(state.path)
					mp.commandv('seek', 0, 'absolute+exact')
					mp.osd_message(t('Cleared saved position & restarted from 00:00'), 2)
				end
				menu:close()
			elseif event.value == 'clear_playlist' then
				local playlist = mp.get_property_native('playlist') or {}
				for _, item in ipairs(playlist) do
					if item.filename then clear_file_watch_later(normalize_path(item.filename)) end
				end
				if state.path then mp.commandv('seek', 0, 'absolute+exact') end
				mp.osd_message(t('Cleared saved positions for playlist (%s items)', #playlist), 3)
				menu:close()
			elseif event.value == 'clear_all' then
				clear_all_watch_later()
				menu:close()
			elseif event.value == 'toggle_save_on_quit' then
				local cur = mp.get_property_bool('save-position-on-quit', true)
				mp.set_property_bool('save-position-on-quit', not cur)
				if not not cur == false and state.path then
					mp.commandv('delete-watch-later-config', state.path)
				end
				menu:update_items(build_items())
			end
		end
	end)
end

bind_command('clear-history', open_clear_history_menu)
bind_command('clear-directory-history', function() clear_directory_watch_later() end)
bind_command('chapters', create_self_updating_menu_opener({
	title = t('Chapters'),
	type = 'chapters',
	list_prop = 'chapter-list',
	active_prop = 'chapter',
	serializer = function(chapters, current_chapter)
		local items = {}
		chapters = normalize_chapters(chapters)
		for index, chapter in ipairs(chapters) do
			items[index] = {
				title = chapter.title or '',
				hint = format_time(chapter.time, state.duration),
				value = index,
				active = index - 1 == current_chapter,
			}
		end
		return items
	end,
	on_activate = function(event) mp.commandv('set', 'chapter', tostring(event.value - 1)) end,
}))
bind_command('editions', create_self_updating_menu_opener({
	title = t('Editions'),
	type = 'editions',
	list_prop = 'edition-list',
	active_prop = 'current-edition',
	serializer = function(editions, current_id)
		local items = {}
		for _, edition in ipairs(editions or {}) do
			local edition_id_1 = tostring(edition.id + 1)
			items[#items + 1] = {
				title = edition.title or t('Edition %s', edition_id_1),
				hint = edition_id_1,
				value = edition.id,
				active = edition.id == current_id,
			}
		end
		return items
	end,
	on_activate = function(event) mp.commandv('set', 'edition', event.value) end,
}))
bind_command('show-in-directory', function()
	-- Ignore URLs
	if not state.path or is_protocol(state.path) then return end

	mp.command_native_async({
		name = 'subprocess', args = {'xdg-open', serialize_path(state.path).dirname},
		playback_only = false, detach = true,
	}, function() end)
end)
bind_command('stream-quality', open_stream_quality_menu)
bind_command('open-file', open_open_file_menu)
bind_command('shuffle', function()
	set_state('shuffle', not state.shuffle)
	mp.osd_message(state.shuffle and t('Shuffle ON') or t('Shuffle OFF'))
end)
bind_command('items', function()
	if state.has_playlist then
		mp.command('script-binding dusky_player/playlist')
	else
		mp.command('script-binding dusky_player/open-file')
	end
end)
bind_command('next', function() navigate_item(1) end)
bind_command('prev', function() navigate_item(-1) end)
bind_command('next-file', function() navigate_directory(1) end)
bind_command('prev-file', function() navigate_directory(-1) end)

local function handle_media_key(delta)
	if config.media_keys_mode == 'seek' and state.duration and state.duration > 0 then
		local step = config.media_keys_step * delta
		if config.media_keys_step_flag ~= '' then
			mp.commandv('seek', step, config.media_keys_step_flag)
		else
			mp.commandv('seek', step)
		end
		Elements:flash({'timeline'})
	else
		navigate_item(delta)
	end
end
bind_command('media-next', function() handle_media_key(1) end, 'repeatable')
bind_command('media-prev', function() handle_media_key(-1) end, 'repeatable')
bind_command('cycle-media-keys-mode', function()
	options.media_keys_mode = config.media_keys_mode == 'seek' and 'navigate' or 'seek'
	update_config()
	mp.osd_message(config.media_keys_mode == 'seek'
		and t('Media keys: Seek (%ss)', config.media_keys_step)
		or t('Media keys: Next/Prev item'))
end)
for _, key in ipairs({'NEXT', 'XF86_NEXT', 'FORWARD'}) do
	mp.add_forced_key_binding(key, 'media-key-' .. key:lower(), function()
		if key_binding_overwrites['media-next'] then
			mp.command(key_binding_overwrites['media-next'])
		else
			handle_media_key(1)
		end
	end, 'repeatable')
end
for _, key in ipairs({'PREV', 'XF86_PREV', 'REWIND'}) do
	mp.add_forced_key_binding(key, 'media-key-' .. key:lower(), function()
		if key_binding_overwrites['media-prev'] then
			mp.command(key_binding_overwrites['media-prev'])
		else
			handle_media_key(-1)
		end
	end, 'repeatable')
end
bind_command('first', function()
	if state.has_playlist then
		mp.commandv('set', 'playlist-pos-1', '1')
	else
		load_file_index_in_current_directory(1)
	end
end)
bind_command('last', function()
	if state.has_playlist then
		mp.commandv('set', 'playlist-pos-1', tostring(state.playlist_count))
	else
		load_file_index_in_current_directory(-1)
	end
end)
bind_command('first-file', function() load_file_index_in_current_directory(1) end)
bind_command('last-file', function() load_file_index_in_current_directory(-1) end)
bind_command('delete-file-prev', function() delete_file_navigate(-1) end)
bind_command('delete-file-next', function() delete_file_navigate(1) end)
bind_command('delete-file-quit', function()
	mp.command('stop')
	if state.path and not is_protocol(state.path) then delete_file(state.path) end
	mp.command('quit')
end)
bind_command('menu-prev', function() Elements:maybe('menu', 'navigate_by_items', -1) end)
bind_command('menu-next', function() Elements:maybe('menu', 'navigate_by_items', 1) end)
bind_command('menu-prev-page', function() Elements:maybe('menu', 'navigate_by_page', -1) end)
bind_command('menu-next-page', function() Elements:maybe('menu', 'navigate_by_page', 1) end)
bind_command('menu-start', function() Elements:maybe('menu', 'navigate_by_items', -math.huge) end)
bind_command('menu-end', function() Elements:maybe('menu', 'navigate_by_items', math.huge) end)
bind_command('menu-activate', function() Elements:maybe('menu', 'activate_selected_item') end)
bind_command('menu-back', function() Elements:maybe('menu', 'back') end)
bind_command('audio-device', create_self_updating_menu_opener({
	title = t('Audio devices'),
	type = 'audio-device-list',
	list_prop = 'audio-device-list',
	active_prop = 'audio-device',
	serializer = function(audio_device_list, current_device)
		current_device = current_device or 'auto'
		local ao = mp.get_property('current-ao') or ''
		local items = {}
		for _, device in ipairs(audio_device_list) do
			if device.name == 'auto' or string.match(device.name, '^' .. ao) then
				local hint = string.match(device.name, ao .. '/(.+)')
				if not hint then hint = device.name end
				items[#items + 1] = {
					title = device.description:sub(1, 7) == 'Default'
						and t('Default %s', device.description:sub(9))
						or device.description,
					hint = hint,
					active = device.name == current_device,
					value = device.name,
				}
			end
		end
		return items
	end,
	on_activate = function(event) mp.commandv('set', 'audio-device', event.value) end,
}))
bind_command('paste', function()
	local has_playlist = mp.get_property_native('playlist-count') > 1
	mp.commandv('script-binding', 'dusky_player/paste-to-' .. (has_playlist and 'playlist' or 'open'))
end)
bind_command('paste-to-open', function()
	local payload = get_clipboard()
	if payload then mp.commandv('loadfile', payload) end
end)
bind_command('paste-to-playlist', function()
	-- If there's no file loaded, we use `paste-to-open`, which both opens and adds to playlist
	if state.is_idle then
		mp.commandv('script-binding', 'dusky_player/paste-to-open')
	else
		local payload = get_clipboard()
		if payload then
			mp.commandv('loadfile', payload, 'append')
			mp.commandv('show-text', t('Added to playlist') .. ': ' .. payload, 3000)
		end
	end
end)
bind_command('copy-to-clipboard', function()
	if state.path then
		set_clipboard(state.path)
	else
		mp.commandv('show-text', t('Nothing to copy'), 3000)
	end
end)
bind_command('open-config-directory', function()
	local config_path = mp.command_native({'expand-path', '~~/mpv.conf'})
	local config = serialize_path(normalize_path(config_path))

	if config then
		mp.command_native_async({
			name = 'subprocess', args = {'xdg-open', config.dirname},
			playback_only = false, detach = true,
		}, function() end)
	else
		msg.error('Couldn\'t serialize config path "' .. config_path .. '".')
	end
end)

--[[ MESSAGE HANDLERS ]]

mp.register_script_message('show-submenu', function(id) toggle_menu_with_items({submenu = id}) end)
mp.register_script_message('show-submenu-blurred', function(id)
	toggle_menu_with_items({submenu = id, mouse_nav = true})
end)
mp.register_script_message('open-menu', function(json, submenu_id)
	local data = utils.parse_json(json)
	if type(data) ~= 'table' or type(data.items) ~= 'table' then
		msg.error('open-menu: received json didn\'t produce a table with menu configuration')
	else
		open_command_menu(data, {submenu = submenu_id, on_close = data.on_close})
	end
end)
mp.register_script_message('update-menu', function(json)
	local data = utils.parse_json(json)
	if type(data) ~= 'table' or type(data.items) ~= 'table' then
		msg.error('update-menu: received json didn\'t produce a table with menu configuration')
	else
		local menu = data.type and Menu:is_open(data.type)
		if menu then menu:update(data) end
	end
end)
mp.register_script_message('select-menu-item', function(type, item_index, menu_id)
	local menu = Menu:is_open(type)
	local index = tonumber(item_index)
	if menu and index and not menu.mouse_nav then
		index = round(index)
		if index > 0 and index <= #menu.current.items then
			menu:select_index(index, menu_id)
			menu:scroll_to_index(index, menu_id, true)
		end
	end
end)
mp.register_script_message('close-menu', function(type)
	if Menu:is_open(type) then Menu:close() end
end)
mp.register_script_message('menu-action', function(name, ...)
	local menu = Menu:is_open()
	if menu then
		local method = ({
			['search-cancel'] = 'search_cancel',
			['search-query-update'] = 'search_query_update',
		})[name]
		if method then menu[method](menu, ...) end
	end
end)
mp.register_script_message('dusky-thumbnails-info', function(json)
	local data = utils.parse_json(json)
	if type(data) ~= 'table' or not data.width or not data.height then
		thumbnail.disabled = true
		msg.error('dusky-thumbnails-info: received json didn\'t produce a table with thumbnail information')
	else
		thumbnail = data
		request_render()
	end
end)
mp.register_script_message('set', function(name, value)
	external[name] = value
	Elements:trigger('external_prop_' .. name, value)
end)
mp.register_script_message('toggle-elements', function(elements) Elements:toggle(comma_split(elements)) end)
mp.register_script_message('set-min-visibility', function(visibility, elements)
	local fraction = tonumber(visibility)
	local ids = comma_split(elements and elements ~= '' and elements or 'timeline,controls,volume,top_bar')
	if fraction then Elements:set_min_visibility(clamp(0, fraction, 1), ids) end
end)
mp.register_script_message('flash-elements', function(elements) Elements:flash(comma_split(elements)) end)
mp.register_script_message('overwrite-binding', function(name, command) key_binding_overwrites[name] = command end)
mp.register_script_message('disable-elements', function(id, elements) Manager:disable(id, elements) end)

--[[ ELEMENTS ]]

-- Dynamic elements
local constructors = {
	window_border = require('elements/WindowBorder'),
	buffering_indicator = require('elements/BufferingIndicator'),
	pause_indicator = require('elements/PauseIndicator'),
	top_bar = require('elements/TopBar'),
	timeline = require('elements/Timeline'),
	controls = options.controls and options.controls ~= 'never' and require('elements/Controls'),
	volume = itable_index_of({'left', 'right'}, options.volume) and require('elements/Volume'),
}

-- Required elements
require('elements/Curtain'):new()

-- Element manager
-- Handles creating and destroying elements based on disabled_elements user+script config.
Manager = {
	-- Managed disable-able element IDs
	_ids = itable_join(table_keys(constructors), {'idle_indicator', 'audio_indicator'}),
	---@type table<string, string[]> A map of clients and a list of element ids they disable
	_disabled_by = {},
	---@type table<string, boolean>
	disabled = {},
}

-- Set client and which elements it wishes disabled. To undo just pass an empty `element_ids` for the same `client`.
---@param client string
---@param element_ids string|string[]|nil `foo,bar` or `{'foo', 'bar'}`.
function Manager:disable(client, element_ids)
	self._disabled_by[client] = comma_split(element_ids)
	---@diagnostic disable-next-line: deprecated
	self.disabled = create_set(itable_join(unpack(table_values(self._disabled_by))))
	self:_commit()
end

function Manager:_commit()
	-- Create and destroy elements as needed
	for _, id in ipairs(self._ids) do
		local constructor = constructors[id]
		if not self.disabled[id] then
			if not Elements:has(id) and constructor then constructor:new() end
		else
			Elements:maybe(id, 'destroy')
		end
	end

	-- We use `on_display` event to tell elements to update their dimensions
	Elements:trigger('display')
end

-- Initial commit
Manager:disable('user', options.disable_elements)

theme:watch(function() handle_options({}) end)
