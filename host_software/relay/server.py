#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — 桥接服务

  TCP/串口 ──► 解析 ──► Hub ──► WebSocket / HTTP
                              └─► JSONL 录制

用法（在 host_software/ 目录下）：
  python relay/server.py
  python relay/server.py --source tcp:127.0.0.1:9100 --port 8080
  python relay/server.py --source serial:COM5@115200 --port 8080
  python relay/server.py --source replay:records/sim60.log --port 8080

零第三方依赖；串口模式需要 pyserial。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from relay.hub import Client, Hub  # noqa: E402
from relay.recorder import Recorder, default_path  # noqa: E402
from relay.sources import build_source  # noqa: E402
from relay.ws import WebSocket  # noqa: E402

MAX_HEADER = 64 * 1024


class RelayServer:
    def __init__(self, hub: Hub, web_dir: str, sim_control: Optional[str] = None):
        self.hub = hub
        self.web_dir = os.path.abspath(web_dir)
        self.record_dir = os.path.join(ROOT, "records")
        self.sim_control = sim_control

    # ------------------------------------------------------------------
    # 连接入口
    # ------------------------------------------------------------------
    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            method, target, headers, body = await self._read_request(reader)
            path = urlsplit(target).path
            if path == "/ws" and "websocket" in headers.get("upgrade", "").lower():
                await self._handle_ws(reader, writer, headers)
            else:
                await self._handle_http(writer, method, target, headers, body)
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            OSError,
            ValueError,
        ):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    @staticmethod
    async def _read_request(
        reader: asyncio.StreamReader,
    ) -> Tuple[str, str, Dict[str, str], bytes]:
        data = await reader.readuntil(b"\r\n\r\n")
        if len(data) > MAX_HEADER:
            raise ValueError("header too large")
        head = data.decode("latin-1")
        lines = head.split("\r\n")
        if not lines or not lines[0]:
            raise ValueError("empty request")
        first = lines[0].split(" ")
        if len(first) < 2:
            raise ValueError("bad request line")
        method, target = first[0], first[1]

        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length", "0") or "0")
        body = await reader.readexactly(length) if length > 0 else b""
        return method, target, headers, body

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    async def _handle_http(
        self,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> None:
        split = urlsplit(target)
        path = split.path
        query = parse_qs(split.query)

        if method == "GET" and path in ("/", "/index.html"):
            await self._serve_file(writer, os.path.join(self.web_dir, "index.html"))
            return

        # 首页“进入实时监测”按钮指向 /dashboard.html，这里给它加路由
        if method == "GET" and path in ("/dashboard.html", "/dashboard"):
            await self._serve_file(writer, os.path.join(self.web_dir, "dashboard.html"))
            return

        if method == "GET" and path.startswith("/static/"):
            rel = path[len("/static/"):]
            await self._serve_file(writer, os.path.join(self.web_dir, rel))
            return

        if method == "GET" and path == "/api/state":
            await self._json(writer, self.hub.api_state())
            return

        if method == "GET" and path == "/api/health":
            await self._json(writer, self.hub.status_message())
            return

        if method == "GET" and path == "/api/events":
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError:
                limit = 50
            await self._json(writer, self.hub.events_list(limit))
            return

        if method == "POST" and path == "/api/control":
            await self._control(writer, body)
            return

        if method == "POST" and path == "/api/record/start":
            await self._record_start(writer, body)
            return

        if method == "POST" and path == "/api/record/stop":
            await self._record_stop(writer)
            return

        if method == "GET" and path == "/favicon.ico":
            await self._respond(writer, "204 No Content", b"", "text/plain")
            return

        await self._respond(
            writer,
            "404 Not Found",
            b"not found",
            "text/plain; charset=utf-8",
        )

    async def _serve_file(self, writer: asyncio.StreamWriter, path: str) -> None:
        base = os.path.realpath(self.web_dir)
        target = os.path.realpath(path)
        if not (target == base or target.startswith(base + os.sep)):
            await self._respond(writer, "403 Forbidden", b"forbidden", "text/plain")
            return
        if not os.path.isfile(target):
            await self._respond(writer, "404 Not Found", b"not found", "text/plain")
            return
        with open(target, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        await self._respond(writer, "200 OK", data, ctype)

    async def _json(self, writer: asyncio.StreamWriter, obj: Any) -> None:
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await self._respond(writer, "200 OK", payload, "application/json; charset=utf-8")

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter,
        status: str,
        body: bytes,
        ctype: str,
    ) -> None:
        head = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-store\r\n"
            "\r\n"
        ).encode("latin-1")
        writer.write(head + body)
        await writer.drain()

    async def _control(self, writer: asyncio.StreamWriter, body: bytes) -> None:
        if not self.sim_control:
            await self._json(writer, {"ok": False, "error": "未配置 --sim-control"})
            return
        key = ""
        try:
            data = json.loads(body.decode("utf-8"))
            key = str(data.get("key", "")).lower()
        except (ValueError, UnicodeDecodeError):
            key = ""
        if key not in set("dbxvplsq"):
            await self._json(writer, {"ok": False, "error": "非法控制键"})
            return
        spec = self.sim_control
        if spec.startswith("tcp:"):
            spec = spec[4:]
        host, _, port_s = spec.rpartition(":")
        try:
            _, cw = await asyncio.open_connection(host or "127.0.0.1", int(port_s or "9101"))
            cw.write(key.encode("ascii"))
            await cw.drain()
            cw.close()
            await cw.wait_closed()
        except OSError as exc:
            await self._json(writer, {"ok": False, "error": f"控制端口不可用: {exc}"})
            return
        await self._json(writer, {"ok": True, "key": key})

    async def _record_start(
        self, writer: asyncio.StreamWriter, body: bytes
    ) -> None:
        path = ""
        if body:
            try:
                data = json.loads(body.decode("utf-8"))
                path = str(data.get("path", "") or "")
            except (ValueError, UnicodeDecodeError):
                path = ""
        if not path:
            path = default_path(self.record_dir)

        if self.hub.recorder is not None:
            self.hub.recorder.close()
        try:
            self.hub.recorder = Recorder(path)
        except OSError as exc:
            await self._json(writer, {"ok": False, "error": str(exc)})
            return
        await self._json(writer, {"ok": True, "path": path})

    async def _record_stop(self, writer: asyncio.StreamWriter) -> None:
        path = ""
        if self.hub.recorder is not None:
            path = self.hub.recorder.path
            self.hub.recorder.close()
            self.hub.recorder = None
        await self._json(writer, {"ok": True, "path": path})

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------
    async def _handle_ws(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: Dict[str, str],
    ) -> None:
        ws = await WebSocket.accept(reader, writer, headers)
        client = self.hub.register(ws)
        await ws.send_json(self.hub.hello_message())
        sender = asyncio.create_task(self._sender(client))

        def on_text(text: str) -> None:
            try:
                data = json.loads(text)
            except ValueError:
                return
            if isinstance(data, dict) and data.get("type") == "ping":
                asyncio.create_task(
                    ws.send_json({"type": "pong", "t": data.get("t")})
                )

        try:
            await ws.recv_loop(on_text)
        finally:
            sender.cancel()
            self.hub.unregister(client)
            await ws.close()

    async def _sender(self, client: Client) -> None:
        ws = client.ws
        while not ws.closed:
            if client.slow:
                await ws.close()
                return

            msg: Optional[Dict[str, Any]] = None
            for queue in (client.events, client.telemetry, client.status):
                try:
                    msg = queue.get_nowait()
                    break
                except asyncio.QueueEmpty:
                    continue

            if msg is None:
                await asyncio.sleep(0.005)
                continue

            try:
                await asyncio.wait_for(ws.send_json(msg), timeout=2.0)
            except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError):
                await ws.close()
                return


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------
async def _ingest(source, hub: Hub) -> None:
    try:
        async for line in source.lines():
            hub.on_line(line, time.perf_counter_ns())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 源异常不能拖垮 HTTP 服务
        print(f"[source] fatal: {exc}", file=sys.stderr)


