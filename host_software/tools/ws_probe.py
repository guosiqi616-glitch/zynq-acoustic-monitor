#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ws_probe.py — 无头 WebSocket 客户端，用来验证桥接服务

用法（在 host_software/ 目录下）：
  python tools/ws_probe.py --url ws://127.0.0.1:8080/ws --seconds 5
  python tools/ws_probe.py --url ws://127.0.0.1:8080/ws --seconds 10 --expect-events 1

只依赖标准库，和 relay/ws.py 的极简实现配对。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import time
from collections import Counter
from typing import Optional, Tuple
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _masked_frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    header = bytearray([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack("!H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", n)
    return bytes(header) + mask + masked


async def _read_frame(
    reader: asyncio.StreamReader,
) -> Tuple[bool, int, bytes]:
    head = await reader.readexactly(2)
    b1, b2 = head[0], head[1]
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F

    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]

    mask = await reader.readexactly(4) if masked else b"\x00\x00\x00\x00"
    payload = await reader.readexactly(length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return fin, opcode, payload


async def probe(url: str, seconds: float, expect_events: int) -> int:
    parts = urlsplit(url)
    if parts.scheme != "ws":
        print("只支持 ws:// URL")
        return 1
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/ws"

    reader, writer = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    writer.write(
        (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()

    header = await reader.readuntil(b"\r\n\r\n")
    if b"101" not in header.split(b"\r\n", 1)[0]:
        print("握手失败：", header.decode("latin-1", "replace"))
        writer.close()
        return 1

    counts: Counter = Counter()
    last = None
    events = 0
    start = time.monotonic()
    errors = 0

    try:
        while time.monotonic() - start < seconds:
            try:
                fin, opcode, payload = await asyncio.wait_for(
                    _read_frame(reader), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                break

            if opcode == 0x8:  # close
                break
            if opcode == 0x9:  # ping
                writer.write(_masked_frame(0xA, payload))
                await writer.drain()
                continue
            if opcode != 0x1:
                continue

            try:
                msg = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                errors += 1
                continue

            kind = msg.get("type", "?")
            counts[kind] += 1
            if kind == "telemetry":
                last = msg
            elif kind == "event":
                events += 1
            elif kind == "hello":
                print(
                    "hello: protocol={} scale={}".format(
                        msg.get("protocol"), msg.get("scale")
                    )
                )
            elif kind == "status" and not msg.get("protocol_ok", True):
                print("协议不兼容：", msg.get("protocol_error"))
    finally:
        try:
            writer.write(_masked_frame(0x8, struct.pack("!H", 1000)))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    elapsed = max(0.001, time.monotonic() - start)
    tel = counts.get("telemetry", 0)
    rate = tel / elapsed

    print("-" * 60)
    print("duration      : {:.1f} s".format(elapsed))
    print("hello         : {}".format(counts.get("hello", 0)))
    print("telemetry     : {}  ({:.1f}/s)".format(tel, rate))
    print("status        : {}".format(counts.get("status", 0)))
    print("events        : {}".format(events))
    print("sessions      : {}".format(counts.get("session", 0)))
    print("decode errors : {}".format(errors))
    if last:
        print(
            "last telemetry: BER={} lock={} det={} supp={} temp={} hum={} lat={}ms".format(
                last.get("ber"),
                last.get("lock"),
                last.get("det"),
                last.get("supp"),
                last.get("temp"),
                last.get("hum"),
                last.get("latency_ms"),
            )
        )

    problems = []
    if counts.get("hello", 0) < 1:
        problems.append("没有 hello")
    if tel < max(5, int(seconds * 10)):
        problems.append("telemetry 太少（{:.1f}/s）".format(rate))
    if counts.get("status", 0) < 1:
        problems.append("没有 status")
    if events < expect_events:
        problems.append("事件数 {} < 期望 {}".format(events, expect_events))
    if errors:
        problems.append("JSON 解码错误 {}".format(errors))

    print("-" * 60)
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print("PASS")
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="WebSocket 桥接服务探测")
    parser.add_argument("--url", default="ws://127.0.0.1:8080/ws")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--expect-events", type=int, default=0)
    args = parser.parse_args(argv)

    try:
        return asyncio.run(probe(args.url, args.seconds, args.expect_events))
    except (ConnectionError, OSError) as exc:
        print("连接失败：", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
