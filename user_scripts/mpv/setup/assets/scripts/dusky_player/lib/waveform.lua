-- Whole-track peak envelope, drawn by the timeline's existing ASS renderer.
-- FFmpeg streams one bin at a time; no PCM files or whole-track audio buffers.
local Waveform = {}
Waveform.__index = Waveform
local bins = 2048
local capture_limit = 512 * 1024

function Waveform.new(owner)
	local self = setmetatable({loaded = state.path ~= nil}, Waveform)
	owner:register_disposer(function() self:cancel() end)
	owner:register_mp_event('start-file', function() self:reset(false) end)
	owner:register_mp_event('end-file', function() self:reset(false) end)
	owner:register_mp_event('file-loaded', function()
		self.loaded = true
		self:update()
	end)
	for _, prop in ipairs({'track-list', 'audio-dec-params', 'audio-params', 'duration', 'demuxer-via-network'}) do
		owner:observe_mp_property(prop, 'native', function() self:update() end)
	end
	return self
end

function Waveform:cancel()
	-- Invalidate callbacks before aborting: an old worker must never publish data.
	self.job = nil
	if self.worker then mp.abort_async_command(self.worker) end
	self.worker = nil
end

function Waveform:reset(loaded)
	self:cancel()
	self.loaded, self.key, self.data, self.path, self.geometry_key = loaded, nil, nil, nil, nil
	request_render()
end

