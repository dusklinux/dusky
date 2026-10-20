-- Run with Lua or LuaJIT; tests the bundled module without mpv or audio hardware.
local test_dir = arg[0]:match('^(.*[/])') or './'
local production = test_dir .. '../setup/assets/scripts/dusky_player/lib/waveform.lua'
local properties, jobs, warnings, next_id = {}, {}, {}, 0
mp = {
 get_property = function(k) return properties[k] end,
 get_property_number = function(k) return properties[k] end,
 get_property_native = function(k, default) if properties[k] == nil then return default end; return properties[k] end,
 command_native_async = function(args, callback)
  next_id = next_id + 1
  jobs[next_id] = {args=args, callback=callback}
  return next_id
 end,
 abort_async_command = function(id) jobs[id].aborted = true end,
}
utils = {file_info = function(path) if path == '/fixture/audio.wav' then return {is_file=true,size=100,mtime=1} end end}
msg = {warn=function(s) warnings[#warnings+1]=s end, info=function() end}
state = {path='/fixture/audio.wav', duration=10, time=4}
options = {timeline_waveform=false}
config = {color={heatmap='ffffff'}}
request_render = function() end
normalize_path = function(s) return s end
local events, disposers = {}, {}
local owner = {
 register_disposer=function(_, fn) disposers[#disposers+1]=fn end,
 register_mp_event=function(_, name,fn) events[name]=fn end,
 observe_mp_property=function() end,
}
local Waveform = dofile(production)
local w = Waveform.new(owner)
local function baseline()
 properties = {
  path='/fixture/audio.wav', duration=10, ['audio-params']={samplerate=48000},
  ['current-demuxer']='lavf', ['track-list']={{type='audio', selected=true, ['ff-index']=0}},
 }
 w:reset(true)
 options.timeline_waveform=true
end
w:update(); assert(next_id==0)
for _, change in ipairs({
 function() properties['demuxer-via-network']=true end,
 function() properties.duration=nil end,
 function() properties.duration=math.huge end,
 function() properties.path='https://localhost/audio.wav' end,
 function() properties['track-list'][1].external=true end,
 function() properties['track-list'][2]={type='video'} end,
 function() properties['track-list'][1].selected=false end,
 function() properties['current-demuxer']='edl' end,
}) do
 baseline(); change(); local before=next_id; w:update(); assert(next_id==before and w.data==nil)
end
print('PASS off, network, unknown/infinite duration, URL, external audio, video, disabled audio and virtual inputs excluded')
baseline(); properties['track-list'][2]={type='video',albumart=true}; w:update()
local id=w.worker
for _=1,1000 do w:update() end
assert(w.worker==id and next_id==id)
local good='frame:0 pts_time:0\nlavfi.astats.Overall.Peak_level=-inf\nframe:1 pts_time:5\nlavfi.astats.Overall.Peak_level=-6.020600\n'
jobs[id].callback(true,{status=0,stdout=good})
assert(w.data.points[1].peak==0 and math.abs(w.data.points[2].peak-.5)<1e-6)
local data=w.data
options.timeline_waveform=false; w:update(); options.timeline_waveform=true; w:update()
assert(w.data==data and next_id==id)
print('PASS 1000 duplicate property updates spawn one worker; silence parse and completed cache reuse')
baseline(); w:update(); local stale=w.worker; events['start-file']()
baseline(); w:update(); local fresh=w.worker
jobs[stale].callback(true,{status=0,stdout=good}); assert(w.worker==fresh and not w.data)
jobs[fresh].callback(true,{status=0,stdout=good}); assert(w.data)
print('PASS stale completion after abort cannot replace current data')
for _, result in ipairs({{status=1,stderr='decode failed'}, {status=0,stdout=''},
 {status=0,stdout='frame:0 pts_time:0\nlavfi.astats.Overall.Peak_level=nan\n'},
 {status=0,stdout=string.rep('a',512*1024)}}) do
 baseline(); w:update(); local failed=w.worker; jobs[failed].callback(true,result)
 assert(not w.data and not w.worker)
 local n=next_id; w:update(); assert(next_id==n)
 options.timeline_waveform=false; w:update(); options.timeline_waveform=true; w:update(); assert(next_id==n+1)
end
baseline(); w:update(); local id=w.worker; jobs[id].callback(false,nil,'missing ffmpeg'); assert(not w.data)
print('PASS failed/missing worker, malformed/empty/truncated output; no retry loop, explicit toggle retries')
baseline(); w:update(); jobs[w.worker].callback(true,{status=0,stdout=good})
local function ass()
 return {text='',new_event=function() end,opacity=function() end,append=function(self,s) self.text=self.text..s end}
end
local a=ass(); w:render(a,0,900,915,940,1,1,400,402); assert(#a.text>0)
local path=w.path; local a=ass(); w:render(a,0,900,915,940,1,1,450,452); assert(w.path==path)
local a=ass(); w:render(a,0,900,915,940,0,1,450,452); assert(a.text=='')
w:reset(true); w:update(); jobs[w.worker].callback(true,{status=0,stdout=good})
local a=ass(); w:render(a,0,900,915,940,1,1,450,452); assert(#a.text>0)
for _, fn in ipairs(disposers) do fn() end
print('PASS geometry cache, hidden waveform fast path, and replacement at identical dimensions')
