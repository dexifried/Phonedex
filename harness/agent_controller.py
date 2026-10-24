"""Headless DEXIFYZ call dispatcher: polls Worker, runs one SIP call at a time.

Secrets stay DPAPI-encrypted in the Windows profile. Nothing listens publicly.
"""
from __future__ import annotations
import argparse
import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from collections import deque

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"vendor"))
from harness.agent_client import AgentGatewayClient
from sip_outbound import dial
from phonebridge.agentphone_trunk import status as trunk_status

LOG=logging.getLogger("dexifyz-agent-controller")
ID=re.compile(r"^call_[a-f0-9]{32}$")
CMD=re.compile(r"^cmd_[a-f0-9]{32}$")
HOME=Path(os.environ.get("PHONEDEX_DATA_DIR",str(Path.home()/".phonedex"))).expanduser()
SEEN=HOME/"dispatched.json"
TERMINATION=os.environ.get("PHONEDEX_TERMINATION_URI","")
OUTBOUND_PORT=int(os.environ.get("PHONEDEX_MEDIA_PORT","41002"))
PUBLIC_IP=os.environ.get("PHONEDEX_PUBLIC_IP","")
CALLER=os.environ.get("PHONEDEX_CALLER_ID","")
MAX_CALLS_PER_DAY=int(os.environ.get("PHONEDEX_MAX_CALLS_PER_DAY","12"))

def load_seen():
    try:
        payload=json.loads(SEEN.read_text(encoding="utf-8"))
        if not isinstance(payload,dict):return {}
        return {k:v for k,v in payload.items() if ID.fullmatch(k) and isinstance(v,str)}
    except (OSError,ValueError,TypeError):
        return {}

def save_seen(seen:dict):
    HOME.mkdir(parents=True,exist_ok=True)
    tmp=SEEN.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen,separators=(",",":")),encoding="utf-8")
    os.replace(tmp,SEEN)

