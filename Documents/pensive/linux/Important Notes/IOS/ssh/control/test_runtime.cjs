// Host-only contract tests for the raw bridge. These do not emulate iOS behavior.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const calls=[];let methodEncoding='B28@0:8@16B24';
function pointer(kind,value){return {kind,value,isNull:()=>false,readUtf8String:()=>value,toString:()=>kind+':'+value};}
const sandbox={console,Map,Set,Promise,Object,Number,Error,setTimeout,
    rpc:{exports:{}},ptr:()=>pointer('ptr',0),
    Memory:{allocUtf8String:text=>pointer('cstring',text)},
    Module:{getGlobalExportByName:name=>name},
    NativeFunction:function(name,result,types){
        return (...args)=>{
            calls.push({name,result,types,args});
            switch(name){
            case 'objc_getClass':return pointer('class',args[0].value);
            case 'sel_registerName':return pointer('selector',args[0].value);
            case 'object_getClass':return pointer('class','Fake');
            case 'class_getInstanceMethod':return pointer('method',args[1].value);
            case 'method_getTypeEncoding':return pointer('encoding',({'isRecording':'B16@0:8','currentCaptureMode':'q16@0:8','changeToMode:device:':'v32@0:8q16q24','startRecording':'B16@0:8'})[args[0].value]||methodEncoding);
            case 'class_getName':return pointer('cstring','Fake');
            case 'objc_msgSend':
                if(args[1].value==='isRecording')return 0;
                if(args[1].value==='currentCaptureMode')return 2;
                if(args[1].value==='isKindOfClass:')return args[0].kind==='NSString'?1:0;
                if(args[1].value==='UTF8String')return pointer('cstring',args[0].value);
                if(args[1].value==='stringWithUTF8String:')return pointer('NSString',args[2].value);
                return 1;
            default:throw Error('Unexpected native operation in host fixture: '+name);
            }
        };
    }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(__dirname+'/runtime.js','utf8')+'\nglobalThis.fixture={checked,ns,text};',sandbox);
const {checked,ns,text}=sandbox.fixture;
const string=ns('com.apple.camera');assert.equal(string.kind,'NSString');
assert.equal(text(string),'com.apple.camera');assert.equal(text(pointer('NSNumber','123')),null);
const receiver=pointer('object','SpringBoard');
let sent=calls.filter(c=>c.name==='objc_msgSend').length;
checked(receiver,'launchApplicationWithIdentifier:suspended:','B28@0:8@16B24','bool',['pointer','bool'],[string,0]);
const launch=calls.at(-1);assert.equal(launch.name,'objc_msgSend');assert.equal(launch.args[2].kind,'NSString');assert.equal(launch.args[3],0);
methodEncoding='v28@0:8@16B24';sent=calls.filter(c=>c.name==='objc_msgSend').length;
assert.throws(()=>checked(receiver,'launchApplicationWithIdentifier:suspended:','B28@0:8@16B24','bool',['pointer','bool'],[string,0]),/ABI mismatch/);
assert.equal(calls.filter(c=>c.name==='objc_msgSend').length,sent,'mismatched ABI must not invoke selector');
assert.throws(()=>checked({isNull:()=>true},'anything','v16@0:8','void'),/Nil receiver/);
sandbox.fixtureCamera=pointer('object','Camera');
vm.runInContext('camera=()=>globalThis.fixtureCamera;globalThis.fixtureCameraActions=actions;',sandbox);
sandbox.fixtureCameraActions['camera.mode']({mode:2});
assert.equal(calls.at(-1).args[2],2);assert.equal(calls.at(-1).args[3],0);
assert.equal(sandbox.fixtureCameraActions['camera.start']().accepted,true);
assert.throws(()=>sandbox.fixtureCameraActions['camera.mode']({mode:99}),/Only Photo/);
(async()=>{
    await assert.rejects(sandbox.rpc.exports.execute('unknown-action',{}),/Unknown action/);
    console.log('PASS: NSString conversion/type validation, explicit BOOL argument, ABI rejection before invocation, nil rejection, Slo-mo mode/start dispatch, unknown-action rejection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
