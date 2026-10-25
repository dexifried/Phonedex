"""Offline SIP teardown regressions. No phone calls, provider traffic or credentials."""
import asyncio
import base64
import secrets
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from bridge import RTPBridge
from sip_tls_gateway import SipsGateway, receive_sip
from sip_outbound import watch_remote_disconnect


class FakeWriter:
    def __init__(self):
        self.buffers=[]
        self.closed=False
    def get_extra_info(self,key):
        return ('127.0.0.1',32000) if key=='peername' else None
    def write(self,data):
        self.buffers.append(data)
    async def drain(self):
        await asyncio.sleep(0)
    def close(self):
        self.closed=True
    async def wait_closed(self):
        await asyncio.sleep(0)


def packet(method,ident,body=b''):
    rows=[
        method+' sip:phonedex@localhost SIP/2.0',
        'Via: SIP/2.0/TLS 127.0.0.1;branch=z9hG4bK-test',
        'From: <sip:caller@localhost>;tag=remote',
        'To: <sip:phonedex@localhost>',
        'Contact: <sip:caller@127.0.0.1:5061;transport=tls>',
        'Call-ID: '+ident,
        'CSeq: 1 '+method,
        'Content-Length: '+str(len(body))
    ]
    return ('\r\n'.join(rows)+'\r\n\r\n').encode()+body


def offer():
    key=base64.b64encode(secrets.token_bytes(30)).decode()
    return ('v=0\r\nc=IN IP4 127.0.0.1\r\nm=audio 49997 RTP/SAVP 0\r\n'
            'a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:'+key+'\r\n').encode()


def gateway(slow=False):
    g=SipsGateway(SimpleNamespace(
        allow_source=['127.0.0.1/32'],bind='127.0.0.1',
        advertise_ip='127.0.0.1',contact_host='localhost',
        sip_port=5061,media_port=49999))
    async def reset():
        if slow:await asyncio.sleep(.30)
    g.model=SimpleNamespace(
        opts=SimpleNamespace(on_media_idle=None),
        activate_inbound=Mock(),reset_call=AsyncMock(side_effect=reset))
    g.model_port=41000
    return g


