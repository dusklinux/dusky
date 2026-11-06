--[[ UI specific utilities that might or might not depend on its state or options ]]

---@alias Point {x: number; y: number}
---@alias Rect {ax: number, ay: number, bx: number, by: number, window_drag?: boolean}
---@alias Circle {point: Point, r: number, window_drag?: boolean}
---@alias Hitbox Rect|Circle
---@alias ComplexBindingInfo {event: 'down' | 'repeat' | 'up' | 'press'; is_mouse: boolean; canceled: boolean; key_name?: string; key_text?: string;}

-- String & multi-criteria sorting
file_stat_cache = {}

---@param bytes number|nil
---@return string
function format_bytes(bytes)
	if not bytes or bytes <= 0 then return '0 B' end
	if bytes < 1024 then return string.format('%d B', bytes) end
	if bytes < 1048576 then return string.format('%.1f KB', bytes / 1024) end
	if bytes < 1073741824 then return string.format('%.1f MB', bytes / 1048576) end
	return string.format('%.2f GB', bytes / 1073741824)
end

---@param path string
---@return {size: number, mtime: number}
function get_file_stat(path)
	local cached = file_stat_cache[path]
	if cached then return cached end
	local info = utils.file_info(path)
	cached = {size = info and info.size or 0, mtime = info and info.mtime or 0}
	file_stat_cache[path] = cached
	return cached
end

