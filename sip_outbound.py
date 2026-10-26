"""Standalone outbound SIP/TLS + SDES-SRTP dialer for SIP trunk.

Separate from a provider's HTTP calling API and existing Muse webhook.

Requires: --termination-uri from authenticated SIP trunk, --credential-source
pointing to the local DPAPI-backed bridge adapter (not plaintext),
--public-ip, --dial, and established UDP port mapping.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import hashlib
import ipaddress
import logging
import re
import secrets
import ssl
import sys
import time
import wave
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor'))
from pylibsrtp import Session, Policy
from phonebridge.audio import RTPPacket
from phonebridge.voicemail import VoicemailScreen
from bridge import RTPBridge
from sip_tls_gateway import Call, SecureMedia, parse_sdp
from phonebridge.agentphone_trunk import load_credentials

LOG = logging.getLogger('sip-outbound')
E164 = re.compile(r'^\+[1-9]\d{6,14}$')


def terminate_target(uri):
    if not (uri.startswith('sip:') or uri.startswith('sips:')):
        raise ValueError('Provide actual SIP trunk termination URI, not a web URL')
    rest = uri.split(':',1)[1].split(';',1)[0].rsplit('@',1)[-1]
    if not re.fullmatch(r'[a-zA-Z0-9.-]+(?::[0-9]{2,5})?', rest):
        raise ValueError('Invalid SIP hostname')
    hostname, sep, port = rest.partition(':')
    if hostname.lower() == 'localhost' or hostname == '127.0.0.1':
        raise ValueError('External SIP trunk required')
    n = int(port) if sep else 5061
    if not 1024 <= n <= 65535:
        raise ValueError('Invalid port')
    return hostname, n


def sip_digest(challenge, method, uri, user, password):
    kv = dict((a.lower(), b[1:-1] if b.startswith('"') else b)
        for a,b in re.findall(r'([\w-]+)\s*=\s*("[^"]*"|[^,\s]+)',challenge))
    realm,nonce=kv.get('realm'),kv.get('nonce')
    if not realm or not nonce: raise ValueError('Missing digest challenge')
    if kv.get('algorithm','MD5').upper() != 'MD5':
        raise ValueError('Only standard MD5 digest supported')
    qop={x.strip() for x in kv.get('qop','').split(',')}
    if qop != {''} and 'auth' not in qop:
        raise ValueError('Unsupported authentication quality')
    md5=lambda t:hashlib.md5(t.encode()).hexdigest()
    ha1=md5(f'{user}:{realm}:{password}')
    ha2=md5(f'{method}:{uri}')
    fields={'username':user,'realm':realm,'nonce':nonce,'uri':uri}
    if 'auth' in qop:
        nc='00000001';cnonce=secrets.token_hex(8)
        digest=md5(f'{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}')
    else:
        digest=md5(f'{ha1}:{nonce}:{ha2}')
    fields['response']=digest
    if 'opaque' in kv:fields['opaque']=kv['opaque']
    text='Digest '+', '.join(f'{k}="{v}"' for k,v in fields.items())
    if 'auth' in qop:text+=f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    return text


def invite_sdp(ip,port,key):
    ip4=ipaddress.ip_address(ip)
    if not (ip4.version==4 and ip4.is_global):
        raise ValueError('Real public IPv4 address is required')
    if len(key)!=30:raise ValueError('Invalid SRTP key')
    return (f'v=0\r\no=phonedex 1 1 IN IP4 {ip}\r\ns=Phonedex Voice\r\nc=IN IP4 {ip}\r\nt=0 0\r\n'
      f'm=audio {port} RTP/SAVP 0\r\na=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n'
      f'a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{base64.b64encode(key).decode()}\r\n')


def request(method,uri,fields,body=''):
    data=body.encode('ascii')
    lines=[f'{method} {uri} SIP/2.0']+[f'{k}: {v}' for k,v in fields]
    if body:lines.append('Content-Type: application/sdp')
    lines.append(f'Content-Length: {len(data)}')
    return ('\r\n'.join(lines)+'\r\n\r\n').encode()+data


async def response(reader):
    raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),timeout=45)
    if len(raw)>16384:raise ValueError('Oversized SIP headers')
    parts=raw.decode('latin-1').split('\r\n')
    line=parts[0].split(' ',2); code=int(line[1]); headers={'_status':line[2] if len(line)>2 else ''}
    for line in parts[1:]:
        if ':' in line:
            k,v=line.split(':',1);headers[k.lower().strip()]=v.strip()
    size=int(headers.get('content-length','0'))
    if size<0 or size>32768:raise ValueError('Oversized SIP body')
    data=await asyncio.wait_for(reader.readexactly(size),timeout=8) if size else b''
    return code,headers,data.decode('latin-1')


async def final_response(reader):
    while True:
        code,h,b=await response(reader)
        if code>=200:return code,h,b


async def watch_remote_disconnect(reader, writer):
    """Recognize carrier BYE/CANCEL or closed SIP/TLS signaling leg."""
    while True:
        try:
            raw=await reader.readuntil(b'\r\n\r\n')
        except (asyncio.IncompleteReadError,ConnectionError,OSError):
            return "transport_closed"
        if len(raw)>16384:
            return "invalid_signaling"
        lines=raw.decode('latin-1','replace').split('\r\n')
        first=lines[0].split()
        headers={}
        for line in lines[1:]:
            if ':' in line:
                k,v=line.split(':',1)
                headers[k.strip().lower()]=v.strip()
        try:
            length=int(headers.get('content-length','0'))
            if not 0<=length<=32768:return "invalid_signaling"
            if length:await reader.readexactly(length)
        except (ValueError,asyncio.IncompleteReadError):
            return "transport_closed"
        if not first or first[0] not in ('BYE','CANCEL'):
            continue
        method=first[0]
        reply=['SIP/2.0 200 OK']
        for name in ('via','from','to','call-id','cseq'):
            if name in headers:
                reply.append(name.title()+': '+headers[name])
        reply.append('Content-Length: 0')
        try:
            writer.write(('\r\n'.join(reply)+'\r\n\r\n').encode('latin-1'))
            await writer.drain()
        except (OSError,ConnectionError):
            pass
        LOG.info('Carrier requested end of phone call (%s)',method)
        return 'remote_bye'



class Gateway:
    def __init__(self):
        self.call=None
        self.model_port=0


async def supervise_answer_screen(model: RTPBridge, screen: VoicemailScreen,
                                  stop: asyncio.Event) -> str:
    """Wait for a human answer or a completed beep. Never speak on uncertainty.

    Not dependent on inbound RTP continuing forever: carriers sometimes
    stop RTP after an IVR greeting. This watchdog cannot fabricate a beep.
    """
    clock=asyncio.get_running_loop()
    start=clock.time()
    while not screen.event.is_set():
        elapsed=clock.time()-start
        if elapsed>=3.6 and screen.frames==0:
            screen.decide('human')
        elif elapsed>=5.0 and not screen.machine and screen.frames>0 and screen.spoken==0:
            # Initial silence/non-speech may indicate a quiet human; a
            # partly heard greeting is never reclassified as human purely
            # because wall-clock time elapsed. Long uncertain greetings
            # fail closed rather than talking over a recording.
            screen.decide('human')
        elif elapsed>=27.0:
            screen.decide('no_beep')
        if not screen.event.is_set():
            try:
                await asyncio.wait_for(screen.event.wait(),timeout=.20)
            except asyncio.TimeoutError:
                pass
    decision=screen.decision or 'no_beep'
    if decision=='no_beep':
        LOG.info('Voicemail screening gave no reliable beep; ending without a message')
        stop.set()
    else:
        model.release_answer_screen(decision)
    LOG.info('Outbound answering-machine screen result=%s',decision)
    return decision


async def play_outbound_greeting(model:RTPBridge, media_port:int, file_path:Path):
    """Play a bounded pre-rendered spoken greeting into the existing RTP sender.

    This is independent of GPT Live generating its own first turn.
    Reuses the RTPBridge speaker sequence, SSRC and SRTP gateway.
    """
    if not file_path.is_file() or file_path.stat().st_size > 1_000_000:
        raise ValueError('Expected small, locally rendered greeting WAV file')
    with wave.open(str(file_path),'rb') as f:
        if (f.getnchannels(),f.getsampwidth(),f.getframerate()) != (1,2,8000):
            raise ValueError('Greeting must be mono 8-kHz PCM16 WAV')
        samples=f.readframes(f.getnframes())
    if not (16000 <= len(samples) <= 160000):
        raise ValueError('Greeting must be 1-10 seconds')
    # Wait briefly for the first inbound RTP packet. If the provider sends
    # nothing, pin the known local media socket and start the model ourselves.
    deadline=asyncio.get_running_loop().time()+2.0
    while model.peer is None and asyncio.get_running_loop().time()<deadline:
        await asyncio.sleep(.05)
    if model.peer is None:
        model.peer=('127.0.0.1',media_port)
        model.last_input=time.monotonic()
        if model.model_task is None:
            model.model_task=asyncio.create_task(model.model_loop())
    LOG.info('Playing local phone greeting (%s PCM16 bytes)',len(samples))
    for pos in range(0,len(samples),320):
        packet=samples[pos:pos+320]
        if len(packet)<320:packet+=b'\0'*(320-len(packet))
        model.on_model_audio(packet,8000)
        await asyncio.sleep(.02)
    LOG.info('Local phone greeting queued completely')


async def dial(opts, control=None):
    if not E164.fullmatch(opts.to) or not E164.fullmatch(opts.from_number):
        raise ValueError('Phone numbers must be E.164')
    hostname,port=terminate_target(opts.termination_uri)
    if opts.termination_uri.find(opts.public_ip)>=0:
        raise ValueError('Do not dial our inbound gateway as termination')
    credentials=load_credentials()
    key=secrets.token_bytes(30)
    offer=invite_sdp(opts.public_ip,opts.media_port,key)
    uri=f'sips:{opts.to}@{hostname}:{port}'
    origin=f'<sip:{opts.from_number}@{hostname}>;tag={secrets.token_hex(7)}'
    destination=f'<sip:{opts.to}@{hostname}>'
    call_id=secrets.token_hex(12)+'@'+opts.public_ip
    gateway=Gateway();loop=asyncio.get_running_loop()
    mode=getattr(opts,'mode','business')
    if mode not in ('business','normal'):raise ValueError('Invalid call mode')
    template='business-prompt.txt' if mode=='business' else 'normal-prompt.txt'
    assignment=getattr(opts,'call_prompt','').strip()
    if len(assignment)>2000:raise ValueError('Controller prompt too long')
    prompt=(ROOT/template).read_text(encoding='utf-8')
    if assignment:
        prompt += '\nCall-specific instructions from the dispatch controller (not to be quoted or described as instructions):\n' + assignment
    # Independent opt-in per outbound attempt; the incoming receptionist
    # never uses voicemail screening.
    voicemail_mode=getattr(opts,'voicemail_mode','auto')
    if voicemail_mode not in ('auto','off'):
        raise ValueError('Invalid voicemail mode')
    dashboard_control=None
    if os.environ.get('PHONEDEX_DASHBOARD_EVENTS')=='1':
        from controlcentre.integration import instrument,DashboardControl
        control=instrument(opts,control)
        if isinstance(control,DashboardControl):
            dashboard_control=control
    screen=VoicemailScreen() if voicemail_mode=='auto' else None
    model_args=argparse.Namespace(backend='subscription',prompt=prompt,idle_seconds=45,
        codex_exe='codex',model_wake_file=opts.model_wake_file,call_control=control,
        outbound_answer_screen=screen)
    model=RTPBridge(model_args)
    try:
        model_socket,_=await loop.create_datagram_endpoint(lambda:model,local_addr=('127.0.0.1',0))
        gateway.model_port=model_socket.get_extra_info('sockname')[1]
        try:
            media_socket,media_protocol=await loop.create_datagram_endpoint(lambda:SecureMedia(gateway),local_addr=('0.0.0.0',opts.media_port))
        except BaseException:
            model_socket.close()
            await model.close()
            raise
    except BaseException:
        if dashboard_control is not None:await dashboard_control.close('failed')
        raise
    if dashboard_control is not None:dashboard_control.start()
    writer=None
    greeting_task=None
    screen_task=None
    screen_abort=asyncio.Event()
    dashboard_outcome='ended'
    try:
        ctx=ssl.create_default_context()
        reader,writer=await asyncio.wait_for(asyncio.open_connection(hostname,port,ssl=ctx,server_hostname=('pstn.agentphone.ai' if hostname.endswith('.pstn.agentphone.ai') else hostname)),15)
        LOG.info('Verified SIP trunk TLS connection established')
        if control: await control.status('dialing')
        cseq=1
        def headers(method,branch,to=destination,auth=None):
            rows=[('Via',f'SIP/2.0/TLS {opts.public_ip}:5061;branch={branch};rport'),('Max-Forwards','70'),
                 ('From',origin),('To',to),('Call-ID',call_id),('CSeq',f'{cseq} {method}'),
                 ('Contact',f'<sip:{opts.from_number}@{opts.public_ip}:5061;transport=tls>'),
                 ('User-Agent','Phonedex/0.1')]
            if auth:rows.append(auth)
            return rows
        branch='z9hG4bK'+secrets.token_hex(8)
        writer.write(request('INVITE',uri,headers('INVITE',branch),offer));await writer.drain()
        code,h,body=await final_response(reader)
        if code in (401,407):
            writer.write(request('ACK',uri,headers('ACK',branch,h.get('to',destination))))
            await writer.drain()
            challenge=h.get('proxy-authenticate') if code==407 else h.get('www-authenticate')
            if not challenge:raise RuntimeError('SIP authentication challenge missing')
            digest=sip_digest(challenge,'INVITE',uri,credentials['username'],credentials['password'])
            cseq+=1; branch='z9hG4bK'+secrets.token_hex(8)
            auth_header='Proxy-Authorization' if code==407 else 'Authorization'
            writer.write(request('INVITE',uri,headers('INVITE',branch,auth=(auth_header,digest)),offer));await writer.drain()
            code,h,body=await final_response(reader)
        LOG.info('SIP termination returned %s %s; reason=%s warning=%s',code,h.get('_status',''),h.get('reason',''),h.get('warning',''))
        LOG.warning('SIP_DIAG='+str({k:h.get(k) for k in ('server','reason','warning','x-twilio-error','x-error-code')}))
        if code!=200:raise RuntimeError(f'Carrier SIP {code}, call not connected')
        remote,remote_key=parse_sdp(body,False)
        gateway.call=Call(call_id,remote,origin,destination,'',secrets.token_hex(7),
            Session(policy=Policy(key=remote_key,ssrc_type=Policy.SSRC_ANY_INBOUND)),
            Session(policy=Policy(key=key,ssrc_type=Policy.SSRC_ANY_OUTBOUND)))
        dialog=h.get('to',destination)
        writer.write(request('ACK',uri,headers('ACK','z9hG4bK'+secrets.token_hex(8),dialog)));await writer.drain()
        LOG.info('Call answered, secure audio leg established')
        # Avoid carrier/model deadlock: start GPT Live even if the carrier
        # waits for our first encrypted audio packet.
        model.activate_inbound(opts.media_port)
        if screen is not None:
            screen_task=asyncio.create_task(
                supervise_answer_screen(model,screen,screen_abort))
            LOG.info('Outbound call screening active; RTP silent pending decision')
        else:
            LOG.info('Outbound GPT Live primed before carrier RTP')
        if control:
            control.model=model
            await control.status('answered')
        # No local TTS is ever played to the caller; GPT Live speaks naturally.
        hangup_event=control.hangup if control else asyncio.Event()
        tasks={
            asyncio.create_task(hangup_event.wait()):'local_hangup',
            asyncio.create_task(watch_remote_disconnect(reader,writer)):'remote',
            asyncio.create_task(asyncio.sleep(opts.duration)):'duration',
            asyncio.create_task(screen_abort.wait()):'voicemail_no_beep',
        }
        done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        winning=next(iter(done))
        outcome=winning.result() if tasks[winning]=='remote' else tasks[winning]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        LOG.info('Outbound call ending; reason=%s',outcome)
        if greeting_task:
            if greeting_task.done():
                await greeting_task
            else:
                LOG.warning('Greeting did not complete during call')
        if control: await control.status('ending')
        cseq+=1
        if outcome not in ('remote_bye','transport_closed','invalid_signaling'):
            writer.write(request('BYE',uri,headers('BYE','z9hG4bK'+secrets.token_hex(8),dialog)))
            await writer.drain()
    except BaseException:
        dashboard_outcome='failed'
        raise
    finally:
        if screen_task is not None:
            screen_task.cancel()
            await asyncio.gather(screen_task,return_exceptions=True)
        if control: control.model=None
        if greeting_task and not greeting_task.done():
            greeting_task.cancel()
            await asyncio.gather(greeting_task,return_exceptions=True)
        LOG.info('Carrier SRTP packets received=%s; returned=%s',media_protocol.accepted,media_protocol.returned)
        gateway.call=None
        media_socket.close()
        await model.close()
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (ssl.SSLError, ConnectionError, OSError) as exc:
                LOG.warning('Carrier TLS connection closed with shutdown warning: %s',type(exc).__name__)
        if dashboard_control is not None:
            try:await dashboard_control.close(dashboard_outcome)
            except Exception as exc:LOG.warning('Dashboard cleanup unavailable (%s)',type(exc).__name__)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--termination-uri',required=True)
    p.add_argument('--public-ip',required=True)
    p.add_argument('--to',default='')
    p.add_argument('--from-number',default='')
    p.add_argument('--media-port',type=int,default=41000)
    p.add_argument('--model-wake-file',default=None)
    p.add_argument('--voicemail-mode',choices=('auto','off'),default='auto',
                   help='detect a human answer or wait until the voicemail beep before GPT Live speaks')
    # Compatibility only: legacy test launcher still supplies this option.
    # The file is NEVER played to the caller, preventing dual voices/glitches.
    p.add_argument('--greeting-file',default=None,help=argparse.SUPPRESS)
    p.add_argument('--duration',type=int,default=60)
    p.add_argument('--mode',choices=('business','normal'),default='business')
    p.add_argument('--call-prompt',default='')
    p.add_argument('--dial',action='store_true')
    opts=p.parse_args()
    hostname,port=terminate_target(opts.termination_uri)
    if not E164.fullmatch(opts.to):p.error('Invalid caller number')
    if opts.dial:
        if not 10<=opts.duration<=180:p.error('Invalid call length')
        logging.basicConfig(level=logging.INFO)
        asyncio.run(dial(opts))
    else:
        invite_sdp(opts.public_ip,opts.media_port,secrets.token_bytes(30))
        print(f'OUTBOUND_DRY_RUN_OK host={hostname} port={port}; NO CALL PLACED')

if __name__=='__main__':main()