class CallControl:
    def __init__(self,client:AgentGatewayClient,request:dict):
        self.client=client
        self.id=request["call_id"]
        self.mode=request["mode"]
        self.hangup=asyncio.Event()
        self.model=None
        self.handled:set[str]=set()
        self.pending=deque()
        self.received=deque()
        self.batch=0
        self.events_seen=set()
        self.event_order=deque()
        self.received_deltas=0
        self.delivered_deltas=0
        self.failed_uploads=0
        self.started=dt.datetime.now(dt.timezone.utc).isoformat()
        self.state="queued"
        self.status_sequence=0

    async def _post(self,data:dict) -> bool:
        try:
            result=await asyncio.to_thread(self.client.event,{"call_id":self.id,**data})
            if not isinstance(result,dict) or result.get("ok") is not True:
                LOG.warning("Gateway event was not acknowledged: kind=%s",data.get("kind"))
                return False
            return True
        except (RuntimeError,TimeoutError,OSError) as exc:
            LOG.warning("Gateway event delivery failed: kind=%s error=%s",
                        data.get("kind"),type(exc).__name__)
            return False

    async def status(self,state:str,error:str|None=None):
        self.state=state
        sequence=self.status_sequence
        self.status_sequence+=1
        await self._post({"kind":"status","state":state,"sequence":sequence,
                          "started_at":self.started,"error":(error or "")[:110]})

    async def command_ack(self,command_id:str,state:str,error:str|None=None):
        await self._post({"kind":"command","command_id":command_id,"state":state,"error":(error or "")[:100]})

    def on_live_event(self,event:dict):
        typ=event.get("type")
        if typ in ("session.input_transcript.delta","session.output_transcript.delta"):
            fragment=event.get("delta")
            if not isinstance(fragment,str) or not fragment or len(fragment)>1200:
                return
            event_id=event.get("event_id")
            if isinstance(event_id,str) and event_id:
                if event_id in self.events_seen:
                    return
                self.events_seen.add(event_id)
                self.event_order.append(event_id)
                if len(self.event_order)>4096:
                    self.events_seen.discard(self.event_order.popleft())
            speaker="caller" if typ=="session.input_transcript.delta" else "assistant"
            ms=event.get("end_ms",event.get("start_ms",0))
            if not isinstance(ms,(int,float)):ms=0
            self.received.append({"speaker":speaker,"ms":max(0,min(3600000,int(ms))),"text":fragment})
            self.received_deltas+=1
            if self.received_deltas==1:
                LOG.info("First GPT Live transcript fragment captured; speaker=%s",speaker)
            # Backpressure: bound in-memory history, but log overflow instead of silently clearing.
            if len(self.received)>2000:
                LOG.warning("Transcript backlog exceeded 2000; reducing oldest backlog")
                self.received.popleft()
        elif typ=="session.instructions.appended":
            command_id=event.get("client_event_id")
            if isinstance(command_id,str) and CMD.fullmatch(command_id):
                self.pending.append((command_id,"acknowledged",None))
        elif typ=="error":
            ev=event.get("client_event_id") or event.get("event_id")
            if isinstance(ev,str) and CMD.fullmatch(ev):
                self.pending.append((ev,"failed","GPT Live rejected instruction"))

    async def send_steer(self,cmd:dict):
        ident=cmd.get("command_id")
        if not isinstance(ident,str) or not CMD.fullmatch(ident) or ident in self.handled:
            return
        if cmd.get("type")=="hangup":
            self.handled.add(ident)
            self.hangup.set()
            await self.command_ack(ident,"completed")
            return
        if cmd.get("type")!="steer":
            self.handled.add(ident)
            await self.command_ack(ident,"failed","Unknown command")
            return
        instruction=cmd.get("instruction")
        if not isinstance(instruction,str) or len(instruction)>900 or len(instruction)<2:
            self.handled.add(ident)
            await self.command_ack(ident,"failed","Invalid steer size")
            return
        backend=getattr(self.model,"backend",None) if self.model else None
        if backend is None or getattr(backend,"sideband",None) is None:
            # Don't lose a steer sent while the call is dialing.
            return
        self.handled.add(ident)
        try:
            await backend.send_private_steer(ident,instruction)
            await self.command_ack(ident,"submitted")
        except (ValueError,RuntimeError,ConnectionError, OSError) as exc:
            await self.command_ack(ident,"failed",type(exc).__name__)

    async def flush(self):
        while self.pending:
            ident,state,error=self.pending.popleft()
            await self.command_ack(ident,state,error)
        while self.received:
            # A predictable sequence number is essential for retry idempotence.
            batch=list(self.received)[:40]
            index=self.batch
            delivered=await self._post({"kind":"transcript","sequence":index,"items":batch})
            if not delivered:
                self.failed_uploads+=1
                LOG.warning("Transcript upload pending retry: batch=%d fragments=%d",index,len(batch))
                break
            for _ in batch:self.received.popleft()
            self.batch+=1
            self.delivered_deltas+=len(batch)
            LOG.info("Transcript batch acknowledged: batch=%d fragments=%d total=%d",
                     index,len(batch),self.delivered_deltas)

