-- ~/.config/mpv/scripts/hwdec_topology.lua
-- Dynamically resolves multi-GPU hardware decoding hierarchy, Aquamarine/Hyprland
-- AQ_DRM_DEVICES (by-path / cardN mapping), and secondary VA-API render nodes.

local utils = require('mp.utils')
local msg = require('mp.msg')

local function read_first_line(path)
	local f = io.open(path, 'r')
	if not f then return nil end
	local line = f:read('*l')
	f:close()
	return line
end

local function read_all(path)
	local f = io.open(path, 'r')
	if not f then return nil end
	local content = f:read('*a')
	f:close()
	return content
end

local function read_device_metadata(entry)
	local base = '/sys/class/drm/' .. entry .. '/device'
	local vendor = read_first_line(base .. '/vendor')
	local uevent = read_all(base .. '/uevent')
	if not vendor then
		vendor = read_first_line(base .. '/../vendor')
		uevent = read_all(base .. '/../uevent') or uevent
	end
	if vendor then vendor = vendor:lower() end
	local driver = uevent and uevent:match('DRIVER=([^\r\n]+)')
	local pci_bdf = uevent and uevent:match('PCI_SLOT_NAME=([^\r\n]+)')
	if pci_bdf then pci_bdf = pci_bdf:lower() end

	local card_node = nil
	local drm_siblings = utils.readdir(base .. '/drm', 'dirs') or {}
	table.sort(drm_siblings)
	for _, sib in ipairs(drm_siblings) do
		if sib:match('^card%d+$') then
			card_node = sib
			break
		end
	end

	return vendor, driver, pci_bdf, card_node
end

local function discover_drm_topology()
	local nodes = utils.readdir('/sys/class/drm', 'dirs') or {}
	table.sort(nodes)

	local igpu_render_node = nil
	local has_nvidia = false
	local by_node = {}
	local by_pci = {}

	for _, entry in ipairs(nodes) do
		if entry:match('^renderD%d+$') then
			local vendor, driver, pci_bdf, card_node = read_device_metadata(entry)
			local dev_path = '/dev/dri/' .. entry
			local drv_low = (driver or ''):lower()
			local is_nouveau_or_nova = drv_low == 'nouveau' or drv_low:find('^nova') ~= nil
			local is_nvidia_prop = vendor == '0x10de' and not is_nouveau_or_nova
			local is_vaapi = vendor == '0x8086' or vendor == '0x1002'
				or is_nouveau_or_nova or drv_low == 'virtio-pci' or drv_low == 'virtio_gpu'

			local gpu = {
				render_node = dev_path,
				card_node = card_node and ('/dev/dri/' .. card_node) or nil,
				vendor = vendor,
				driver = drv_low,
				pci_bdf = pci_bdf,
				is_nvidia_prop = is_nvidia_prop,
				is_vaapi = is_vaapi,
			}
			by_node[entry] = gpu
			by_node[dev_path] = gpu
			if card_node then
				by_node[card_node] = gpu
				by_node['/dev/dri/' .. card_node] = gpu
			end
			if pci_bdf then by_pci[pci_bdf] = gpu end

			if is_nvidia_prop then
				has_nvidia = true
			elseif vendor == '0x8086' or vendor == '0x1002' then
				-- Prefer Intel (0x8086) or AMD (0x1002) render node for VA-API fallback
				if not igpu_render_node or vendor == '0x8086' then
					igpu_render_node = dev_path
				end
			end
		end
	end

	return {
		igpu_node = igpu_render_node,
		has_nvidia = has_nvidia,
		by_node = by_node,
		by_pci = by_pci,
	}
end

if mp.get_property_bool('option-info/hwdec/set-from-commandline', false) then
	msg.info('Respecting command-line hwdec override: ' .. tostring(mp.get_property('hwdec')))
	return
end

local topology = discover_drm_topology()