do
	-- alphanum sorting for humans in Lua
	local function padnum(n)
		return ("%03d%s"):format(#n, n)
	end

	function natural_key(str)
		return tostring(str or ''):lower():gsub('0*(%d+)', padnum)
	end

	local function sort_lua(strings, asc)
		if asc == nil then asc = true end
		local tuples = {}
		for i, f in ipairs(strings) do
			tuples[i] = {natural_key(f), f}
		end
		table.sort(tuples, function(a, b)
			if a[1] == b[1] then
				if asc then
					return a[2] < b[2]
				else
					return a[2] > b[2]
				end
			end
			if asc then
				return a[1] < b[1]
			else
				return a[1] > b[1]
			end
		end)
		for i, tuple in ipairs(tuples) do strings[i] = tuple[2] end
		return strings
	end

	---@param strings string[]
	---@param asc? boolean
	function sort_strings(strings, asc)
		return sort_lua(strings, asc)
	end

	---@param dirname string
	---@param files string[]
	---@param sort_by? string 'natural'|'filename'|'title'|'mtime'|'size'|'duration'
	---@param asc? boolean
	function sort_directory_files(dirname, files, sort_by, asc)
		sort_by = sort_by or (state and state.dir_sort_by) or 'natural'
		if asc == nil then asc = (state and state.dir_sort_asc ~= nil) and state.dir_sort_asc or true end

		if sort_by == 'natural' then
			return sort_lua(files, asc)
		end

		local tuples = {}
		local unprobed = {}
		for i, file in ipairs(files) do
			local full_path = join_path(dirname, file)
			local nkey = natural_key(file)
			local key = nkey
			if sort_by == 'filename' then
				key = file:lower()
			elseif sort_by == 'mtime' then
				key = get_file_stat(full_path).mtime
			elseif sort_by == 'size' then
				key = get_file_stat(full_path).size
			elseif sort_by == 'duration' then
				local meta = get_media_meta and get_media_meta(full_path) or nil
				if meta == nil and not has_any_extension(file, config.types.image) then
					unprobed[#unprobed + 1] = full_path
				end
				key = (meta and type(meta.duration) == 'number') and meta.duration or -1
			elseif sort_by == 'title' then
				local meta = get_media_meta and get_media_meta(full_path) or nil
				if meta == nil and not has_any_extension(file, config.types.image) then
					unprobed[#unprobed + 1] = full_path
				end
				local title = meta and meta.title or file
				key = natural_key(title)
			end
			tuples[i] = {key = key, nkey = nkey, file = file}
		end

		if #unprobed > 0 and request_media_probe then
			request_media_probe(unprobed)
		end

		table.sort(tuples, function(a, b)
			if a.key ~= b.key then
				if asc then return a.key < b.key else return a.key > b.key end
			end
			if a.nkey ~= b.nkey then
				if asc then return a.nkey < b.nkey else return a.nkey > b.nkey end
			end
			if asc then
				return a.file < b.file
			else
				return a.file > b.file
			end
		end)

		for i, tuple in ipairs(tuples) do files[i] = tuple.file end
		return files
	end
end

-- Creates in-between frames to animate value from `from` to `to` numbers.
---@param from number
---@param to number|fun():number
---@param setter fun(value: number)
---@param duration_or_callback? number|fun() Duration in milliseconds or a callback function.
---@param callback? fun() Called either on animation end, or when animation is killed.
function tween(from, to, setter, duration_or_callback, callback)
	local duration = duration_or_callback
	if type(duration_or_callback) == 'function' then callback = duration_or_callback end
	if type(duration) ~= 'number' then duration = options.animation_duration end

	local current, done, timeout = from, false, nil
	local get_to = type(to) == 'function' and to or function() return to --[[@as number]] end
	local distance = math.abs(get_to() - current)
	local cutoff = distance * 0.01
	local target_ticks = (math.max(duration, 1) / (state.render_delay * 1000))
	local decay = 1 - ((cutoff / distance) ^ (1 / target_ticks))

	local function finish()
		if not done then
			setter(get_to())
			done = true
			timeout:kill()
			if callback then callback() end
			request_render()
		end
	end

	local function tick()
		local to = get_to()
		current = current + ((to - current) * decay)
		local is_end = math.abs(to - current) <= cutoff
		if is_end then
			finish()
		else
			setter(current)
			timeout:resume()
			request_render()
		end
	end

	timeout = mp.add_timeout(state.render_delay, tick)
	if cutoff > 0 then tick() else finish() end

	return finish
end

-- Returns signed distance (negative values mean how deep inside the rect the point is).
---@param point Point
---@param rect Rect
function get_point_to_rectangle_proximity(point, rect)
	local dx = math.max(rect.ax - point.x, point.x - rect.bx)
	local dy = math.max(rect.ay - point.y, point.y - rect.by)
	local distance = math.sqrt(math.max(0, dx)^2 + math.max(0, dy)^2)
	return distance + math.min(0, math.max(dx, dy))
end

---@param point_a Point
---@param point_b Point
function get_point_to_point_proximity(point_a, point_b)
	local dx, dy = point_a.x - point_b.x, point_a.y - point_b.y
	return math.sqrt(dx * dx + dy * dy)
end

---@param point Point
---@param hitbox Hitbox
function point_collides_with(point, hitbox)
	return (hitbox.r and get_point_to_point_proximity(point, hitbox.point) <= hitbox.r) or
		(not hitbox.r and get_point_to_rectangle_proximity(point, hitbox --[[@as Rect]]) <= 0)
end

---@param lax number
---@param lay number
---@param lbx number
---@param lby number
---@param max number
---@param may number
---@param mbx number
---@param mby number
function get_line_to_line_intersection(lax, lay, lbx, lby, max, may, mbx, mby)
	-- Calculate the direction of the lines
	local uA = ((mbx - max) * (lay - may) - (mby - may) * (lax - max)) /
		((mby - may) * (lbx - lax) - (mbx - max) * (lby - lay))
	local uB = ((lbx - lax) * (lay - may) - (lby - lay) * (lax - max)) /
		((mby - may) * (lbx - lax) - (mbx - max) * (lby - lay))

	-- If uA and uB are between 0-1, lines are colliding
	if uA >= 0 and uA <= 1 and uB >= 0 and uB <= 1 then
		return lax + (uA * (lbx - lax)), lay + (uA * (lby - lay))
	end

	return nil, nil
end

-- Returns distance from the start of a finite ray assumed to be at (rax, ray)
-- coordinates to a line.
---@param rax number
---@param ray number
---@param rbx number
---@param rby number
---@param lax number
---@param lay number
---@param lbx number
---@param lby number
function get_ray_to_line_distance(rax, ray, rbx, rby, lax, lay, lbx, lby)
	local x, y = get_line_to_line_intersection(rax, ray, rbx, rby, lax, lay, lbx, lby)
	if x then
		return math.sqrt((rax - x) ^ 2 + (ray - y) ^ 2)
	end
	return nil
end

-- Returns distance from the start of a finite ray assumed to be at (ax, ay)
-- coordinates to a rectangle. Returns `0` if ray originates inside rectangle.
---@param  ax number
---@param  ay number
---@param  bx number
---@param  by number
---@param  rect Rect
---@return number|nil
function get_ray_to_rectangle_distance(ax, ay, bx, by, rect)
	-- Is inside
	if ax >= rect.ax and ax <= rect.bx and ay >= rect.ay and ay <= rect.by then
		return 0
	end

	local closest = nil

	local function updateDistance(distance)
		if distance and (not closest or distance < closest) then closest = distance end
	end

	updateDistance(get_ray_to_line_distance(ax, ay, bx, by, rect.ax, rect.ay, rect.bx, rect.ay))
	updateDistance(get_ray_to_line_distance(ax, ay, bx, by, rect.bx, rect.ay, rect.bx, rect.by))
	updateDistance(get_ray_to_line_distance(ax, ay, bx, by, rect.ax, rect.by, rect.bx, rect.by))
	updateDistance(get_ray_to_line_distance(ax, ay, bx, by, rect.ax, rect.ay, rect.ax, rect.by))

	return closest
end

-- Converts a flat table of points to a smooth curve using Catmull-Rom to Bezier conversion.
---@param points number[] Flat table: x1, y1, x2, y2, ...
---@return number[] Flat table: start point followed by segment entries cp1x, cp1y, cp2x, cp2y, px, py, ...
function points_to_bezier(points)
	if not points or #points < 4 then return {} end
	local function catmullrom_to_bezier(p0x, p0y, p1x, p1y, p2x, p2y, p3x, p3y)
		local cp1x = p1x + (p2x - p0x) / 6
		local cp1y = p1y + (p2y - p0y) / 6
		local cp2x = p2x - (p3x - p1x) / 6
		local cp2y = p2y - (p3y - p1y) / 6
		return cp1x, cp1y, cp2x, cp2y
	end
	-- Helper to get x, y from flat table
	local function get_xy(i)
		return points[i * 2 - 1], points[i * 2]
	end
	local curve = {points[1], points[2]}
	local xy_pairs = #points / 2
	for i = 1, xy_pairs - 1 do
		local p0x, p0y = get_xy(math.max(i - 1, 1))
		local p1x, p1y = get_xy(i)
		local p2x, p2y = get_xy(i+1)
		local p3x, p3y = get_xy(math.min(i + 2, xy_pairs))
		local cp1x, cp1y, cp2x, cp2y = catmullrom_to_bezier(p0x, p0y, p1x, p1y, p2x, p2y, p3x, p3y)
		local n = #curve
		curve[n+1], curve[n+2], curve[n+3], curve[n+4], curve[n+5], curve[n+6] =
			cp1x, cp1y, cp2x, cp2y, p2x, p2y
	end
	return curve
end

-- Extracts the properties used by property expansion of that string.
---@param str string
---@param res { [string] : boolean } | nil
---@return { [string] : boolean }
function get_expansion_props(str, res)
	res = res or {}
	for str in str:gmatch('%$(%b{})') do
		local name, str = str:match('^{[?!]?=?([^:]+):?(.*)}$')
		if name then
			local s = name:find('==') or nil
			if s then name = name:sub(0, s - 1) end
			res[name] = true
			if str and str ~= '' then get_expansion_props(str, res) end
		end
	end
	return res
end

-- Escape a string for verbatim display on the OSD.
---@param str string
function ass_escape(str)
	-- There is no escape for '\' in ASS (I think?) but '\' is used verbatim if
	-- it isn't followed by a recognized character, so add a zero-width
	-- non-breaking space
	str = str:gsub('\\', '\\\239\187\191')
	str = str:gsub('{', '\\{')
	str = str:gsub('}', '\\}')
	-- Precede newlines with a ZWNBSP to prevent ASS's weird collapsing of
	-- consecutive newlines
	str = str:gsub('\n', '\239\187\191\\N')
	-- Turn leading spaces into hard spaces to prevent ASS from stripping them
	str = str:gsub('\\N ', '\\N\\h')
	str = str:gsub('^ ', '\\h')
	return str
end

---@param seconds number
---@param max_seconds number|nil Trims unnecessary `00:` if time is not expected to reach it.
---@return string
function format_time(seconds, max_seconds)
	local human = mp.format_time(seconds)
	if options.time_precision > 0 then
		local formatted = string.format('%.' .. options.time_precision .. 'f', math.abs(seconds) % 1)
		human = human .. '.' .. string.sub(formatted, 3)
	end
	if max_seconds then
		local trim_length = (max_seconds < 60 and 7 or (max_seconds < 3600 and 4 or 0))
		if trim_length > 0 then
			local has_minus = seconds < 0
			human = string.sub(human, trim_length + (has_minus and 1 or 0))
			if has_minus then human = '-' .. human end
		end
	end
	return human
end

---@param opacity number 0-1
function opacity_to_alpha(opacity)
	return 255 - math.ceil(255 * opacity)
end

-- POSIX paths: backslashes are ordinary filename characters.
function path_separator() return '/' end

---@param p1 string
---@param p2 string
---@return string
function join_path(p1, p2)
    p1 = trim_trailing_separator(p1)
    return p1 == '/' and p1 .. p2 or p1 .. '/' .. p2
end

---@param path string
---@return boolean
function is_absolute(path) return path:sub(1, 1) == '/' end

-- Ensure path is absolute.
---@param path string
---@return string
function ensure_absolute(path)
	if is_absolute(path) then return path end
	return join_path(state.cwd, path)
end

-- Keep the root slash while trimming directory suffixes.
---@param path string
---@return string, string
function trim_trailing_separator(path)
    path = trim_end(path, '/')
    return path == '' and '/' or path, '/'
end

-- Ensures path is absolute, remove trailing slashes/backslashes.
-- Lightweight version of normalize_path for performance critical parts.
---@param path string
---@return string
function normalize_path_lite(path)
	if not path or is_protocol(path) then return path end
	path = trim_trailing_separator(ensure_absolute(path))
	return path
end

-- Ensures path is absolute, remove trailing slashes/backslashes, normalization of path separators and deduplication.
---@param path string
---@return string
function normalize_path(path)
    if not path or is_protocol(path) then return path end
    path = trim_trailing_separator(ensure_absolute(path)):gsub('//+', '/')
    return path
end

---@param path string
function is_protocol(path)
	return type(path) == 'string' and (path:find('^%a[%w.+-]-://') ~= nil or path:find('^%a[%w.+-]-:%?') ~= nil)
end

---@param path string
---@param extensions string[] Lowercase extensions without the dot.
function has_any_extension(path, extensions)
	local path_last_dot_index = string_last_index_of(path, '.')
	if not path_last_dot_index then return false end
	local path_extension = path:sub(path_last_dot_index + 1):lower()
	for _, extension in ipairs(extensions) do
		if path_extension == extension then return true end
	end
	return false
end

-- Executes mp command defined as a string or an itable, or does nothing if command is any other value.
-- Returns boolean specifying if command was executed or not.
---@param command string | string[] | nil | any
---@return boolean executed `true` if command was executed.
function execute_command(command)
	local command_type = type(command)
	if command_type == 'string' then
		mp.command(command)
		return true
	elseif command_type == 'table' and #command > 0 then
		mp.command_native(command)
		return true
	end
	return false
end

-- Serializes path into its semantic parts.
---@param path string
---@return nil|{path: string; is_root: boolean; dirname?: string; basename: string; filename: string; extension?: string;}
function serialize_path(path)
	if not path or is_protocol(path) then return end

	local normal_path = normalize_path_lite(path)
	local dirname, basename = utils.split_path(normal_path)
	if basename == '' then basename, dirname = dirname:sub(1, #dirname - 1), nil end
	local dot_i = string_last_index_of(basename, '.')

	return {
		path = normal_path,
		is_root = dirname == nil,
		dirname = dirname,
		basename = basename,
		filename = dot_i and basename:sub(1, dot_i - 1) or basename,
		extension = dot_i and basename:sub(dot_i + 1) or nil,
	}
end

local system_files = create_set({
	'$RECYCLE.BIN', '$Recycle.Bin', '$SysReset', '$WinREAgent', '.sys', 'pagefile.sys', 'hiberfil.sys', 'config.sys',
	'swapfile.sys', 'Thumbs.db', 'desktop.ini',
})

-- Reads items in directory and splits it into directories and files tables.
---@param path string
---@param opts? {types?: string[], hidden?: boolean}
---@return string[] files
---@return string[] directories
---@return string|nil error
function read_directory(path, opts)
	opts = opts or {}
	local items, error = utils.readdir(path, 'all')
	local files, directories = {}, {}

	if not items then
		return files, directories, 'Reading directory "' .. path .. '" failed. Error: ' .. utils.to_string(error)
	end

	for _, item in ipairs(items) do
		if item ~= '.' and item ~= '..' and not system_files[item] and (opts.hidden or item:sub(1, 1) ~= '.') then
			local full_path = join_path(path, item)
			local info = utils.file_info(full_path)
			if info then
				if info.is_file then
					file_stat_cache[full_path] = {size = info.size or 0, mtime = info.mtime or 0}
					if not opts.types or has_any_extension(item, opts.types) then
						files[#files + 1] = item
					end
				else
					directories[#directories + 1] = item
				end
			end
		end
	end

	return files, directories
end

-- Returns full absolute paths of files in the same directory as `file_path`,
-- and index of the current file in the table.
-- Returned table will always contain `file_path`, regardless of `allowed_types`.
---@param file_path string
---@param opts? {types?: string[], hidden?: boolean}
function get_adjacent_files(file_path, opts)
	opts = opts or {}
	local current_meta = serialize_path(file_path)
	if not current_meta then return end
	local files, _dirs, error = read_directory(current_meta.dirname, {hidden = opts.hidden})
	if error then
		msg.error(error)
		return
	end
	sort_directory_files(current_meta.dirname, files, state.dir_sort_by, state.dir_sort_asc)
	local current_file_index
	local paths = {}
	for _, file in ipairs(files) do
		local is_current_file = current_meta.basename == file
		if is_current_file or not opts.types or has_any_extension(file, opts.types) then
			paths[#paths + 1] = join_path(current_meta.dirname, file)
			if is_current_file then current_file_index = #paths end
		end
	end
	if not current_file_index then return end
	return paths, current_file_index
end

-- Navigates in a list, using delta or, when `state.shuffle` is enabled,
-- randomness to determine the next item. Loops around if `loop-playlist` is enabled.
---@param paths table
---@param current_index number
---@param delta number 1 or -1 for forward or backward
function decide_navigation_in_list(paths, current_index, delta)
	if #paths < 2 then return end
	delta = delta < 0 and -1 or 1

	-- Shuffle looks at the played files history trimmed to 80% length of the paths
	-- and removes all paths in it from the potential shuffle pool. This guarantees
	-- no path repetition until at least 80% of the playlist has been exhausted.
	if state.shuffle then
		state.shuffle_history = state.shuffle_history or {
			pos = #state.history,
			paths = itable_slice(state.history),
		}
		state.shuffle_history.pos = state.shuffle_history.pos + delta
		local history_path = state.shuffle_history.paths[state.shuffle_history.pos]
		local next_index = history_path and itable_index_of(paths, history_path)
		if next_index then
			return next_index, history_path
		end
		if delta < 0 then
			state.shuffle_history.pos = state.shuffle_history.pos - delta
		else
			state.shuffle_history.pos = math.min(state.shuffle_history.pos, #state.shuffle_history.paths + 1)
		end

		local trimmed_history = itable_slice(state.history, -math.floor(#paths * 0.8))
		local shuffle_pool = {}

		for index, value in ipairs(paths) do
			if not itable_has(trimmed_history, value) then
				shuffle_pool[#shuffle_pool + 1] = index
			end
		end

		math.randomseed(os.time())
		local next_index = shuffle_pool[math.random(#shuffle_pool)]
		local next_path = paths[next_index]
		table.insert(state.shuffle_history.paths, state.shuffle_history.pos, next_path)
		return next_index, next_path
	end

	local new_index = current_index + delta
	if mp.get_property_native('loop-playlist') then
		if new_index > #paths then
			new_index = new_index % #paths
		elseif new_index < 1 then
			new_index = #paths - new_index
		end
	elseif new_index < 1 or new_index > #paths then
		return
	end

	return new_index, paths[new_index]
end

---@param delta number
function navigate_directory(delta)
	if not state.path or is_protocol(state.path) then return false end
	local paths, current_index = get_adjacent_files(state.path, {
		types = config.types.load,
		hidden = options.show_hidden_files,
	})
	if paths and current_index then
		local _, path = decide_navigation_in_list(paths, current_index, delta)
		if path then
			mp.commandv('loadfile', path)
			return true
		end
	end
	return false
end

---@param delta number
function navigate_playlist(delta)
	local playlist, pos = mp.get_property_native('playlist'), mp.get_property_native('playlist-pos-1')
	if playlist and #playlist > 1 and pos then
		local paths = itable_map(playlist, function(item) return normalize_path(item.filename) end)
		local index = decide_navigation_in_list(paths, pos, delta)
		if index then
			mp.commandv('playlist-play-index', index - 1)
			return true
		end
	end
	return false
end

---@param delta number
function navigate_item(delta)
	if mp.get_property_number('playlist-count', 0) > 1 then return navigate_playlist(delta) else return navigate_directory(delta) end
end

-- trash-cli is optional when use_trash is enabled.
---@param path string
function delete_file(path)
    local args = options.use_trash and {'trash', path} or {'rm', '--', path}
    return mp.command_native({
        name = 'subprocess', args = args, playback_only = false,
        capture_stdout = true, capture_stderr = true,
    })
end

function delete_file_navigate(delta)
	local path, playlist_pos = state.path, state.playlist_pos
	local is_local_file = path and not is_protocol(path)

	if navigate_item(delta) then
		if state.has_playlist then
			mp.commandv('playlist-remove', playlist_pos - 1)
		end
	else
		mp.command('stop')
	end

	if is_local_file then
		if Menu:is_open('open-file') then
			Elements:maybe('menu', 'delete_value', path)
		end
		if path then delete_file(path) end
	end
end

function serialize_chapter_ranges(normalized_chapters)
	local ranges = {}
	local simple_ranges = {
		{
			name = 'openings',
			patterns = {
				'^op ', '^op$', ' op$',
				'^opening$', ' opening$',
			},
			requires_next_chapter = true,
		},
		{
			name = 'intros',
			patterns = {
				'^intro$', ' intro$',
				'^avant$', '^prologue$',
			},
			requires_next_chapter = true,
		},
		{
			name = 'endings',
			patterns = {
				'^ed ', '^ed$', ' ed$',
				'^ending ', '^ending$', ' ending$',
			},
		},
		{
			name = 'outros',
			patterns = {
				'^outro$', ' outro$',
				'^closing$', '^closing ',
				'^preview$', '^pv$',
			},
		},
	}
	local sponsor_ranges = {}

	-- Extend with alt patterns
	for _, meta in ipairs(simple_ranges) do
		local alt_patterns = config.chapter_ranges[meta.name] and config.chapter_ranges[meta.name].patterns
		if alt_patterns then meta.patterns = itable_join(meta.patterns, alt_patterns) end
	end

	-- Clone chapters
	local chapters = {}
	for i, normalized in ipairs(normalized_chapters) do chapters[i] = table_assign({}, normalized) end

	for i, chapter in ipairs(chapters) do
		-- Simple ranges
		for _, meta in ipairs(simple_ranges) do
			if config.chapter_ranges[meta.name] then
				local match = itable_find(meta.patterns, function(p) return chapter.lowercase_title:find(p) end)
				if match then
					local next_chapter = chapters[i + 1]
					if next_chapter or not meta.requires_next_chapter then
						ranges[#ranges + 1] = table_assign({
							start = chapter.time,
							['end'] = next_chapter and next_chapter.time or math.huge,
						}, config.chapter_ranges[meta.name])
					end
				end
			end
		end

		-- Sponsor blocks
		if config.chapter_ranges.ads then
			local id = chapter.lowercase_title:match('segment start *%(([%w]%w-)%)')
			if id then -- ad range from sponsorblock
				for j = i + 1, #chapters, 1 do
					local end_chapter = chapters[j]
					local end_match = end_chapter.lowercase_title:match('segment end *%(' .. id .. '%)')
					if end_match then
						local range = table_assign({
							start_chapter = chapter,
							end_chapter = end_chapter,
							start = chapter.time,
							['end'] = end_chapter.time,
						}, config.chapter_ranges.ads)
						ranges[#ranges + 1], sponsor_ranges[#sponsor_ranges + 1] = range, range
						end_chapter.is_end_only = true
						break
					end
				end -- single chapter for ad
			elseif not chapter.is_end_only and
				(chapter.lowercase_title:find('%[sponsorblock%]:') or chapter.lowercase_title:find('^sponsors?')) then
				local next_chapter = chapters[i + 1]
				ranges[#ranges + 1] = table_assign({
					start = chapter.time,
					['end'] = next_chapter and next_chapter.time or math.huge,
				}, config.chapter_ranges.ads)
			end
		end
	end

	-- Fix overlapping sponsor block segments
	for index, range in ipairs(sponsor_ranges) do
		local next_range = sponsor_ranges[index + 1]
		if next_range then
			local delta = next_range.start - range['end']
			if delta < 0 then
				local mid_point = range['end'] + delta / 2
				range['end'], range.end_chapter.time = mid_point - 0.01, mid_point - 0.01
				next_range.start, next_range.start_chapter.time = mid_point, mid_point
			end
		end
	end
	table.sort(chapters, function(a, b) return a.time < b.time end)

	return chapters, ranges
end

-- Ensures chapters are in chronological order
function normalize_chapters(chapters)
	if not chapters then return {} end
	-- Ensure chronological order
	table.sort(chapters, function(a, b) return a.time < b.time end)
	-- Ensure titles
	for index, chapter in ipairs(chapters) do
		local chapter_number = chapter.title and string.match(chapter.title, '^Chapter (%d+)$')
		if chapter_number then
			chapter.title = t('Chapter %s', tonumber(chapter_number))
		end
		chapter.title = chapter.title ~= '(unnamed)' and chapter.title ~= '' and chapter.title or t('Chapter %s', index)
		chapter.lowercase_title = chapter.title:lower()
	end
	return chapters
end

function serialize_chapters(chapters)
	chapters = normalize_chapters(chapters)
	if not chapters then return end
	--- timeline font size isn't accessible here, so normalize to size 1 and then scale during rendering
	local opts = {size = 1, bold = true}
	for index, chapter in ipairs(chapters) do
		chapter.index = index
		chapter.title_wrapped, chapter.title_lines = wrap_text(chapter.title, opts, 25)
		chapter.title_wrapped_width = text_width(chapter.title_wrapped, opts)
		chapter.title_wrapped = ass_escape(chapter.title_wrapped)
	end
	return chapters
end

---Find all active key bindings or the active key binding for key
---@param key string|nil
---@return {[string]: table}|table
function find_active_keybindings(key)
	local bindings = mp.get_property_native('input-bindings', {})
	local active_map = {} -- map: key-name -> bind-info
	local active_table = {}
	for _, bind in pairs(bindings) do
		if bind.owner ~= 'dusky_player' and bind.priority >= 0 and (not key or bind.key == key) and (
				not active_map[bind.key]
				or (active_map[bind.key].is_weak and not bind.is_weak)
				or (bind.is_weak == active_map[bind.key].is_weak and bind.priority > active_map[bind.key].priority)
			)
		then
			active_table[#active_table + 1] = bind
			active_map[bind.key] = bind
		end
	end
	return key and active_map[key] or active_table
end

do
	local key_subs = {{'^#$', ''}, {anycase('sharp'), '#'}}

	-- Replaces stuff like `SHARP` -> `#`, `#` -> ``
	---@param keybind string
	function keybind_to_human(keybind)
		for _, sub in ipairs(key_subs) do
			keybind = string.gsub(keybind, sub[1], sub[2])
		end
		return keybind
	end
end

---@param type 'sub'|'audio'|'video'
---@param path string
function load_track(type, path)
	mp.commandv(type .. '-add', path, 'cached')
	-- If subtitle track was loaded, assume the user also wants to see it
	if type == 'sub' then
		mp.commandv('set', 'sub-visibility', 'yes')
	end
end

-- Native MPV clipboard support on Wayland; no external helper or fallback.
---@return string|nil
function get_clipboard()
	local payload, err = mp.get_property('clipboard/text')
	if not payload then msg.warn('Clipboard unavailable: ' .. tostring(err)) end
	return payload
end

---@param payload any
---@return string|nil
function set_clipboard(payload)
	payload = tostring(payload)
	local success, err = mp.set_property('clipboard/text', payload)
	if not success then
		msg.warn('Clipboard unavailable: ' .. tostring(err))
		return nil
	end
	mp.commandv('show-text', t('Copied to clipboard') .. ': ' .. payload, 3000)
	return payload
end

-- Returns Youtube heatmap data if available.
---@return number[]|nil Flat table of normalized points (0–1)
function load_youtube_heatmap()
	if not state.path or not is_protocol(state.path) then return end
	-- Match mpv's ytdl whitelist
	if not (state.path:match('^https?://%w+%.youtube%.com/') or
			state.path:match('^https?://youtube%.com/') or
			state.path:match('^https?://youtu%.be/')) then return end

	local r = mp.get_property_native('user-data/mpv/ytdl/json-subprocess-result')
	local ytdl_result = r and utils.parse_json(r.stdout)
	if ytdl_result and ytdl_result.heatmap then
		local data = ytdl_result.heatmap
		local max_val = 0
		local vid_length = data[#data].end_time
		for _, seg in ipairs(data) do
			max_val = math.max(max_val, seg.value)
		end
		-- Normalize and clamp to avoid gaps in heatmap
		local is_above = options.timeline_heatmap == 'above'
		local min_height, graph_height = 4, is_above and 40 or options.timeline_size
		local max_norm_y = 1 - (min_height / graph_height)
		local norm = {0, 1}
		for _, seg in ipairs(data) do
			local center_time = (seg.start_time + seg.end_time) / 2
			local norm_x = center_time / vid_length
			local norm_y = math.min(max_norm_y, 1 - (seg.value / max_val))
			norm[#norm + 1], norm[#norm + 2] = norm_x, norm_y
		end
		-- Add final anchor
		local last_y = math.min(max_norm_y, 1 - (data[#data].value / max_val))
		norm[#norm + 1], norm[#norm + 2] = 1, last_y
		norm[#norm + 1], norm[#norm + 2] = 1, 1
		return points_to_bezier(norm)
	end
end

--[[ RENDERING ]]

function render()
	if not display.initialized then return end
	state.render_last_time = mp.get_time()

	cursor:clear_zones()

	-- Actual rendering
	local ass = assdraw.ass_new()

	-- Idle indicator
	if state.is_idle and not Manager.disabled.idle_indicator then
		local smaller_side = math.min(display.width, display.height)
		local center_x, center_y, icon_size = display.width / 2, display.height / 2, math.max(smaller_side / 4, 56)
		ass:icon(center_x, center_y - icon_size / 4, icon_size, 'not_started', {
			color = fg, opacity = config.opacity.idle_indicator,
		})
		ass:txt(center_x, center_y + icon_size / 2, 8, t('Drop files or URLs to play here'), {
			size = icon_size / 4, color = fg, opacity = config.opacity.idle_indicator,
		})
	end

	-- Audio indicator
	if state.is_audio and not state.has_image and not Manager.disabled.audio_indicator
		and not (state.pause and options.pause_indicator == 'static') then
		local smaller_side = math.min(display.width, display.height)
		ass:icon(display.width / 2, display.height / 2, smaller_side / 4, 'graphic_eq', {
			color = fg, opacity = config.opacity.audio_indicator,
		})
	end

	-- Elements
	for _, element in Elements:ipairs() do
		if element.enabled then
			local result = element:maybe('render')
			if result then
				ass:new_event()
				ass:merge(result)
			end
		end
	end

	cursor:decide_keybinds()

	-- submit
	if osd.res_x == display.width and osd.res_y == display.height and osd.data == ass.text then
		return
	end

	osd.res_x = display.width
	osd.res_y = display.height
	osd.data = ass.text
	osd.z = 2000
	osd:update()

	update_margins()
end

-- Request that render() is called.
-- The render is then either executed immediately, or rate-limited if it was
-- called a small time ago.
state.render_timer = mp.add_timeout(0, render)
state.render_timer:kill()
function request_render()
	if state.render_timer:is_enabled() then return end
	local timeout = math.max(0, state.render_delay - (mp.get_time() - state.render_last_time))
	state.render_timer.timeout = timeout
	state.render_timer:resume()
end