async def _run(args: argparse.Namespace) -> int:
    hub = Hub()

    mode = str(args.mode).upper()
    if mode == "AUTO":
        if args.source.startswith("serial:"):
            mode = "BOARD"
        elif args.source.startswith(("replay:", "file:")):
            mode = "REPLAY"
        else:
            mode = "SIM"
    hub.mode = mode

    if args.record:
        hub.recorder = Recorder(args.record)

    def on_status(connected: bool, detail: str) -> None:
        hub.set_source(connected, detail)
        if not args.quiet:
            state = "UP  " if connected else "DOWN"
            print(f"[source] {state} {detail}", file=sys.stderr)

    source = build_source(args.source, on_status)
    server = RelayServer(hub, args.web, args.sim_control)

    hub_task = asyncio.create_task(hub.broadcast_loop(args.hz))
    ingest_task = asyncio.create_task(_ingest(source, hub))

    srv = await asyncio.start_server(server.handle_client, args.host, args.port)
    addr = ", ".join(str(sock.getsockname()) for sock in srv.sockets or [])
    if not args.quiet:
        print(
            f"bridge listening on http://{args.host}:{args.port}  "
            f"source={args.source}  ({addr})",
            file=sys.stderr,
        )

    try:
        async with srv:
            await srv.serve_forever()
    finally:
        ingest_task.cancel()
        hub_task.cancel()
        for task in (ingest_task, hub_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if hub.recorder is not None:
            hub.recorder.close()
        print("bridge stopped", file=sys.stderr)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="机械异响监测系统桥接服务")
    parser.add_argument("--source", default="tcp:127.0.0.1:9100",
                        help="tcp:host:port / serial:COM5@115200 / udp:host:port / replay:path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hz", type=float, default=25.0, help="WebSocket 遥测广播频率")
    parser.add_argument("--web", default=os.path.join(ROOT, "web"))
    parser.add_argument("--record", default=None, help="启动时录制到指定 JSONL")
    parser.add_argument("--mode", default="auto", choices=["auto", "sim", "board", "replay"],
                        help="数据源徽章模式；默认按 source 自动判断")
    parser.add_argument("--sim-control", default=None,
                        help="模拟器控制端口，如 tcp:127.0.0.1:9101，供页面 L/P 键使用")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 0


