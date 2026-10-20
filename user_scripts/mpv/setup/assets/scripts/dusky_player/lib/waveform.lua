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
	for _, prop in ipairs({'track-list', 'audio-params', 'duration', 'demuxer-via-network'}) do
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
	local track
	local audio_count = 0
	local valid = not mp.get_property_native('demuxer-via-network', false)
	for _, item in ipairs(mp.get_property_native('track-list', {})) do
		if item.type == 'video' and not item.albumart and not item.image then valid = false end
		if item.type == 'audio' and item.selected then track = item end
		if item.type == 'audio' and not item.external then audio_count = audio_count + 1 end
	end
	-- ff-index is guaranteed only for lavf. A sole internal audio track is
	-- unambiguous with other demuxers too; exclude external audio and virtual inputs.
	local demuxer = mp.get_property('current-demuxer')
	local lavf = demuxer == 'lavf'
	valid = valid and track and not track.external and (lavf or (demuxer == 'mkv' and audio_count == 1))
	if source then source = normalize_path(source) end
	local info = valid and source and utils.file_info(source)
	local params = mp.get_property_native('audio-params', {})
	local rate = params.samplerate
	local index = lavf and track and track['ff-index'] or nil
	local mapping = index and index >= 0 and ('0:' .. index) or (not lavf and '0:a:0')
	if not (info and info.is_file and duration and duration > 0 and duration < math.huge
		and rate and rate > 0 and mapping) then
		self:reset(self.loaded)
		return
	end
	local samples = math.ceil(duration * rate / bins)
	if samples > 2147483647 then self:reset(self.loaded); return end
	local key = table.concat({source, info.size, info.mtime, mapping, rate, duration}, '\n')
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
		args = {'ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-threads', '1',
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
	local offset = state.rebase_start_time == false and (state.start_time or 0) or 0
	local geometry_key = table.concat({ax, ay, bx, by, state.duration, line, offset}, ',')
	if geometry_key ~= self.geometry_key then
		local pieces = {}
		local width, middle, height = bx - ax - line - 1, (ay + by) / 2, (by - ay - 2) / 2
		-- Merge bins narrower than a screen pixel, retaining their maximum peak.
		-- This bounds ASS work by the visible resolution as well as the bin count.
		local columns = math.max(1, math.min(bins, math.floor(width)))
		local peaks = {}
		for _, point in ipairs(data.points) do
			local first = math.max(1, math.floor((point.time + offset) / state.duration * columns) + 1)
			local last = math.min(columns, math.ceil((point.time + data.step + offset) / state.duration * columns))
			for column = first, last do peaks[column] = math.max(peaks[column] or 0, point.peak) end
		end
		for column = 1, columns do
			local left = ax + 0.5 + width * (column - 1) / columns
			local right = ax + 0.5 + width * column / columns
			local peak = (peaks[column] or 0) * height
			if peak > 0 then
				pieces[#pieces + 1] = string.format('m %.2f %.2f l %.2f %.2f %.2f %.2f %.2f %.2f',
					left, middle - peak, right, middle - peak, right, middle + peak, left, middle + peak)
			end
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
