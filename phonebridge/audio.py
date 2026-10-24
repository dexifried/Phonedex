"""PCMU / RTP helpers for local telephony audio tests (Python standard library)."""
from __future__ import annotations
from dataclasses import dataclass
import math
import struct

SAMPLES_PER_PACKET = 160

def pcmu_decode(value: int) -> int:
    u = (~value) & 255
    magnitude = ((((u & 15) << 3) + 132) << ((u >> 4) & 7)) - 132
    return -magnitude if u & 128 else magnitude

def pcmu_encode(value: int) -> int:
    value = max(-32768, min(32767, int(value)))
    sign = 0x80 if value < 0 else 0
    magnitude = min(32635, abs(value)) + 132
    seg = min(7, max(0, magnitude.bit_length() - 8))
    return (~(sign | (seg << 4) | ((magnitude >> (seg + 3)) & 15))) & 255

def ulaw_to_pcm16(data: bytes) -> bytes:
    return b''.join(struct.pack('<h', pcmu_decode(v)) for v in data)

def pcm16_to_ulaw(data: bytes) -> bytes:
    if len(data) % 2: raise ValueError('Odd PCM16 length')
    return bytes(pcmu_encode(v[0]) for v in struct.iter_unpack('<h', data))

def resample_pcm16(data: bytes, source: int, target: int) -> bytes:
    if len(data) % 2: raise ValueError('Odd PCM16 length')
    if source < 1 or target < 1: raise ValueError('Invalid sample rate')
    if source == target: return data
    samples = [s[0] for s in struct.iter_unpack('<h', data)]
    if not samples: return b''
    output = bytearray(round(len(samples) * target / source) * 2)
    for i in range(len(output) // 2):
        pos = i * source / target
        if source > target and source % target == 0:
            k = source // target
            subset = samples[int(pos):int(pos) + k]
            value = sum(subset) / len(subset)
        else:
            lo = min(int(pos), len(samples) - 1)
            hi = min(lo + 1, len(samples) - 1)
            value = samples[lo] * (1 - pos + lo) + samples[hi] * (pos - lo)
        struct.pack_into('<h', output, i * 2, max(-32768, min(32767, round(value))))
    return bytes(output)

def rms_pcm16(data: bytes) -> float:
    squares = [v[0] ** 2 for v in struct.iter_unpack('<h', data)]
    return math.sqrt(sum(squares) / len(squares)) if squares else 0

@dataclass(frozen=True)
class RTPPacket:
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes

    def pack(self) -> bytes:
        return struct.pack('!BBHII', 0x80, 0, self.sequence & 0xffff,
                           self.timestamp & 0xffffffff, self.ssrc & 0xffffffff) + self.payload

    @staticmethod
    def parse(data: bytes) -> 'RTPPacket':
        if len(data) < 12 or data[0] >> 6 != 2: raise ValueError('Invalid RTP header')
        offset = 12 + 4 * (data[0] & 15)
        if len(data) < offset: raise ValueError('Truncated CSRC')
        if data[0] & 16:
            if len(data) < offset + 4: raise ValueError('Truncated RTP extension')
            offset += 4 + 4 * struct.unpack_from('!H', data, offset + 2)[0]
            if len(data) < offset: raise ValueError('Truncated RTP extension body')
        end = len(data)
        if data[0] & 32:
            pad = data[-1]
            if not pad or end - pad < offset: raise ValueError('Bad RTP padding')
            end -= pad
        if data[1] & 127 != 0: raise ValueError('RTP payload type must be PCMU=0')
        seq, timestamp, ssrc = struct.unpack_from('!HII', data, 2)
        return RTPPacket(seq, timestamp, ssrc, data[offset:end])
