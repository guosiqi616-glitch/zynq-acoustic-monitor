#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ws.py — 极简 WebSocket 服务端（标准库，无第三方依赖）

只实现演示需要的能力：
  - RFC6455 握手
  - 服务端发送文本帧（不掩码）
  - 接收客户端文本/关闭/ ping 帧（处理掩码）
  - 回 pong、回 close
不支持扩展、压缩、二进制业务帧。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from typing import Any, Callable, Dict, Optional

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1_000_000

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.closed = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 握手
    # ------------------------------------------------------------------
    @classmethod
    async def accept(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: Dict[str, str],
    ) -> "WebSocket":
        key = headers.get("sec-websocket-key", "")
        if not key:
            raise ValueError("missing Sec-WebSocket-Key")
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode("ascii")).digest()
        ).decode("ascii")
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        return cls(reader, writer)

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header += struct.pack("!H", n)
        else:
            header.append(127)
            header += struct.pack("!Q", n)

        async with self._lock:
            self.writer.write(bytes(header) + payload)
            await self.writer.drain()

    async def send_text(self, text: str) -> None:
        if self.closed:
            return
        await self._send_frame(OP_TEXT, text.encode("utf-8"))

    async def send_json(self, obj: Any) -> None:
        await self.send_text(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        )

    # ------------------------------------------------------------------
    # 接收
    # ------------------------------------------------------------------
    async def _read_frame(self) -> tuple[bool, int, bytes]:
        head = await self.reader.readexactly(2)
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F

        if length == 126:
            length = struct.unpack("!H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await self.reader.readexactly(8))[0]

        if length > MAX_FRAME:
            raise ValueError("frame too large")

        mask = await self.reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
        payload = await self.reader.readexactly(length) if length else b""
        if masked:
            payload = bytes(
                byte ^ mask[i % 4] for i, byte in enumerate(payload)
            )
        return fin, opcode, payload

    async def recv_loop(
        self, on_text: Optional[Callable[[str], None]] = None
    ) -> None:
        frag_opcode: Optional[int] = None
        frag_data = bytearray()

        try:
            while not self.closed:
                fin, opcode, payload = await self._read_frame()

                if opcode == OP_CLOSE:
                    break
                if opcode == OP_PING:
                    await self._send_frame(OP_PONG, payload)
                    continue
                if opcode == OP_PONG:
                    continue

                if opcode in (OP_TEXT, OP_BINARY):
                    if fin:
                        self._dispatch(opcode, payload, on_text)
                    else:
                        frag_opcode = opcode
                        frag_data = bytearray(payload)
                elif opcode == OP_CONT and frag_opcode is not None:
                    frag_data.extend(payload)
                    if fin:
                        self._dispatch(frag_opcode, bytes(frag_data), on_text)
                        frag_opcode = None
                        frag_data = bytearray()
        except (asyncio.IncompleteReadError, ConnectionError, OSError, ValueError):
            pass
        finally:
            await self.close()

    @staticmethod
    def _dispatch(
        opcode: int, payload: bytes, on_text: Optional[Callable[[str], None]]
    ) -> None:
        if opcode != OP_TEXT or on_text is None:
            return
        try:
            on_text(payload.decode("utf-8", "replace"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            await self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except (ConnectionError, OSError, RuntimeError):
            pass
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError, RuntimeError):
            pass