local function resolve_drm_path(dev_path)
	if not dev_path or dev_path == '' then return nil end
	if topology.by_node[dev_path] then return topology.by_node[dev_path] end
	local leaf = dev_path:match('([^/]+)$')
	if leaf and topology.by_node[leaf] then return topology.by_node[leaf] end
	local bdf = dev_path:match('pci%-([0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+%.[0-7])')
	if bdf and topology.by_pci[bdf:lower()] then return topology.by_pci[bdf:lower()] end
	if leaf and (leaf:match('^card%d+$') or leaf:match('^renderD%d+$')) then
		local vendor, driver = read_device_metadata(leaf)
		if vendor then
			local drv_low = (driver or ''):lower()
			local is_nouveau_or_nova = drv_low == 'nouveau' or drv_low:find('^nova') ~= nil
			return {
				vendor = vendor,
				driver = drv_low,
				is_nvidia_prop = vendor == '0x10de' and not is_nouveau_or_nova,
				is_vaapi = vendor == '0x8086' or vendor == '0x1002' or is_nouveau_or_nova,
			}
		end
	end
	return nil
end

local function parse_aq_drm_devices()
	local drm_env = os.getenv('AQ_DRM_DEVICES') or os.getenv('WLR_DRM_DEVICES') or ''
	local ordered = {}
	for dev_path in drm_env:gmatch('[^:]+') do
		local gpu = resolve_drm_path(dev_path)
		if gpu then ordered[#ordered + 1] = gpu end
	end
	return ordered
end

local aq_gpus = parse_aq_drm_devices()
local primary_gpu = aq_gpus[1]

local function is_nvidia_primary_context()
	if topology.has_nvidia and not topology.igpu_node then
		return true
	end
	if primary_gpu and primary_gpu.vendor then
		return primary_gpu.is_nvidia_prop == true
	end
	local egl_vendor = os.getenv('__EGL_VENDOR_LIBRARY_FILENAMES') or ''
	return os.getenv('__GLX_VENDOR_LIBRARY_NAME') == 'nvidia'
		or os.getenv('__NV_PRIME_RENDER_OFFLOAD') == '1'
		or os.getenv('GBM_BACKEND') == 'nvidia-drm'
		or egl_vendor:find('nvidia', 1, true) ~= nil
end

local vaapi_cli_override = mp.get_property_bool('option-info/vaapi-device/set-from-commandline', false)

-- Apply topology once at startup before any file decoder initializes
if topology.has_nvidia and is_nvidia_primary_context() then
	local secondary_vaapi_node = nil
	for i = 2, #aq_gpus do
		if aq_gpus[i].is_vaapi and aq_gpus[i].render_node then
			secondary_vaapi_node = aq_gpus[i].render_node
			break
		end
	end
	secondary_vaapi_node = secondary_vaapi_node or topology.igpu_node
	if secondary_vaapi_node then
		-- NVIDIA primary + secondary Intel/AMD GPU present:
		-- Point vaapi-device explicitly at the discovered iGPU render node.
		if not vaapi_cli_override then
			mp.set_property('vaapi-device', secondary_vaapi_node)
		end
		mp.set_property('hwdec', 'nvdec,vaapi-copy')
		msg.info('NVIDIA primary context with secondary GPU: hwdec=nvdec,vaapi-copy (' .. secondary_vaapi_node .. ')')
	else
		-- Single-GPU NVIDIA system: use nvdec only, then fall back directly to CPU software decode.
		mp.set_property('hwdec', 'nvdec')
		msg.info('Single-GPU NVIDIA context: hwdec=nvdec (direct software fallback)')
	end
else
	-- Integrated GPU primary (hybrid D3cold mode) or pure Intel/AMD system:
	-- Try primary iGPU (vaapi) first; only touch secondary nvdec-copy if vaapi fails.
	local primary_vaapi_node = (primary_gpu and primary_gpu.is_vaapi and primary_gpu.render_node) or topology.igpu_node
	if primary_vaapi_node and not vaapi_cli_override then
		mp.set_property('vaapi-device', primary_vaapi_node)
	end
	if topology.has_nvidia then
		mp.set_property('hwdec', 'vaapi,nvdec-copy')
		msg.info('Mesa/VA-API primary context with secondary NVIDIA GPU: hwdec=vaapi,nvdec-copy'
			.. (primary_vaapi_node and (' (' .. primary_vaapi_node .. ', dGPU wakes only on iGPU codec miss)') or ''))
	else
		mp.set_property('hwdec', 'vaapi')
		msg.info('Mesa/VA-API primary context: hwdec=vaapi'
			.. (primary_vaapi_node and (' (' .. primary_vaapi_node .. ')') or ''))
	end
end