function Waveform:update()
	if not options.timeline_waveform or not self.loaded then
		self:cancel()
		-- Completed data survives toggling off/on for the current track.
		if not self.data then self.key = nil end
		request_render()
		return
	end
	local duration = mp.get_property_number('duration')
	local source = mp.get_property('path')
	local track, audio_index
	local audio_count = 0
	local valid = not mp.get_property_native('demuxer-via-network', false)
	for _, item in ipairs(mp.get_property_native('track-list', {})) do
		if item.type == 'video' and not item.albumart and not item.image then valid = false end
		if item.type == 'audio' then
			if not item.external then
				if item.selected then audio_index = audio_count end
				audio_count = audio_count + 1
			end
			if item.selected then track = item end
		end
	end
	-- Prefer lavf ff-index when present; otherwise map by internal audio ordinal (0:a:N).
	local demuxer = mp.get_property('current-demuxer')
	local lavf = demuxer == 'lavf'
	valid = valid and track and not track.external and (lavf or demuxer == 'mkv')
	if source then source = normalize_path(source) end
	local info = valid and source and utils.file_info(source)
	local params = mp.get_property_native('audio-dec-params') or mp.get_property_native('audio-params') or {}
	local rate = params.samplerate
	local index = lavf and track and track['ff-index'] or nil
	local mapping = (index and index >= 0 and ('0:' .. index))
		or (audio_index and ('0:a:' .. audio_index))
	if not (info and info.is_file and duration and duration > 0 and duration < math.huge
		and rate and rate > 0 and mapping) then
		self:reset(self.loaded)
		return
	end
	local samples = math.ceil(duration * rate / bins)
	if samples > 2147483647 then self:reset(self.loaded); return end
	local key = table.concat({source, info.size, info.mtime, mapping, rate}, '\n')
	if key == self.key then return end
	self:cancel()
	self.key, self.data, self.path, self.geometry_key = key, nil, nil, nil
	local job = {duration = duration, step = samples / rate}
	self.job = job
	local graph = string.format('asetnsamples=n=%d:p=0,' ..
		'astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,' ..
		'ametadata=mode=print:key=lavfi.astats.Overall.Peak_level:file=-', samples)
	self.worker = mp.command_native_async({
		name = 'subprocess', playback_only = true,
		capture_stdout = true, capture_stderr = true, capture_size = capture_limit,
		args = {'ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-v', 'error', '-threads', '1',
			'-i', source, '-map', mapping, '-vn', '-sn', '-dn',
			'-filter_threads', '1', '-af', graph, '-t', string.format('%.9f', duration),
			'-frames:a', tostring(bins + 2), '-f', 'null', '-'},
	}, function(success, result, error)
		if self.job ~= job then return end
		self.worker, self.job = nil, nil
		if not success or not result or result.status ~= 0 or #(result.stdout or '') >= capture_limit then
			msg.warn('Waveform analysis failed: ' .. tostring(error or (result and result.stderr)))
			return
		end
		local points, time = {}, nil
		for line in (result.stdout or ''):gmatch('[^\r\n]+') do
			local timestamp = line:match('pts_time:([^%s]+)')
			if timestamp then time = tonumber(timestamp) end
			local peak = line:match('^lavfi%.astats%.Overall%.Peak_level=(.+)$')
			if peak and time then
				local db = tonumber(peak)
				local amplitude = peak == '-inf' and 0 or (db and 10 ^ (db / 20))
				if not amplitude or amplitude ~= amplitude or amplitude == math.huge then
					msg.warn('Waveform analysis returned an invalid peak')
					return
				end
				points[#points + 1] = {time = time, peak = math.min(amplitude, 1)}
				time = nil
			end
		end
		if #points == 0 then msg.warn('Waveform analysis returned no peaks'); return end
		local base = points[1].time > job.step and points[1].time or 0
		if base > 0 then
			for i = 1, #points do
				points[i].time = points[i].time - base
			end
		end
		job.points = points
		self.data = job
		msg.info(string.format('Waveform ready: %d bins, %.1fs audio', #points, duration))
		request_render()
	end)
	request_render()
end

function Waveform:render(ass, ax, ay, bx, by, visibility, line, fax, fbx)
	if not options.timeline_waveform or not self.data or visibility <= 0 or by - ay < 4 then return end
	local data = self.data
	-- Cache geometry at each size; playback and cursor updates only reuse the string.
	-- Two clips reproduce t2x's progress-line gap without rebuilding on every tick.
	local geometry_key = table.concat({ax, ay, bx, by, line}, ',')
	if geometry_key ~= self.geometry_key then
		local pieces = {}
		local width, middle, height = bx - ax - line - 1, (ay + by) / 2, (by - ay - 2) / 2
		-- Merge bins narrower than a screen pixel, retaining their maximum peak.
		-- This bounds ASS work by the visible resolution as well as the bin count.
		local columns = math.max(1, math.min(bins, math.floor(width)))
		local peaks = {}
		local points = data.points
		for i = 1, #points do
			local point = points[i]
			local next_time = points[i + 1] and points[i + 1].time
			local stop_time = (next_time and next_time > point.time + data.step) and next_time or (point.time + data.step)
			local first = math.max(1, math.floor(point.time / data.duration * columns) + 1)
			local last = math.min(columns, math.ceil(stop_time / data.duration * columns))
			for column = first, last do peaks[column] = math.max(peaks[column] or 0, point.peak) end
		end
		local col = 1
		while col <= columns do
			local peak = math.floor((peaks[col] or 0) * height * 4 + 0.5) / 4
			local next_col = col + 1
			if peak > 0 then
				while next_col <= columns and math.floor((peaks[next_col] or 0) * height * 4 + 0.5) / 4 == peak do
					next_col = next_col + 1
				end
				local left = ax + 0.5 + width * (col - 1) / columns
				local right = ax + 0.5 + width * (next_col - 1) / columns
				pieces[#pieces + 1] = string.format('m %.2f %.2f l %.2f %.2f %.2f %.2f %.2f %.2f',
					left, middle - peak, right, middle - peak, right, middle + peak, left, middle + peak)
			end
			col = next_col
		end
		self.path, self.geometry_key = table.concat(pieces, ' '), geometry_key
	end
	local function draw(shift, left, right)
		if right <= left then return end
		ass:new_event()
		ass:append('{\\pos(' .. shift .. ',0)\\rDefault\\an7\\blur0\\bord0\\shad0\\1c&H' .. config.color.heatmap .. '}')
		ass:opacity(0.65 * visibility)
		ass:append('{\\clip(' .. left .. ',' .. ay .. ',' .. right .. ',' .. by .. ')}')
		-- This cached path is in pixels; assdraw's default p4 uses eighth-pixels.
		ass:append('{\\p1}' .. self.path .. '{\\p0}')
	end
	if fax then
		draw(0, ax, fax)
		draw(line, fbx, bx)
	else
		draw(0, ax, bx)
	end
end

return Waveform