class SIPTeardownTests(unittest.IsolatedAsyncioTestCase):
    async def start_call(self, g, ident):
        reader=asyncio.StreamReader();writer=FakeWriter()
        job=asyncio.create_task(g.handle_tls(reader,writer))
        reader.feed_data(packet('INVITE',ident,offer()))
        await asyncio.sleep(.04)
        self.assertIsNotNone(g.call)
        self.assertIn(b'Contact: <sip:phonedex@localhost:5061;transport=tls>',
                      b''.join(writer.buffers))
        g.model.activate_inbound.assert_called_once_with(49999)
        return reader,writer,job

    async def test_model_starts_on_sip_answer_and_tls_drop_frees_call(self):
        g=gateway()
        reader,writer,job=await self.start_call(g,'sip_drop')
        reader.feed_eof()
        await asyncio.wait_for(job,2)
        self.assertIsNone(g.call)
        self.assertIsNone(g.model.opts.on_media_idle)
        g.model.reset_call.assert_awaited()

    async def test_remote_bye_is_responded_before_slow_model_reset(self):
        g=gateway(slow=True)
        reader,writer,job=await self.start_call(g,'sip_bye')
        initial=len(writer.buffers)
        reader.feed_data(packet('BYE','sip_bye'))
        await asyncio.sleep(.05)
        self.assertIn(b'SIP/2.0 200 OK',b''.join(writer.buffers[initial:]))
        self.assertFalse(job.done())  # SIP response arrived before slow reset completed
        reader.feed_eof()
        await asyncio.wait_for(job,3)
        self.assertIsNone(g.call)

    async def test_model_media_idle_emits_dialog_bye_once(self):
        g=gateway()
        reader,writer,job=await self.start_call(g,'sip_idle')
        idle=g.model.opts.on_media_idle
        idle_task=asyncio.create_task(idle())
        await asyncio.sleep(.05)
        data=b''.join(writer.buffers)
        self.assertEqual(data.count(b'BYE sip:'),1)
        self.assertIn(b'From: <sip:phonedex@localhost>;tag=',data)
        self.assertIn(b'To: <sip:caller@localhost>;tag=remote',data)
        # Carrier can reject an already-closed dialog: still release resources.
        reader.feed_data(
            b'SIP/2.0 481 Call/Transaction Does Not Exist\r\n'
            b'Call-ID: sip_idle\r\nCSeq: 1 BYE\r\nContent-Length: 0\r\n\r\n')
        await asyncio.wait_for(idle_task,2)
        self.assertTrue(writer.closed)
        self.assertIsNone(g.call)
        reader.feed_eof()
        await asyncio.wait_for(job,2)

    async def test_second_call_not_busy_after_bye(self):
        g=gateway()
        reader,writer,job=await self.start_call(g,'sip_first')
        reader.feed_data(packet('BYE','sip_first'))
        await asyncio.sleep(.08)
        self.assertIsNone(g.call)
        reader.feed_data(packet('INVITE','sip_second',offer()))
        await asyncio.sleep(.08)
        self.assertIsNotNone(g.call)
        self.assertEqual(g.call.ident,'sip_second')
        reader.feed_eof()
        await asyncio.wait_for(job,2)

    async def test_duplicate_invite_retransmission_does_not_restart_model(self):
        g=gateway()
        reader,writer,job=await self.start_call(g,'same_dialog')
        initial=[x for x in writer.buffers if b'SIP/2.0 200 OK' in x][0]
        reader.feed_data(packet('INVITE','same_dialog',offer()))
        await asyncio.sleep(.06)
        replies=[x for x in writer.buffers if b'SIP/2.0 200 OK' in x]
        self.assertEqual(len(replies),2)
        self.assertEqual(replies[0],initial)
        self.assertEqual(replies[1],initial)
        g.model.activate_inbound.assert_called_once()
        reader.feed_eof()
        await asyncio.wait_for(job,2)

    async def test_rapid_hangup_cooldown_does_not_start_model(self):
        g=gateway()
        # The test carrier supplies a syntactically valid E.164 caller identity.
        def from_caller(message):
            return message.replace(b'sip:caller@localhost',b'sip:+12025550123@localhost')
        for n in range(3):
            reader=asyncio.StreamReader();writer=FakeWriter()
            task=asyncio.create_task(g.handle_tls(reader,writer))
            ident='rapid_'+str(n)
            reader.feed_data(from_caller(packet('INVITE',ident,offer())))
            await asyncio.sleep(.05)
            self.assertIsNotNone(g.call)
            reader.feed_data(from_caller(packet('BYE',ident)))
            await asyncio.sleep(.06)
            reader.feed_eof()
            await asyncio.wait_for(task,2)
            self.assertIsNone(g.call)
        prior_calls=g.calls
        reader=asyncio.StreamReader();writer=FakeWriter()
        task=asyncio.create_task(g.handle_tls(reader,writer))
        reader.feed_data(from_caller(packet('INVITE','rapid_blocked',offer())))
        await asyncio.sleep(.06)
        response=b''.join(writer.buffers)
        self.assertIn(b'480 Temporarily Unavailable',response)
        self.assertIn(b'Retry-After:',response)
        self.assertEqual(g.calls,prior_calls)
        self.assertIsNone(g.call)
        reader.feed_eof()
        await asyncio.wait_for(task,2)

    async def test_completed_dialog_invite_replay_does_not_restart_model(self):
        g=gateway()
        reader,writer,task=await self.start_call(g,'done_dialog')
        reader.feed_data(packet('BYE','done_dialog'))
        await asyncio.sleep(.06)
        self.assertIsNone(g.call)
        count=g.calls
        reader.feed_data(packet('INVITE','done_dialog',offer()))
        await asyncio.sleep(.06)
        self.assertIn(b'481 Call/Transaction Does Not Exist',b''.join(writer.buffers))
        self.assertEqual(g.calls,count)
        reader.feed_eof()
        await asyncio.wait_for(task,2)

    async def test_bridge_audio_idle_notifies_once(self):
        calls=[]
        def callback():
            calls.append(time.monotonic())
        bridge=RTPBridge(SimpleNamespace(idle_seconds=.01,on_media_idle=callback))
        bridge.peer=('127.0.0.1',49999)
        bridge.last_input=time.monotonic()-1
        task=asyncio.create_task(bridge.idle())
        try:
            await asyncio.sleep(1.10)
            self.assertIsNone(bridge.peer)
            self.assertEqual(len(calls),1)
        finally:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


class OutboundTeardownTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_bye_ends_outbound_call_with_ack(self):
        reader=asyncio.StreamReader();writer=FakeWriter()
        reader.feed_data(packet('BYE','outbound_test'))
        result=await asyncio.wait_for(watch_remote_disconnect(reader,writer),1)
        self.assertEqual(result,'remote_bye')
        self.assertIn(b'SIP/2.0 200 OK',b''.join(writer.buffers))
        self.assertIn(b'Call-Id: outbound_test',b''.join(writer.buffers))

    async def test_outbound_tls_eof_ends_call(self):
        reader=asyncio.StreamReader()
        reader.feed_eof()
        result=await asyncio.wait_for(watch_remote_disconnect(reader,FakeWriter()),1)
        self.assertEqual(result,'transport_closed')

    async def test_unrelated_sip_response_does_not_end_outbound_call(self):
        reader=asyncio.StreamReader();writer=FakeWriter()
        reader.feed_data(b'SIP/2.0 100 Trying\r\nCSeq: 1 INVITE\r\nContent-Length: 0\r\n\r\n')
        reader.feed_data(packet('BYE','outbound_after_response'))
        result=await asyncio.wait_for(watch_remote_disconnect(reader,writer),1)
        self.assertEqual(result,'remote_bye')


if __name__=='__main__':
    unittest.main()
