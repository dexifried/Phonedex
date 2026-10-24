"""Experimental Codex app-server live audio backend, without Hermes.

Uses the public Codex app-server JSON-RPC interface. The realtime calls here are
experimental and not warranted for commercial customer-facing deployment.
"""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
import shutil
from typing import Callable

LOG = logging.getLogger('codex-live')

class CodexLive:
    def __init__(self, deliver_pcm: Callable[[bytes, int], None],
                 prompt: str, executable: str = 'codex'):
        self.deliver_pcm = deliver_pcm
        self.prompt = prompt
        self.executable = executable
        self.proc = None
        self.thread_id = None
        self._id = 0
        self._pending = {}
        self._tasks = []
        self._started = asyncio.Event()
        self._write_lock = asyncio.Lock()

    async def _rpc(self, method: str, params: dict, timeout: int = 20):
        if self.proc is None or self.proc.stdin is None:
            raise RuntimeError('Codex app-server is not running')
        self._id += 1
        request_id = self._id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def _send(self, obj: dict):
        data = json.dumps(obj, separators=(',', ':')).encode() + b'\n'
        async with self._write_lock:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()

    async def _stdout(self):
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                request_id = msg.get('id')
                if isinstance(request_id, int):
                    future = self._pending.get(request_id)
                    if future and not future.done():
                        if msg.get('error'):
                            future.set_exception(RuntimeError(str(msg['error'].get('message', 'RPC error'))))
                        else:
                            future.set_result(msg.get('result') or {})
                    continue
                method = msg.get('method')
                params = msg.get('params') or {}
                if method == 'thread/realtime/started':
                    self._started.set()
                elif method == 'thread/realtime/outputAudio/delta':
                    audio = params.get('audio') or {}
                    try:
                        samples = base64.b64decode(audio['data'], validate=True)
                        rate = int(audio.get('sampleRate', 24000))
                        if int(audio.get('numChannels', 1)) == 1 and len(samples) % 2 == 0:
                            self.deliver_pcm(samples, rate)
                    except (ValueError, KeyError) as exc:
                        LOG.warning('Invalid audio event: %s', exc)
                elif method == 'thread/realtime/error':
                    LOG.error('Live error: %s', params.get('message', 'unknown'))
                elif method == 'thread/realtime/closed':
                    LOG.warning('Live session closed')
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Codex app-server disconnected'))

    async def _stderr(self):
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            LOG.debug('Codex: %s', line.decode(errors='replace').strip()[:200])

    async def start(self):
        executable = shutil.which(self.executable) or (
            self.executable if os.path.isfile(self.executable) else None)
        if executable is None:
            raise RuntimeError('Codex executable missing; supply --codex-exe')
        self.proc = await asyncio.create_subprocess_exec(
            executable, 'app-server', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._tasks = [asyncio.create_task(self._stdout()), asyncio.create_task(self._stderr())]
        try:
            await self._rpc('initialize', {
                'clientInfo': {'name':'ai-phone-bridge', 'title':'AI Phone Bridge', 'version':'0.1.0'},
                'capabilities': {'experimentalApi':True}
            })
            await self._send({'method':'initialized'})
            result = await self._rpc('thread/start', {
                'ephemeral':True, 'approvalPolicy':'never', 'sandbox':'read-only'
            })
            self.thread_id = result['thread']['id']
            await self._rpc('thread/realtime/start', {
                'threadId':self.thread_id,
                'model':'gpt-live-1-codex',
                'version':'v3',
                'outputModality':'audio',
                'transport':{'type':'websocket'},
                'includeStartupContext':False,
                'clientManagedHandoffs':True,
                'prompt':self.prompt
            })
            await asyncio.wait_for(self._started.wait(), timeout=20)
            LOG.info('Codex live session established')
        except Exception:
            await self.close()
            raise

    async def push(self, pcm24: bytes):
        if not self._started.is_set() or self.thread_id is None:
            return
        await self._rpc('thread/realtime/appendAudio', {
            'threadId':self.thread_id,
            'audio':{
                'data':base64.b64encode(pcm24).decode(),
                'sampleRate':24000,
                'numChannels':1,
                'samplesPerChannel':len(pcm24)//2
            }
        }, timeout=10)

    async def close(self):
        if self.proc is not None:
            if self.proc.returncode is None and self.thread_id:
                try:
                    await self._rpc('thread/realtime/stop', {'threadId':self.thread_id}, timeout=2)
                except Exception:
                    pass
            if self.proc.returncode is None:
                self.proc.terminate()
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=3)
                except asyncio.TimeoutError:
                    self.proc.kill()
                    await self.proc.wait()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started.clear()
        self.proc = None
        self.thread_id = None
