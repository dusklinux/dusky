-- ~/.config/mpv/scripts/hwdec_topology.lua
-- Dynamically resolves multi-GPU hardware decoding hierarchy and secondary
-- VA-API render nodes via sysfs before decoder initialization.

local utils = require('mp.utils')
local msg = require('mp.msg')

local function read_first_line(path)
	local f = io.open(path, 'r')
	if not f then return nil end
	local line = f:read('*l')
	f:close()
	return line
end

local function discover_drm_topology()
	local nodes = utils.readdir('/sys/class/drm', 'dirs') or {}
	table.sort(nodes)

	local igpu_render_node = nil
	local has_nvidia = false

	for _, entry in ipairs(nodes) do
		if entry:match('^renderD%d+$') then
			local vendor = read_first_line('/sys/class/drm/' .. entry .. '/device/vendor')
			local dev_path = '/dev/dri/' .. entry
			if vendor == '0x8086' or vendor == '0x1002' then
				-- Prefer Intel (0x8086) or AMD (0x1002) render node for VA-API fallback
				if not igpu_render_node or vendor == '0x8086' then
					igpu_render_node = dev_path
				end
			elseif vendor == '0x10de' then
				has_nvidia = true
			end
		end
	end

	return {
		igpu_node = igpu_render_node,
		has_nvidia = has_nvidia,
	}
end

local topology = discover_drm_topology()

local function is_nvidia_primary_context()
	return os.getenv('__GLX_VENDOR_LIBRARY_NAME') == 'nvidia'
		or os.getenv('__NV_PRIME_RENDER_OFFLOAD') == '1'
		or os.getenv('GBM_BACKEND') == 'nvidia-drm'
end

-- Apply topology once at startup before any file decoder initializes
if is_nvidia_primary_context() and topology.has_nvidia then
	if topology.igpu_node then
		-- NVIDIA primary + secondary Intel/AMD GPU present:
		-- Point vaapi-device explicitly at the discovered iGPU render node.
		mp.set_property('vaapi-device', topology.igpu_node)
		mp.set_property('hwdec', 'nvdec,vaapi-copy')
		msg.info('NVIDIA primary context with secondary GPU: hwdec=nvdec,vaapi-copy (' .. topology.igpu_node .. ')')
	else
		-- Single-GPU NVIDIA system: use nvdec only, then fall back directly to CPU software decode.
		mp.set_property('hwdec', 'nvdec')
		msg.info('Single-GPU NVIDIA context: hwdec=nvdec (direct software fallback)')
	end
else
	-- Integrated GPU primary (hybrid D3cold mode) or pure Intel/AMD system:
	-- Keep hwdec=vaapi so cuInit(0) is never invoked and sleeping dGPUs stay in D3cold.
	mp.set_property('hwdec', 'vaapi')
	msg.info('Mesa/VA-API primary context: hwdec=vaapi (dGPU D3cold preserved)')
end
