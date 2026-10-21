'use strict';
// Raw Objective-C runtime bridge: no dependency on Frida's optional ObjC bridge.
// Private calls fail on an unexpected method encoding. Never substitute guessed
// enums, ivar offsets, C strings for NSString, or Boolean JS values for BOOL args.
const nativeCache=new Map();
const native = (name,result,args) => {
    const key=JSON.stringify([name,result,args]);
    if(!nativeCache.has(key))nativeCache.set(key,new NativeFunction(Module.getGlobalExportByName(name),result,args));
    return nativeCache.get(key);
};
const utf8 = text => Memory.allocUtf8String(text);
const selectorCache=new Map();
const selector = name => {
    if(!selectorCache.has(name))selectorCache.set(name,native('sel_registerName','pointer',['pointer'])(utf8(name)));
    return selectorCache.get(name);
};
const classCache=new Map();
const classNamed = name => {
    if(classCache.has(name))return classCache.get(name);
    const value=native('objc_getClass','pointer',['pointer'])(utf8(name));
    if(value.isNull())throw Error('Class is unavailable in this process: '+name);
    classCache.set(name,value);return value;
};
const send0 = (object, name) => native('objc_msgSend','pointer',['pointer','pointer'])(object,selector(name));
const ns = text => native('objc_msgSend','pointer',['pointer','pointer','pointer'])(classNamed('NSString'),selector('stringWithUTF8String:'),utf8(text));
const text = object => {
    if(object.isNull()||!isKind(object,'NSString'))return null;
    return send0(object,'UTF8String').readUtf8String();
};
const classOf = object => native('object_getClass','pointer',['pointer'])(object);
const isKind = (object,name) => !object.isNull() && Boolean(native('objc_msgSend','bool',['pointer','pointer','pointer'])(object,selector('isKindOfClass:'),classNamed(name)));
const className = object => native('class_getName','pointer',['pointer'])(classOf(object)).readUtf8String();
const getMethod = (object,name) => native('class_getInstanceMethod','pointer',['pointer','pointer'])(classOf(object),selector(name));
const encoding = method => native('method_getTypeEncoding','pointer',['pointer'])(method).readUtf8String();
function checked(object,name,expected,result='pointer',types=[],args=[]) {
    if(object.isNull()) throw Error('Nil receiver: '+name);
    const method=getMethod(object,name);
    if(method.isNull()) throw Error(className(object)+' has no '+name);
    const actual=encoding(method);
    if(actual!==expected) throw Error('ABI mismatch '+className(object)+' '+name+': expected '+expected+', got '+actual);
    return native('objc_msgSend',result,['pointer','pointer',...types])(object,selector(name),...args);
}
const responds = (object,name) => !object.isNull() && !getMethod(object,name).isNull();
const number = value => typeof value==='number' ? value : Number(value.toString());
const count = array => array.isNull()?0:number(native('objc_msgSend','uint64',['pointer','pointer'])(array,selector('count')));
const at = (array,index) => native('objc_msgSend','pointer',['pointer','pointer','uint64'])(array,selector('objectAtIndex:'),index);
const shared = name => checked(classNamed(name),'sharedInstance','@16@0:8');
const app = () => checked(classNamed('UIApplication'),'sharedApplication','@16@0:8');
const windows = () => send0(app(),'windows');
const integer = (object,name) => number(checked(object,name,'q16@0:8','int64'));
const boolean = (object,name) => Boolean(checked(object,name,'B16@0:8','bool'));