def run_forever(argv: Optional[list[str]] = None) -> int:
    """带自恢复的主循环：崩溃后自动重启，适合演示现场长时间运行。

    Windows 上浏览器异常断开（WinError 10054）有时会让 asyncio 的
    proactor 在回调里抛异常并结束进程；这里兜一层，自动拉起。

    端口被占用（EADDRINUSE）时退避 5 秒再试，避免刷屏。
    """
    attempt = 0
    while True:
        try:
            rc = main(argv)
            if rc == 0:
                return 0
            raise SystemExit(rc)
        except KeyboardInterrupt:
            return 0
        except BaseException as exc:  # noqa: BLE001 - 演示场景要兜住一切
            attempt += 1
            text = repr(exc)
            busy = "10048" in text or "10013" in text or "Address already in use" in text
            if busy:
                print(f"[supervisor] 端口 8080 被占用（可能还有旧进程在跑），"
                      f"5 秒后重试（第 {attempt} 次）", file=sys.stderr, flush=True)
                time.sleep(5.0)
            else:
                print(f"[supervisor] 服务异常退出（第 {attempt} 次）：{text}，2 秒后重启",
                      file=sys.stderr, flush=True)
                time.sleep(2.0)


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # 带 --no-supervisor 时只跑一次，便于调试
    if "--no-supervisor" in sys.argv:
        sys.argv.remove("--no-supervisor")
        sys.exit(main())
    sys.exit(run_forever())
