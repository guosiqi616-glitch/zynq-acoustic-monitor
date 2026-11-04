#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources.py — 数据源

支持：
  tcp:127.0.0.1:9100      连接模拟器/设备 TCP
  serial:COM5@115200      串口（只需 pyserial，不依赖 pyserial-asyncio）
  udp:0.0.0.0:9000        UDP，一行一包
  replay:records/x.jsonl  回放录制文件（按 t_us 配速）
  file:records/x.log      回放原始日志（按 t_us 配速）

所有源都实现 async def lines() -> AsyncIterator[str]。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import AsyncIterator, Callable, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.protocol import parse_line  # noqa: E402

StatusFn = Callable[[bool, str], None]


def _noop(connected: bool, detail: str) -> None:
    pass


class Source:
    def __init__(self, on_status: Optional[StatusFn] = None):
        self.on_status: StatusFn = on_status or _noop

    async def lines(self) -> AsyncIterator[str]:
        raise NotImplementedError
        yield ""  # pragma: no cover


class TcpSource(Source):
    def __init__(self, host: str, port: int, on_status: Optional[StatusFn] = None):
        super().__init__(on_status)
        self.host = host
        self.port = port

    async def lines(self) -> AsyncIterator[str]:
        backoff = 0.5
        while True:
            writer = None
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as exc:
                self.on_status(False, f"connect {self.host}:{self.port}: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 5.0)
                continue

            backoff = 0.5
            self.on_status(True, f"tcp://{self.host}:{self.port}")
            buf = b""
            try:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        yield raw.decode("ascii", errors="replace").rstrip("\r")
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
                self.on_status(False, f"read: {exc}")
            finally:
                if writer is not None:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except (ConnectionError, OSError):
                        pass

            self.on_status(False, "disconnected")
            await asyncio.sleep(backoff)


class SerialSource(Source):
    def __init__(self, port: str, baud: int, on_status: Optional[StatusFn] = None):
        super().__init__(on_status)
        self.port = port
        self.baud = baud

    async def lines(self) -> AsyncIterator[str]:
        try:
            import serial  # type: ignore
        except ImportError:
            raise SystemExit("串口源需要 pyserial：pip install pyserial")

        while True:
            ser = None
            try:
                ser = serial.Serial(self.port, self.baud, timeout=0.1)
            except Exception as exc:  # 串口不存在/被占用
                self.on_status(False, f"serial {self.port}: {exc}")
                await asyncio.sleep(1.0)
                continue

            self.on_status(True, f"serial://{self.port}@{self.baud}")
            buf = b""
            first_line = True
            try:
                while True:
                    # 阻塞读放到线程池，事件循环继续服务 HTTP/WS
                    # 有多少读多少：原来固定 read(512) 会一直等到凑满 512 字节
                    # （50 帧/秒 × ~55 字节 ≈ 180 ms）才返回，等于每帧被延迟最多 180 ms。
                    _n = ser.in_waiting
                    chunk = await asyncio.to_thread(ser.read, _n if _n > 0 else 1)
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        # 刚打开串口时可能从半行中间接入，丢掉第一行，
                        # 避免把半行当成 E_UNKNOWN_FRAME。
                        if first_line:
                            first_line = False
                            continue
                        yield raw.decode("ascii", errors="replace").rstrip("\r")
            except (ConnectionError, OSError) as exc:
                self.on_status(False, f"serial read: {exc}")
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except (ConnectionError, OSError):
                        pass

            self.on_status(False, "serial disconnected")
            await asyncio.sleep(1.0)


class UdpSource(Source):
    def __init__(self, host: str, port: int, on_status: Optional[StatusFn] = None):
        super().__init__(on_status)
        self.host = host
        self.port = port

    async def lines(self) -> AsyncIterator[str]:
        queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        loop = asyncio.get_running_loop()

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr) -> None:
                queue.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(
            _Proto, local_addr=(self.host, self.port)
        )
        self.on_status(True, f"udp://{self.host}:{self.port}")
        try:
            while True:
                data = await queue.get()
                for raw in data.split(b"\n"):
                    if raw:
                        yield raw.decode("ascii", errors="replace").rstrip("\r")
        finally:
            transport.close()
            self.on_status(False, "udp closed")


class ReplaySource(Source):
    def __init__(self, path: str, speed: float = 1.0, loop: bool = False,
                 on_status: Optional[StatusFn] = None):
        super().__init__(on_status)
        self.path = path
        self.speed = speed
        self.loop = loop

    async def lines(self) -> AsyncIterator[str]:
        self.on_status(True, f"replay://{self.path}")
        try:
            while True:
                last_us: Optional[int] = None
                with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                    for raw in fh:
                        raw = raw.strip()
                        if not raw:
                            continue
                        line = raw
                        t_us: Optional[int] = None
                        if raw.startswith("{"):
                            try:
                                rec = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            line = str(rec.get("line", ""))
                            t_us = rec.get("t_us")
                        if t_us is None:
                            parsed = parse_line(line)
                            if parsed.ok:
                                t_us = parsed.fields.get("t_us")
                        if (
                            self.speed > 0
                            and last_us is not None
                            and isinstance(t_us, int)
                            and t_us >= last_us
                        ):
                            delay = (t_us - last_us) / 1e6 / self.speed
                            if 0 < delay < 5.0:
                                await asyncio.sleep(delay)
                        if isinstance(t_us, int):
                            last_us = t_us
                        yield line
                if not self.loop:
                    break
                await asyncio.sleep(0.5)
        finally:
            self.on_status(False, "replay done")


def build_source(spec: str, on_status: Optional[StatusFn] = None) -> Source:
    """解析 --source 字符串。"""
    if spec.startswith("tcp:"):
        body = spec[4:]
        host, _, port_s = body.rpartition(":")
        return TcpSource(host or "127.0.0.1", int(port_s or "9100"), on_status)
    if spec.startswith("serial:"):
        body = spec[7:]
        port, _, baud_s = body.partition("@")
        return SerialSource(port, int(baud_s or "115200"), on_status)
    if spec.startswith("udp:"):
        body = spec[4:]
        host, _, port_s = body.rpartition(":")
        return UdpSource(host or "0.0.0.0", int(port_s or "9000"), on_status)
    if spec.startswith("replay:"):
        return ReplaySource(spec[7:], on_status=on_status)
    if spec.startswith("file:"):
        return ReplaySource(spec[5:], on_status=on_status)
    raise SystemExit(
        "未知 --source。支持：tcp:host:port / serial:COM5@115200 / "
        "udp:host:port / replay:path / file:path"
    )