// Keep callbacks and the script alive until the scheduled main-queue job runs.
// Host cancellation does not undo an already queued action.
const queuedCallbacks = new Set();
function onMain(job) {
    return new Promise((resolve,reject)=>{
        Script.pin();
        const callback=new NativeCallback(()=>{
            const pool=native('objc_autoreleasePoolPush','pointer',[])();
            try { resolve(job()); } catch(error) { reject(error); }
            finally {
                native('objc_autoreleasePoolPop','void',['pointer'])(pool);
                queuedCallbacks.delete(callback);Script.unpin();
            }
        },'void',['pointer']);
        queuedCallbacks.add(callback);
        native('dispatch_async_f','void',['pointer','pointer','pointer'])(Module.getGlobalExportByName('_dispatch_main_q'),ptr(0),callback);
    });
}
function controllers() {
    const out=[],seen=new Set();
    function visit(value,parent,relation) {
        if(value.isNull()||seen.has(value.toString()))return;
        if(out.length>=600)throw Error('Controller traversal exceeded 600 nodes');
        const id=value.toString();seen.add(id);
        out.push({object:value,id,parent,relation,className:className(value)});
        const children=send0(value,'childViewControllers');
        for(let i=0;i<count(children);i++)visit(at(children,i),id,'child');
        visit(send0(value,'presentedViewController'),id,'presented');
    }
    const list=windows();
    for(let i=0;i<count(list);i++)visit(send0(at(list,i),'rootViewController'),null,'window-root');
    return out;
}
function camera() {
    const match=controllers().filter(item=>item.className==='CAMViewfinderViewController');
    if(match.length!==1)throw Error('Expected one Camera viewfinder, found '+match.length+'; launch native Camera and wait for it');
    return match[0].object;
}
function cameraStatus() {
    const controller=camera();
    return {mode:integer(controller,'currentCaptureMode'),recording:boolean(controller,'isRecording')};
}
function videoFormat() {
    const controller=camera();
    if(![1,2].includes(integer(controller,'currentCaptureMode')))throw Error('Select Video or Slo-mo before reading/changing its format');
    const indicator=checked(controller,'_videoConfigurationStatusIndicator','@16@0:8');
    if(indicator.isNull())throw Error('Camera video indicator is not ready');
    const label=name=>text(send0(checked(indicator,name,'@16@0:8'),'text'));
    return {configuration:integer(controller,'_currentVideoConfiguration'),
        framerateEnum:integer(indicator,'framerate'),resolutionEnum:integer(indicator,'resolution'),
        fpsLabel:label('_framerateLabel'),resolutionLabel:label('_resolutionLabel')};
}
function cameraCycle(which) {
    const controller=camera();
    if(boolean(controller,'isRecording'))throw Error('Stop recording before changing video format');
    const before=videoFormat();
    const indicator=checked(controller,'_videoConfigurationStatusIndicator','@16@0:8');
    const name=which==='fps'?'videoConfigurationStatusIndicatorDidTapFramerate:':'videoConfigurationStatusIndicatorDidTapResolution:';
    checked(controller,name,'v24@0:8@16','void',['pointer'],[indicator]);
    return {before,requested:which+' cycle',note:'Read camera format again after the UI settles'};
}
function views(limit) {
    if(!Number.isInteger(limit)||limit<1||limit>5000)throw Error('max-nodes must be 1..5000');
    const nodes=[],seen=new Set();let truncated=false;
    function walk(view,parent) {
        if(view.isNull()||seen.has(view.toString()))return;
        if(nodes.length>=limit){truncated=true;return;}
        const id=view.toString();seen.add(id);
        const value=name=>{
            const method=getMethod(view,name);
            return !method.isNull()&&encoding(method)==='@16@0:8'?text(send0(view,name)):null;
        };
        let title=null;
        if(responds(view,'currentTitle'))title=value('currentTitle');
        const row={object:view,id,parent,className:className(view),identifier:value('accessibilityIdentifier'),
            label:value('accessibilityLabel'),text:responds(view,'text')?value('text'):null,title};
        nodes.push(row);
        const children=send0(view,'subviews');for(let i=0;i<count(children);i++)walk(at(children,i),id);
    }
    const list=windows();for(let i=0;i<count(list);i++)walk(at(list,i),null);
    return {nodes,truncated};
}
function methodList(name,classMethods,matching) {
    let type=classNamed(name);if(classMethods)type=classOf(type);
    const size=Memory.alloc(4);const list=native('class_copyMethodList','pointer',['pointer','pointer'])(type,size),out=[];
    try {
        for(let i=0;i<size.readU32();i++) {
            const method=list.add(i*Process.pointerSize).readPointer();
            const sel=native('method_getName','pointer',['pointer'])(method);
            const name=native('sel_getName','pointer',['pointer'])(sel).readUtf8String();
            if(name.toLowerCase().includes(matching.toLowerCase()))out.push({name,encoding:encoding(method)});
        }
    } finally {native('free','void',['pointer'])(list);}
    return {classMethods,declaredOnly:true,methods:out};
}
function status() {
    return {pid:Process.id,process:Process.mainModule.name,frida:Frida.version,
        displayOn:boolean(shared('SBBacklightController'),'screenIsOn'),
        uiLocked:boolean(shared('SBLockScreenManager'),'isUILocked'),
        duskieLoaded:Process.findModuleByName('Duskie.dylib')!==null,
        keyboardPayloadLoaded:Process.findModuleByName('DuskieKeyboard.dylib')!==null};
}
function wake() {
    checked(shared('SBBacklightController'),'turnOnScreenFullyWithBacklightSource:','v24@0:8q16','void',['int64'],[1]);
    return {requested:'wake',note:'Read status to verify display state'};
}
function alerts() {
    return controllers().filter(item=>isKind(item.object,'UIAlertController')).map(item=>({
        id:item.id,title:text(send0(item.object,'title')),message:text(send0(item.object,'message'))}));
}
const actions = {
    status, wake,
    'duskie-unlock':()=>{checked(shared('DKLockController'),'authenticateInternally','v16@0:8','void');return {requested:'Duskie simulated-lock authentication',nativePasscodeChanged:false};},
    home:()=>{checked(app(),'_simulateHomeButtonPressWithCompletion:','v24@0:8@?16','void',['pointer'],[ptr(0)]);return {requested:'home'};},
    sleep:()=>{
        if(!boolean(shared('SBBacklightController'),'screenIsOn'))return {changed:false,displayOn:false};
        checked(app(),'_simulateLockButtonPress','v16@0:8','void');return {changed:true,requested:'display off'};
    },
    lock:()=>{checked(shared('SBLockScreenManager'),'lockUIFromSource:withOptions:','v28@0:8i16@20','void',['int','pointer'],[12,ptr(0)]);return {requested:'UI lock'};},
    launch:parameters=>({accepted:Boolean(checked(app(),'launchApplicationWithIdentifier:suspended:','B28@0:8@16B24','bool',['pointer','bool'],[ns(parameters.bundle_id),0]))}),
    apps:()=>{
        const workspace=checked(classNamed('LSApplicationWorkspace'),'defaultWorkspace','@16@0:8');
        const list=send0(workspace,'allInstalledApplications'),result=[];
        for(let i=0;i<count(list);i++){const item=at(list,i);result.push({bundleID:text(send0(item,'bundleIdentifier')),name:text(send0(item,'localizedName'))});}return result;
    },
    controllers:()=>controllers().map(({object,...item})=>item),
    ui:parameters=>{const result=views(parameters.max_nodes);return {...result,nodes:result.nodes.map(({object,...item})=>item)};},
    alerts,
    'dismiss-alert':()=>{
        const match=controllers().filter(item=>isKind(item.object,'UIAlertController'));
        if(match.length!==1)throw Error('Expected exactly one alert, found '+match.length);
        checked(match[0].object,'dismissViewControllerAnimated:completion:','v28@0:8B16@?20','void',['bool','pointer'],[0,ptr(0)]);
        return {requested:'dismiss',actionInvoked:false};
    },
    tap:parameters=>{
        const scanned=views(5000);if(scanned.truncated)throw Error('View traversal truncated; narrow the target process');
        const matches=scanned.nodes.filter(row=>parameters.identifier!==null&&parameters.identifier!==undefined?
            row.identifier===parameters.identifier:[row.label,row.title,row.text].includes(parameters.label));
        const controls=matches.filter(row=>responds(row.object,'sendActionsForControlEvents:'));
        if(controls.length!==1)throw Error('Expected exactly one matching UIControl, found '+controls.length+'; inspect ui and use a unique accessibility ID');
        const target=controls[0].object;
        if(!boolean(target,'isEnabled')||boolean(target,'isHidden'))throw Error('Matching control is disabled or hidden');
        checked(target,'sendActionsForControlEvents:','v24@0:8Q16','void',['uint64'],[64]);
        return {requested:'TouchUpInside',control:controls[0].id,className:controls[0].className};
    },
    methods:parameters=>methodList(parameters.class_name,parameters.class_methods,parameters.matching),
    modules:parameters=>Process.enumerateModules().filter(item=>item.name.toLowerCase().includes(parameters.matching.toLowerCase())).map(item=>({name:item.name,path:item.path,base:item.base.toString(),size:item.size})),
    notify:parameters=>({code:native('notify_post','int',['pointer'])(utf8(parameters.name)),name:parameters.name,completed:false}),
    screenshot:()=>{
        const image=native('UICreateScreenUIImage','pointer',[])();if(image.isNull())throw Error('Screen image unavailable; wake the display first');
        const data=native('UIImagePNGRepresentation','pointer',['pointer'])(image);if(data.isNull())throw Error('PNG encoding failed');
        const encoded=checked(data,'base64EncodedStringWithOptions:','@24@0:8Q16','pointer',['uint64'],[0]);
        return {pngBase64:text(encoded),phoneFileWritten:false};
    },
    'camera.status':cameraStatus,
    'camera.format':videoFormat,
    'camera.mode':parameters=>{
        const c=camera();if(boolean(c,'isRecording'))throw Error('Stop recording before changing mode');
        if(![0,1,2].includes(parameters.mode))throw Error('Only Photo (0), Video (1) and Slo-mo (2) are exposed');
        checked(c,'changeToMode:device:','v32@0:8q16q24','void',['int64','int64'],[parameters.mode,0]);
        return {requestedMode:parameters.mode,device:0,note:'Read camera status after configuration settles'};
    },
    'camera.start':()=>{
        const c=camera();if(![1,2].includes(integer(c,'currentCaptureMode')))throw Error('Select Video or Slo-mo first');
        if(boolean(c,'isRecording'))return {changed:false,recording:true};
        return {accepted:Boolean(checked(c,'startRecording','B16@0:8','bool'))};
    },
    'camera.stop':()=>{const c=camera();if(!boolean(c,'isRecording'))return {changed:false,recording:false};return {accepted:Boolean(checked(c,'stopRecording','B16@0:8','bool'))};},
    'camera.photo':()=>({accepted:Boolean(checked(camera(),'capturePhoto','B16@0:8','bool'))}),
    'camera.fps-next':()=>cameraCycle('fps'),
    'camera.resolution-next':()=>cameraCycle('resolution')
};
rpc.exports.execute = async (action,parameters) => {
    if(action==='unlock') {
        await onMain(wake);
        await new Promise(resolve=>setTimeout(resolve,340));
        return onMain(()=>{
            const lock=shared('SBLockScreenManager');
            const accepted=checked(lock,'_finishUIUnlockFromSource:withOptions:','B28@0:8i16@20','bool',['int','pointer'],[12,ptr(0)]);
            return {accepted:Boolean(accepted),...status(),note:'Verified historical no-passcode procedure; not a device-passcode bypass'};
        });
    }
    if(!Object.hasOwn(actions,action))throw Error('Unknown action '+action);
    return onMain(()=>actions[action](parameters));
};
