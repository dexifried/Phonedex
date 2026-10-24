import assert from 'node:assert/strict';
import {agentRoute} from './src/agent_routes.mjs';
class FakeKV{
 constructor(){this.items=new Map()}
 async get(k,type){const v=this.items.get(k);return v?(type==='json'?JSON.parse(v):v):null}
 async put(k,v){this.items.set(k,v)}
 async list({prefix,limit}){return {keys:[...this.items.keys()].filter(x=>x.startsWith(prefix)).slice(0,limit).map(name=>({name}))}}
}
const env={STORE:new FakeKV(),AGENT_CONTROL_TOKEN:'ctrl_'+ 'a'.repeat(52),VOICE_GATEWAY_TOKEN:'gate_'+ 'b'.repeat(52)};
async function route(method,path,data=null,token=env.AGENT_CONTROL_TOKEN){
 const h={authorization:'Bearer '+token};
 if(data)h['content-type']='application/json';
 return agentRoute(new Request('https://phonedex.example'+path,{
  method,headers:h,...(data?{body:JSON.stringify(data)}:{})
 }),env,new URL('https://phonedex.example'+path).pathname);
}
assert.equal((await route('GET','/agent/','', 'bad')).status,401);
assert.equal((await route('GET','/agent/')).status,200);
let response=await route('POST','/agent/calls',{to:'+12025550123',mode:'normal',prompt:'Ask about an appointment',duration_seconds:60});
assert.equal(response.status,202);
const created=await response.json();assert.ok(created.call_id.startsWith('call_'));
const id=created.call_id;
assert.equal((await route('GET','/agent/calls/'+id)).status,200);
const polled=await (await route('GET','/agent/gateway/poll',null,env.VOICE_GATEWAY_TOKEN)).json();
assert.equal(polled.calls[0].mode,'normal');
assert.equal(polled.calls[0].prompt,'Ask about an appointment');
assert.equal((await route('GET','/agent/gateway/poll',null,env.AGENT_CONTROL_TOKEN)).status,401);
assert.equal((await route('POST','/agent/gateway/events',{call_id:id,kind:'status',sequence:0,state:'answered'},env.VOICE_GATEWAY_TOKEN)).status,200);
assert.equal((await (await route('GET','/agent/calls/'+id)).json()).state,'answered');
await route('POST','/agent/gateway/events',{call_id:id,kind:'status',sequence:1,state:'ending'},env.VOICE_GATEWAY_TOKEN);
assert.equal((await (await route('GET','/agent/calls/'+id)).json()).state,'ending');
response=await route('POST','/agent/calls/'+id+'/steers',{instruction:'Ask whether the caller is free tomorrow.'});
assert.equal(response.status,202);const steer=await response.json();
const poll2=await (await route('GET','/agent/gateway/poll?active='+id,null,env.VOICE_GATEWAY_TOKEN)).json();
assert.equal(poll2.actions[0].command_id,steer.command_id);
assert.equal((await route('POST','/agent/gateway/events',{call_id:id,kind:'transcript',sequence:0,items:[
 {speaker:'caller',ms:500,text:'Hey Phonedex'}, {speaker:'assistant',ms:600,text:'Hi there'}
]},env.VOICE_GATEWAY_TOKEN)).status,200);
const transcript=await (await route('GET','/agent/calls/'+id+'/transcript')).json();
assert.equal(transcript.items.length,2);
assert.equal((await route('POST','/agent/gateway/events',{call_id:id,kind:'command',command_id:steer.command_id,state:'submitted'},env.VOICE_GATEWAY_TOKEN)).status,200);
assert.equal((await route('POST','/agent/gateway/events',{call_id:id,kind:'command',command_id:steer.command_id,state:'acknowledged'},env.VOICE_GATEWAY_TOKEN)).status,200);
const commands=await (await route('GET','/agent/calls/'+id+'/commands')).json();
assert.equal(commands.commands[0].status,'acknowledged');
assert.equal((await route('POST','/agent/calls/'+id+'/hangup',{})).status,202);
const reused={to:'+12025550123',mode:'normal',prompt:'Follow up about a meeting',duration_seconds:60,request_id:'muse_test_req_1234'};
const first=await (await route('POST','/agent/calls',reused)).json();
const second=await (await route('POST','/agent/calls',reused)).json();
assert.equal(first.call_id,second.call_id);
assert.equal(second.duplicate,true);
const conflict=await route('POST','/agent/calls',{...reused,prompt:'Different instruction'});
assert.equal(conflict.status,409);
console.log('PASS: bearer auth, dispatch, separate modes, poll, status, transcript, steer, ack, hangup');
