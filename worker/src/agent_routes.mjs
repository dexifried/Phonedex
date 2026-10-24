// Phonedex remote call control API. Worker persists *instructions and metadata*;
// it does not terminate SIP/RTP and never has access to trunk credentials.
const json = (v,s=200) => new Response(JSON.stringify(v),{status:s,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
const TTL = 60 * 60 * 3, TRANSCRIPT_TTL = 60*60*24;
const E164 = /^\+[1-9]\d{6,14}$/;
const ID = /^[a-z0-9_-]{8,90}$/i;
const CMD = /^[a-z0-9_-]{8,90}$/i;
const sleep = (n) => new Promise(r => setTimeout(r,n));
function equal(a,b) {
 const x=new TextEncoder().encode(a),y=new TextEncoder().encode(b);
 if(x.length!==y.length)return false;
 let v=0;for(let i=0;i<x.length;i++)v|=x[i]^y[i];return v===0;
}
function auth(request, env, role) {
 const secret=role==='controller'?env.AGENT_CONTROL_TOKEN:env.VOICE_GATEWAY_TOKEN;
 const h=request.headers.get('authorization')||'';
 return typeof secret==='string'&&secret.length>=32&&h.startsWith('Bearer ')&&h.length<=300&&equal(secret,h.slice(7));
}
async function bodyJson(request,limit=5000) {
 if(!request.headers.get('content-type')?.toLowerCase().startsWith('application/json'))throw new Error('content_type');
 if(Number(request.headers.get('content-length')||'0')>limit)throw new Error('too_large');
 const raw=await request.text();if(raw.length>limit)throw new Error('too_large');
 const v=JSON.parse(raw);if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('invalid_json');
 return v;
}
const take = (o, keys) => Object.keys(o).every(k=>keys.includes(k));
const get = (env,k) => env.STORE.get(k,'json');
const put = (env,k,v,ttl=TTL) => env.STORE.put(k,JSON.stringify(v),{expirationTtl:ttl});
const list = async (env,prefix,limit=80) => {
 const data=await env.STORE.list({prefix,limit});
 return data.keys.map(x=>x.name).sort();
};
function validCall(input) {
 return take(input,['to','prompt','mode','duration_seconds','request_id'])
  && typeof input.to==='string'&&E164.test(input.to)
  && typeof input.prompt==='string'&&input.prompt.trim().length>0&&input.prompt.length<=2000
  && ['business','normal'].includes(input.mode)
  && Number.isInteger(input.duration_seconds)&&input.duration_seconds>=20&&input.duration_seconds<=300
  && (input.request_id===undefined || (typeof input.request_id==='string'&&ID.test(input.request_id)));
}
async function agentRoute(request, env, pathname) {
 if(!pathname.startsWith('/agent/') && pathname!=='/agent')return null;
 if(!env.STORE)return json({error:'store_unavailable'},503);
 if(!env.AGENT_CONTROL_TOKEN || !env.VOICE_GATEWAY_TOKEN)return json({error:'agent_control_unconfigured'},503);
 const isGateway=pathname.startsWith('/agent/gateway/');
 if(!auth(request,env,isGateway?'gateway':'controller'))return json({error:'unauthorized'},401);
 const method=request.method;
 const segments=pathname.split('/').filter(Boolean);
 try {
   if(segments.length===1) {
     if(method!=='GET')return json({error:'method_not_allowed'},405);
     return json({ok:true,service:'phonedex-agent-control',version:1,
       operations:['POST /agent/calls','GET /agent/calls/{id}','GET /agent/calls/{id}/transcript','POST /agent/calls/{id}/steers','GET /agent/calls/{id}/commands','POST /agent/calls/{id}/hangup'],
       modes:['business','normal'],voiceBackend:'gpt-live',control:'bearer'});
   }
   if(isGateway) {
     if(segments[2]==='poll' && method==='GET') {
       const active=new URL(request.url).searchParams.get('active')||'';
       if(active && !ID.test(active))return json({error:'invalid_id'},400);
       const queued=await list(env,'agent:q:',25);
       const commands=active?await list(env,'agent:cmd:'+active+':',60):[];
       // Each immutable record is fetched separately; status updates live in their own keys.
       const calls=(await Promise.all(queued.slice(0,12).map(k=>get(env,k)))).filter(Boolean);
       const actions=(await Promise.all(commands.slice(0,40).map(k=>get(env,k)))).filter(Boolean);
       return json({calls,actions,at:new Date().toISOString()});
     }
     if(segments[2]==='events' && method==='POST'){
       const v=await bodyJson(request,12500);
       if(!ID.test(v.call_id||''))return json({error:'invalid_call_id'},400);
       const exists=await get(env,'agent:q:'+v.call_id);
       if(!exists)return json({error:'unknown_call'},404);
       if(v.kind==='status') {
         if(!['dialing','ringing','answered','ending','ended','failed','busy','interrupted'].includes(v.state)||!Number.isInteger(v.sequence)||v.sequence<0||v.sequence>999)return json({error:'invalid_state'},400);
         const record={call_id:v.call_id,state:v.state,updated_at:new Date().toISOString(),
           error:typeof v.error==='string'?v.error.slice(0,120):null,mode:exists.mode,to:exists.to,
           started_at:typeof v.started_at==='string'?v.started_at:null};
         await put(env,'agent:st:'+v.call_id+':'+String(v.sequence).padStart(4,'0')+'-'+crypto.randomUUID(),record);
         return json({ok:true});
       }
       if(v.kind==='transcript') {
         if(!Number.isInteger(v.sequence)||v.sequence<0||v.sequence>9999||!Array.isArray(v.items)||v.items.length>70)return json({error:'invalid_transcript'},400);
         const items=v.items.map(item=>({speaker:item.speaker,text:item.text,ms:item.ms})).filter(x=>
           ['caller','assistant'].includes(x.speaker)&&typeof x.text==='string'&&x.text.length<=1200
            && Number.isFinite(x.ms)&&x.ms>=0&&x.ms<36000000);
         if(items.length!==v.items.length)return json({error:'invalid_transcript_item'},400);
         await put(env,'agent:tx:'+v.call_id+':'+String(v.sequence).padStart(5,'0'),{items,created_at:new Date().toISOString()},TRANSCRIPT_TTL);
         return json({ok:true,accepted:items.length});
       }
       if(v.kind==='command') {
         if(!CMD.test(v.command_id||'')||!['submitted','acknowledged','failed','completed'].includes(v.state))return json({error:'invalid_command_state'},400);
         await put(env,'agent:ack:'+v.call_id+':'+v.command_id+':'+v.state,{command_id:v.command_id,state:v.state,
           error:typeof v.error==='string'?v.error.slice(0,110):null,updated_at:new Date().toISOString()});
         return json({ok:true});
       }
       return json({error:'invalid_kind'},400);
     }
     return json({error:'not_found'},404);
   }
   if(segments[1]==='calls' && segments.length===2 && method==='POST') {
     const v=await bodyJson(request);
     if(!validCall(v))return json({error:'invalid_call_arguments',expected:{to:'E.164',mode:'business|normal',prompt:'1..2000 chars',duration_seconds:'20..300'}},400);
     let id='call_'+crypto.randomUUID().replaceAll('-','');
     if(v.request_id){
       const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v.request_id)));
       id='call_'+[...digest].map(x=>x.toString(16).padStart(2,'0')).join('').slice(0,32);
     }
     const existing=await get(env,'agent:q:'+id);
     if(existing){
       const same=existing.to===v.to&&existing.mode===v.mode
         &&existing.prompt===v.prompt&&existing.duration_seconds===v.duration_seconds;
       if(!same)return json({error:'request_id_conflict'},409);
       return json({ok:true,call_id:id,state:'already_queued',mode:v.mode,duplicate:true},200);
     }
     const record={call_id:id,to:v.to,prompt:v.prompt,mode:v.mode,
       duration_seconds:v.duration_seconds,created_at:new Date().toISOString()};
     await put(env,'agent:q:'+id,record);
     return json({ok:true,call_id:id,state:'queued',mode:v.mode,expires_in_seconds:TTL},202);
   }
   if(segments[1]==='calls' && ID.test(segments[2]||'')){
     const id=segments[2],call=await get(env,'agent:q:'+id);
     if(!call)return json({error:'unknown_or_expired_call'},404);
     if(segments.length===3&&method==='GET') {
       const states=await list(env,'agent:st:'+id+':',60);
       const status=states.length?await get(env,states.at(-1)):null;
       return json({call_id:id,to:call.to,mode:call.mode,
         state:status?.state||'queued',created_at:call.created_at,updated_at:status?.updated_at||null,
         error:status?.error||null,duration_seconds:call.duration_seconds});
     }
     if(segments[3]==='transcript' && method==='GET'&&segments.length===4){
       const keys=await list(env,'agent:tx:'+id+':',100);
       const chunks=await Promise.all(keys.slice(-50).map(key=>get(env,key)));
       const items=chunks.filter(Boolean).flatMap(x=>x.items).slice(-350);
       return json({call_id:id,items,has_more:keys.length>50});
     }
     if(segments[3]==='commands' && method==='GET'&&segments.length===4) {
       const keys=await list(env,'agent:cmd:'+id+':',70);
       const commands=await Promise.all(keys.map(k=>get(env,k)));
       const ackKeys=await list(env,'agent:ack:'+id+':',100);
       const ackDocs=(await Promise.all(ackKeys.map(k=>get(env,k)))).filter(Boolean);
       const latest=new Map();
       const order={pending:0,submitted:1,acknowledged:2,failed:3,completed:4};
       for(const a of ackDocs){
         const previous=latest.get(a.command_id);
         if(!previous || (order[a.state]||0)>=(order[previous.state]||0))latest.set(a.command_id,a);
       }
       return json({call_id:id,commands:commands.filter(Boolean).map(x=>({
         command_id:x.command_id,type:x.type,created_at:x.created_at,
         status:latest.get(x.command_id)?.state||'pending',
         error:latest.get(x.command_id)?.error||null}))});
     }
     if(segments.length===4&&segments[3]==='steers'&&method==='POST'){
       const states=await list(env,'agent:st:'+id+':',60);
       const state=states.length?await get(env,states.at(-1)):null;
       if(state&&['ended','failed','busy','interrupted'].includes(state.state))return json({error:'call_already_ended'},409);
       const v=await bodyJson(request,2500);
       if(!take(v,['instruction'])||typeof v.instruction!=='string'||v.instruction.trim().length<2||v.instruction.length>900)return json({error:'invalid_instruction'},400);
       const command_id='cmd_'+crypto.randomUUID().replaceAll('-','');
       await put(env,'agent:cmd:'+id+':'+command_id,{call_id:id,command_id,type:'steer',
         instruction:v.instruction,created_at:new Date().toISOString()});
       return json({ok:true,call_id:id,command_id,status:'pending'},202);
     }
     if(segments.length===4&&segments[3]==='hangup'&&method==='POST'){
       const command_id='cmd_'+crypto.randomUUID().replaceAll('-','');
       await put(env,'agent:cmd:'+id+':'+command_id,{call_id:id,command_id,type:'hangup',created_at:new Date().toISOString()});
       return json({ok:true,call_id:id,command_id,status:'pending'},202);
     }
   }
   return json({error:'not_found'},404);
 } catch(err){
   if(err instanceof SyntaxError)return json({error:'invalid_json'},400);
   if(err.message==='too_large')return json({error:'request_too_large'},413);
   if(err.message==='content_type')return json({error:'expected_json'},415);
   console.error('Phonedex agent route failure',err?.name||'error');
   return json({error:'internal_error'},500);
 }
}
export { agentRoute };