class Controller:
    def __init__(self,client):
        self.client=client
        self.seen=load_seen()
        self.active:CallControl|None=None
        self.task:asyncio.Task|None=None
        self.last_dispatch=0.0
        self.last_flush=0.0

    async def start(self,call:dict):
        cid=call.get("call_id")
        if not isinstance(cid,str) or not ID.fullmatch(cid):return
        if cid in self.seen or self.active is not None:return
        if not isinstance(call.get("to"),str) or not re.fullmatch(r"\+[1-9]\d{6,14}",call["to"]):return
        if call.get("mode") not in ("normal","business"):return
        if not isinstance(call.get("prompt"),str) or not 1<=len(call["prompt"])<=2000:return
        length=call.get("duration_seconds")
        if not isinstance(length,int) or not 20<=length<=300:return
        now=dt.datetime.now(dt.timezone.utc)
        today=now.strftime("%Y-%m-%d")
        if sum(value.startswith(today) for value in self.seen.values())>=MAX_CALLS_PER_DAY:
            LOG.warning("Daily call cap reached; declining queued dispatch")
            control=CallControl(self.client,call)
            await control.status("busy","daily_limit")
            self.seen[cid]=now.isoformat();save_seen(self.seen)
            return
        # Default-deny dial list, checked BEFORE any SIP INVITE.
        allowed={x.strip() for x in os.environ.get("PHONEDEX_ALLOWED_RECIPIENTS","").split(",") if x.strip()}
        if call["to"] not in allowed:
            LOG.warning("Recipient not in configured dial allowlist")
            control=CallControl(self.client,call)
            await control.status("busy","recipient_not_allowed")
            self.seen[cid]=now.isoformat();save_seen(self.seen)
            return
        if time.monotonic()-self.last_dispatch<10:return
        self.seen[cid]=now.isoformat()
        save_seen(self.seen)  # Never redial after restart or lost network ack.
        self.active=CallControl(self.client,call)
        self.last_dispatch=time.monotonic()
        self.task=asyncio.create_task(self.run_call(call,self.active))

    async def run_nat(self,action:str):
        # Explicitly configured router/static port forwarding: never modify
        # firewall, NAT, UPnP or system settings on a user's behalf.
        if os.environ.get("PHONEDEX_MEDIA_PORT_FORWARDED")!="1":
            raise RuntimeError("Set up the SRTP UDP forwarding and then set PHONEDEX_MEDIA_PORT_FORWARDED=1")
        return "MAPPING_OPENED" if action=="Add" else "MAPPING_REMOVED"

    async def run_call(self,call:dict,session:CallControl):
        try:
            trunk_status()  # validates encrypted trunk credentials
            mapped=await self.run_nat("Add")
            if not ("MAPPING_OPENED" in mapped or "MAPPED_TO=" in mapped):
                raise RuntimeError("Outbound NAT mapping wasn't confirmed")
            opts=argparse.Namespace(
                termination_uri=TERMINATION,public_ip=PUBLIC_IP,
                to=call["to"],from_number=CALLER,media_port=OUTBOUND_PORT,
                duration=call["duration_seconds"],mode=call["mode"],
                call_prompt=call["prompt"],
                model_wake_file=(
                    os.environ.get("PHONEDEX_MODEL_WAKE_FILE") or
                    (str(ROOT/"model_wake_prompt.wav") if (ROOT/"model_wake_prompt.wav").is_file() else None)),
                voicemail_mode=os.environ.get("PHONEDEX_VOICEMAIL_MODE","auto"),
                greeting_file=None)
            await session.status("dialing")
            await dial(opts,control=session)
            await session.status("ended")
        except asyncio.CancelledError:
            session.hangup.set()
            await session.status("interrupted")
            raise
        except Exception as exc:
            LOG.warning("Call failed: %s",type(exc).__name__)
            await session.status("failed",type(exc).__name__)
        finally:
            await session.flush()
            LOG.info("TRANSCRIPT_DIAG captured=%d published=%d queued=%d failures=%d call_mode=%s",
                     session.received_deltas,session.delivered_deltas,len(session.received),
                     session.failed_uploads,session.mode)
            try:await self.run_nat("Remove")
            except (RuntimeError,OSError,subprocess.TimeoutExpired):LOG.warning("Outbound mapping cleanup failed")
            self.active=None
            self.task=None

    async def loop_once(self):
        active=self.active
        try:
            result=await asyncio.to_thread(self.client.poll,active.id if active else None)
        except RuntimeError as exc:
            LOG.warning("Worker poll unavailable: %s",exc)
            return
        if active is not None:
            for cmd in result.get("actions",[])[:50]:
                if isinstance(cmd,dict) and cmd.get("call_id")==active.id:
                    await active.send_steer(cmd)
            if time.monotonic()-self.last_flush>5 or active.pending:
                await active.flush()
                self.last_flush=time.monotonic()
        else:
            for item in result.get("calls",[]):
                if isinstance(item,dict):
                    await self.start(item)
                    if self.active:break

    async def run(self):
        while True:
            await self.loop_once()
            await asyncio.sleep(2.7 if self.active else 4.5)

def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    client=AgentGatewayClient()
    LOG.info("Muse controller initialized; no public HTTP listener")
    asyncio.run(Controller(client).run())

if __name__=="__main__":main()
