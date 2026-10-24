"""Experimental Phonedex SIP/TLS + SDES-SRTP gateway for the standalone live bridge.

Defaults to loopback-only and only accepts secure SIP/TLS and encrypted SRTP.
WARNING: This is a prototype, not an audited or carrier-certified SBC.
Do not bind it publicly until provider compatibility, TLS certificates,
router forwarding, resilience and network allowlists have been verified.

Usage (localhost test):
    python sip_tls_gateway.py --cert tests/local.crt --key tests/local.key --backend echo

For production a trusted certificate, public routable IP, approved source
CIDRs and explicit --enable-public are required. No API-billed fallback.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import ipaddress
import logging
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
from dataclasses import dataclass

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
VENDOR=ROOT/'vendor'
if str(VENDOR) not in sys.path:sys.path.insert(0,str(VENDOR))
from pylibsrtp import Policy, Session, Error as SrtpError
from bridge import RTPBridge
from phonebridge.audio import RTPPacket

LOG=logging.getLogger('phonedex-sip-tls')
MAX_HEADERS=8192
MAX_SDP=16384
KEY_RE=re.compile(r'^a=crypto:\d+ AES_CM_128_HMAC_SHA1_80 inline:([a-zA-Z0-9+/=]+)',re.M)


@dataclass
class Call:
    ident:str
    remote_media:tuple[str,int]
    from_header:str
    to_header:str
    via_header:str
    to_tag:str
    inbound_srtp:Session
    outbound_srtp:Session
    active:bool=True
    bye_sent:bool=False


def parse_sdp(sdp:str,allow_loopback:bool):
    media=re.search(r'^m=audio\s+(\d+)\s+RTP/SAVP\s+([^\r\n]+)',sdp,re.M)
    if media is None or '0' not in media.group(2).split():
        raise ValueError('encrypted PCMU RTP/SAVP required')
    port=int(media.group(1))
    if port<1024 or port>65535:raise ValueError('invalid RTP port')
    address=re.findall(r'^c=IN IP4\s+([^\s\r\n]+)',sdp,re.M)
    if len(address)!=1:raise ValueError('must have one media IPv4 address')
    ip=ipaddress.ip_address(address[0])
    if ip.version!=4 or ip.is_multicast or ip.is_unspecified:
        raise ValueError('invalid IPv4 address')
    if allow_loopback and ip!=ipaddress.ip_address('127.0.0.1'):
        raise ValueError('local testing only accepts loopback media')
    if not allow_loopback and ip.is_loopback:
        raise ValueError('provider cannot offer loopback media')
    match=KEY_RE.search(sdp)
    if match is None:raise ValueError('supported SRTP crypto suite missing')
    key=base64.b64decode(match.group(1),validate=True)
    if len(key)!=30:raise ValueError('invalid SRTP master key/salt length')
    return (str(ip),port),key


def response_bytes(status:str,h:dict,body:str='',to_tag:str='',contact:str='')->bytes:
    to=h.get('to','')
    if to_tag and ';tag=' not in to.lower():to+=';tag='+to_tag
    body_bytes=body.encode('ascii')
    lines=[
        'SIP/2.0 '+status,
        *['Via: '+v for v in (h.get('_via_lines') or [h.get('via','')])],
        *['Record-Route: '+v for v in h.get('_record_route_lines',[])],
        'From: '+h.get('from',''),
        'To: '+to,
        'Call-ID: '+h.get('call-id',''),
        'CSeq: '+h.get('cseq',''),
        'Server: Phonedex-Standalone-Voice/0.1',
        'Allow: INVITE, ACK, BYE, CANCEL, OPTIONS',
        'Content-Length: '+str(len(body_bytes)),
    ]
    if contact and body_bytes and status.startswith('200 '):
        lines.insert(-1,'Contact: <'+contact+'>')
    if body_bytes:lines.insert(-1,'Content-Type: application/sdp')
    return ('\r\n'.join(lines)+'\r\n\r\n').encode('ascii')+body_bytes


async def receive_sip(reader):
    raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),timeout=25)
    if len(raw)>MAX_HEADERS:raise ValueError('SIP headers too large')
    lines=raw.decode('latin-1').split('\r\n')
    parts=lines[0].split(' ')
    if len(parts)<3:raise ValueError('invalid SIP start line')
    is_response=parts[0]=='SIP/2.0' and len(parts[1])==3 and parts[1].isdigit()
    if not is_response and parts[-1]!='SIP/2.0':
        raise ValueError('invalid SIP request')
    h={};via=[];routes=[]
    for line in lines[1:]:
        if not line:continue
        if line.startswith((' ','\t')):raise ValueError('folded header rejected')
        if ':' not in line:continue
        k,v=line.split(':',1);k=k.strip().lower();v=v.strip()
        if k in ('via','v'):
            via.append(v)
            if 'via' not in h:h['via']=v
        elif k=='record-route':
            routes.append(v)
        else:h[k]=v
    h['_via_lines']=via;h['_record_route_lines']=routes
    length=int(h.get('content-length','0'))
    if not 0<=length<=MAX_SDP:raise ValueError('invalid SIP body length')
    body=(await asyncio.wait_for(reader.readexactly(length),timeout=6)).decode('latin-1') if length else ''
    if is_response:
        h['_response_code']=parts[1]
        return 'SIP_RESPONSE',h,body
    return parts[0].upper(),h,body


class SecureMedia(asyncio.DatagramProtocol):
    def __init__(self, gateway):
        self.gateway=gateway
        self.transport=None
        self.accepted=0
        self.returned=0

    def connection_made(self, transport):
        self.transport=transport

    def datagram_received(self,data,peer):
        call=self.gateway.call
        if not call or not call.active:return
        if peer==call.remote_media:
            try:
                raw=call.inbound_srtp.unprotect(data)
                pkt=RTPPacket.parse(raw)
                if len(pkt.payload)!=160:return
            except (SrtpError,ValueError,IndexError):return
            self.accepted+=1
            self.transport.sendto(raw,('127.0.0.1',self.gateway.model_port))
        elif peer==('127.0.0.1',self.gateway.model_port):
            try:
                pkt=RTPPacket.parse(data)
                if len(pkt.payload)!=160:return
                encrypted=call.outbound_srtp.protect(data)
            except (SrtpError,ValueError,IndexError):return
            self.returned+=1
            self.transport.sendto(encrypted,call.remote_media)


class SipsGateway:
    def __init__(self,args):
        self.opts=args
        self.call=None
        self.model=None
        self.model_port=None
        self.media=None
        self.active_ip_ranges=[ipaddress.ip_network(x,strict=False) for x in args.allow_source]
        self.lock=asyncio.Lock()
        self.calls=0
        self.signal_writer=None
        self.signal_headers=None

    def allowed(self,address):
        addr=ipaddress.ip_address(address)
        return any(addr in cidr for cidr in self.active_ip_ranges)

    async def _finish_call(self,call):
        """Release model/media exactly once and make room for the next call."""
        if self.call is not call:return
        call.active=False
        self.call=None
        self.signal_writer=None
        self.signal_headers=None
        self.model.opts.on_media_idle=None
        await self.model.reset_call()

    async def _send_dialog_bye(self,call,writer):
        """Emit an in-dialog BYE over the existing trusted SIP/TLS leg."""
        if self.call is not call or not call.active or call.bye_sent:
            return False
        h=self.signal_headers or {}
        local=h.get('to','')
        if ';tag=' not in local.lower():local+=';tag='+call.to_tag
        remote=h.get('from','')
        target=h.get('contact') or remote
        match=re.search(r'<(sips?:[^>]+)>',target,re.I)
        if not match:match=re.search(r'(sips?:[^\s>]+)',target,re.I)
        if not match:
            LOG.warning('SIP BYE has no carrier Contact/From URI')
            return False
        uri=match.group(1)
        if any(ch in uri for ch in ('\r','\n')) or len(uri)>512:
            LOG.warning('SIP BYE has an invalid target URI')
            return False
        host=getattr(self.opts,'contact_host',None) or self.opts.advertise_ip
        port=str(getattr(self.opts,'sip_port',5061))
        packet=[
            'BYE '+uri+' SIP/2.0',
            'Via: SIP/2.0/TLS '+host+':'+port+';branch=z9hG4bK'+secrets.token_hex(8),
            'Max-Forwards: 70',
            *['Route: '+v for v in reversed(h.get('_record_route_lines',[]))],
            'From: '+local,
            'To: '+remote,
            'Call-ID: '+call.ident,
            'CSeq: 1 BYE',
            'Content-Length: 0',
        ]
        call.bye_sent=True
        try:
            writer.write(('\r\n'.join(packet)+'\r\n\r\n').encode('latin-1'))
            await writer.drain()
            LOG.info('Carrier BYE sent; contact_present=%s',bool(h.get('contact')))
            return True
        except (OSError,ConnectionError,UnicodeError) as exc:
            LOG.warning('SIP BYE failed (%s)',type(exc).__name__)
            return False

    async def _terminate_locally(self,call,writer):
        if self.call is not call or not call.active:return
        sent=await self._send_dialog_bye(call,writer)
        if sent:
            # Read loop remains available to observe carrier response.
            await asyncio.sleep(.5)
        async with self.lock:
            if self.call is call:await self._finish_call(call)
        writer.close()

    async def _on_inbound_media_idle(self,call,writer):
        if self.call is call and call.active:
            LOG.info('Inbound RTP expired; terminating SIP dialog')
            await self._terminate_locally(call,writer)

    async def handle_tls(self,reader,writer):
        peer=writer.get_extra_info('peername')
        if not peer or not self.allowed(peer[0]):
            writer.close()
            try:await writer.wait_closed()
            except (OSError,ConnectionError):pass
            return
        try:
            while not reader.at_eof():
                try:
                    method,h,sdp=await receive_sip(reader)
                except asyncio.TimeoutError:
                    if self.call and self.signal_writer is writer and self.call.active:
                        continue
                    break
                except (asyncio.IncompleteReadError,ConnectionError,ValueError,UnicodeError):
                    break
                if method=='SIP_RESPONSE':
                    if h.get('cseq','').strip().upper().endswith(' BYE'):
                        LOG.info('Carrier BYE response status=%s',h.get('_response_code','unknown'))
                    continue
                status='200 OK';answer='';tag='';cleanup=None
                async with self.lock:
                    if method=='OPTIONS':pass
                    elif method=='INVITE':
                        if self.call:
                            status='486 Busy Here'
                        elif not h.get('call-id') or not h.get('via') or not h.get('cseq'):
                            status='400 Bad Request'
                        else:
                            try:
                                media,remote_key=parse_sdp(sdp,self.opts.bind=='127.0.0.1')
                                local_key=secrets.token_bytes(30)
                                local_tag=secrets.token_hex(8)
                                rx=Session(policy=Policy(key=remote_key,ssrc_type=Policy.SSRC_ANY_INBOUND))
                                tx=Session(policy=Policy(key=local_key,ssrc_type=Policy.SSRC_ANY_OUTBOUND))
                                self.call=Call(h['call-id'],media,h.get('from',''),h.get('to',''),h.get('via',''),local_tag,rx,tx)
                                self.signal_writer=writer
                                self.signal_headers=dict(h)
                                if self.media is not None:
                                    self.media.accepted=0;self.media.returned=0
                                key64=base64.b64encode(local_key).decode('ascii')
                                answer=(
                                    'v=0\r\no=phonedex 1 1 IN IP4 '+self.opts.advertise_ip+'\r\n'
                                    's=Phonedex Voice\r\nc=IN IP4 '+self.opts.advertise_ip+'\r\nt=0 0\r\n'
                                    'm=audio '+str(self.opts.media_port)+' RTP/SAVP 0\r\n'
                                    'a=rtpmap:0 PCMU/8000\r\na=sendrecv\r\n'
                                    'a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:'+key64+'\r\n'
                                )
                                tag=local_tag
                                self.calls+=1
                                LOG.info('Encrypted SIP call accepted (count=%s)',self.calls)
                            except Exception as exc:
                                LOG.warning('SIP media negotiation failed (%s)',type(exc).__name__)
                                status='488 Not Acceptable Here'
                    elif method=='ACK':
                        continue
                    elif method in ('BYE','CANCEL'):
                        if self.call and self.call.ident==h.get('call-id'):
                            tag=self.call.to_tag
                            cleanup=self.call
                            # Stop relaying RTP immediately; send 200 before
                            # any slow WebRTC/model teardown.
                            cleanup.active=False
                        else:
                            status='481 Call/Transaction Does Not Exist'
                    else:
                        status='405 Method Not Allowed'
                contact=''
                if method=='INVITE' and status=='200 OK':
                    host=getattr(self.opts,'contact_host',None) or self.opts.advertise_ip
                    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host):
                        raise ValueError('Invalid SIP Contact hostname')
                    contact='sip:phonedex@'+host+':'+str(self.opts.sip_port)+';transport=tls'
                writer.write(response_bytes(status,h,answer,tag,contact))
                await writer.drain()
                if cleanup is not None:
                    async with self.lock:
                        if self.call is cleanup:await self._finish_call(cleanup)
                elif method=='INVITE' and status=='200 OK':
                    if self.call and self.signal_writer is writer:
                        call=self.call
                        self.model.opts.on_media_idle=lambda: self._on_inbound_media_idle(call,writer)
                        self.model.activate_inbound(self.opts.media_port)
        except (OSError,ConnectionError) as exc:
            LOG.warning('SIP signaling transport closed (%s)',type(exc).__name__)
        finally:
            async with self.lock:
                if self.call and self.signal_writer is writer:
                    await self._finish_call(self.call)
            writer.close()
            try:await writer.wait_closed()
            except (OSError,ConnectionError):pass


async def run(args):
    gateway=SipsGateway(args)
    model_opts=argparse.Namespace(backend=args.backend,prompt=Path(args.prompt_file).read_text(encoding='utf-8'),idle_seconds=45,codex_exe='codex',model_wake_file=getattr(args,'model_wake_file',None))
    loop=asyncio.get_running_loop()
    model=RTPBridge(model_opts)
    model_socket,_=await loop.create_datagram_endpoint(lambda:model,local_addr=('127.0.0.1',0))
    gateway.model=model
    gateway.model_port=model_socket.get_extra_info('sockname')[1]
    media_socket,media_protocol=await loop.create_datagram_endpoint(lambda:SecureMedia(gateway),local_addr=(args.bind,args.media_port))
    gateway.media=media_protocol
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert,args.key)
    server=await asyncio.start_server(gateway.handle_tls,host=args.bind,port=args.sip_port,ssl=context)
    LOG.info('SIP/TLS at %s:%d and SDES-SRTP media %s:%d; model=%s',args.bind,args.sip_port,args.bind,args.media_port,args.backend)
    try:
        async with server:await server.serve_forever()
    finally:
        media_socket.close()
        await model.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--cert',required=True)
    p.add_argument('--key',required=True)
    p.add_argument('--bind',default='127.0.0.1')
    p.add_argument('--sip-port',type=int,default=5061)
    p.add_argument('--media-port',type=int,default=41000)
    p.add_argument('--advertise-ip',default='127.0.0.1')
    p.add_argument('--contact-host',default=None)
    p.add_argument('--allow-source',action='append',default=[])
    p.add_argument('--enable-public',action='store_true')
    p.add_argument('--backend',choices=('echo','subscription'),default='echo')
    p.add_argument('--model-wake-file',default=None)
    p.add_argument('--prompt-file',default='business-prompt.txt')
    args=p.parse_args()
    if args.bind!='127.0.0.1':
        if not args.enable_public or not args.allow_source:
            p.error('public mode requires --enable-public and explicit provider --allow-source CIDRs')
        if args.advertise_ip=='127.0.0.1':p.error('public mode requires public --advertise-ip')
        if '0.0.0.0/0' in args.allow_source:p.error('public mode cannot allow arbitrary internet SIP signaling')
    elif not args.allow_source:args.allow_source=['127.0.0.1/32']
    if not (1024<=args.sip_port<=65535 and 1024<=args.media_port<=65535):p.error('invalid SIP/media port')
    logging.basicConfig(level=logging.INFO)
    try:asyncio.run(run(args))
    except KeyboardInterrupt:pass

if __name__=='__main__':main()
