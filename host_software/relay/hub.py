#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hub.py — 状态中枢

职责：
  - 解析后的帧更新“最新状态”
  - 维护事件环形队列（默认 200 条）
  - 每个浏览器客户端两条队列：遥测可覆盖、事件不丢
  - 20–25 Hz 合并广播，不逐帧转发
  - 会话/重启/丢帧/完整性对账
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.protocol import PROTO_MAJOR, PROTO_MINOR, Parsed, parse_line  # noqa: E402

U32_MASK = 0xFFFFFFFF


@dataclass
class DeviceState:
    connected: bool = False
    source: str = "未连接"
    t_us: int = 0
    t_pc_ns: int = 0
    temp: Optional[int] = None
    hum: Optional[int] = None
    db_low: Optional[int] = -1200
    db_high: Optional[int] = -1200
    ber: Optional[int] = 0
    state: int = 0
    lock: int = 0
    det: int = 0
    supp: int = 0
    seq: int = 0
    latency_ms: float = 0.0
    session: int = 0
    last_rx_wall: float = 0.0


@dataclass(eq=False)
class Client:
    ws: Any
    telemetry: "asyncio.Queue[Dict[str, Any]]"
    events: "asyncio.Queue[Dict[str, Any]]"
    status: "asyncio.Queue[Dict[str, Any]]"
    slow: bool = False


class Hub:
    def __init__(self, history: int = 200):
        self.state = DeviceState()
        self.clients: Set[Client] = set()
        self.events: Deque[Dict[str, Any]] = deque(maxlen=history)

        # X 帧（10 Hz 诊断帧）的最新快照，随 telemetry 一起下发
        self.diag: Dict[str, Any] = {}

        self.info: Optional[Dict[str, Any]] = None
        self.protocol_ok = True
        self.protocol_error = ""

        self.source_connected = False
        self.source_detail = "未连接"
        self.last_i_wall = 0.0
        self.session = 0

        self.last_seq: Dict[str, int] = {}
        self.lost: Counter = Counter()
        self.errors: Counter = Counter()
        self.dev_err = 0
        self.ovf = 0

        self.e_count = 0
        self.first_lock: Optional[int] = None
        self.last_t_us: Optional[int] = None
        self.max_t_us: Optional[int] = None
        self.invariant_ok = True

        self.arrivals: Deque[float] = deque(maxlen=400)
        self.offset_samples: Deque[int] = deque(maxlen=2000)
        self.offset_ns: Optional[int] = None

        self.recorder: Any = None
        self.mode = "SIM"
        self.rx = 0

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def set_source(self, connected: bool, detail: str) -> None:
        self.source_connected = connected
        self.source_detail = detail
        self.state.connected = connected
        self.state.source = detail
        self._broadcast_status()

    def register(self, ws: Any) -> Client:
        client = Client(
            ws=ws,
            telemetry=asyncio.Queue(maxsize=1),
            events=asyncio.Queue(maxsize=128),
            status=asyncio.Queue(maxsize=1),
        )
        self.clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self.clients.discard(client)

    # ------------------------------------------------------------------
    # 数据入口
    # ------------------------------------------------------------------
    def on_line(self, line: str, t_pc_ns: int) -> None:
        if self.recorder is not None:
            try:
                self.recorder.record(line, t_pc_ns)
            except OSError:
                pass

        parsed = parse_line(line)
        if not parsed.ok:
            self.errors[parsed.err or "E_UNKNOWN"] += 1
            return
        if parsed.kind == "skip":
            return

        self.state.last_rx_wall = time.monotonic()

        if parsed.kind == "I":
            self._on_i(parsed)
            return
        if not self.protocol_ok:
            self.errors["PROTO"] += 1
            return
        if parsed.kind == "T":
            self._on_t(parsed, t_pc_ns)
        elif parsed.kind == "E":
            self._on_e(parsed, t_pc_ns)
        elif parsed.kind == "X":
            self._on_x(parsed)
        elif parsed.kind == "S":
            self._on_s(parsed)

    def _on_i(self, parsed: Parsed) -> None:
        now = time.monotonic()
        if self.info is None:
            self._new_session("boot", notify=False)
        elif now - self.last_i_wall > 2.0:
            self._new_session("reconnect")

        self.last_i_wall = now
        self.info = parsed.fields
        major = int(self.info["major"])
        self.protocol_ok = major == PROTO_MAJOR
        if not self.protocol_ok:
            self.protocol_error = (
                f"设备协议 {major}.{self.info['minor']}，"
                f"上位机支持 {PROTO_MAJOR}.x"
            )
            self.errors["PROTO"] += 1
        else:
            self.protocol_error = ""
        self._broadcast_status()

    def _on_x(self, parsed: Parsed) -> None:
        """X 帧：多维判决的中间量，10 Hz，只更新快照，不触发广播。"""
        f = parsed.fields
        self.diag = {
            "level": f["level"],
            "score": f["score"],
            "ber": f["ber"],
            "ber_res": f["ber_res"],
            "kurt": f["kurt"],
            "thr": f["thr"],
            "mu": f["mu_ber"],
            "sd": f["sd_ber"],
            "nf_low": f["nf_low"],
            "nf_high": f["nf_high"],
            "flags": f["flags"],
            "ev_pl": f["ev_pl"],
            "ev_ps": f["ev_ps"],
        }

    def _on_t(self, parsed: Parsed, t_pc_ns: int) -> None:
        f = parsed.fields
        t = int(f["t_us"])

        if self.max_t_us is not None and t < self.max_t_us - 1_000_000:
            self._new_session("reboot")

        if self.last_t_us is not None and t < self.last_t_us:
            self.errors["TIME"] += 1
        self.last_t_us = t
        if self.max_t_us is None or t > self.max_t_us:
            self.max_t_us = t

        self._check_seq("T", int(f["seq"]))

        # 时钟偏移：设备 t_us 是微秒，PC 是纳秒，先统一到 ns
        t_ns = t * 1000
        diff = t_pc_ns - t_ns
        self.offset_samples.append(diff)
        if self.offset_ns is None or diff < self.offset_ns:
            self.offset_ns = diff
        latency = (
            (t_pc_ns - (t_ns + self.offset_ns)) / 1e6
            if self.offset_ns is not None
            else 0.0
        )

        self.state.t_us = t
        self.state.t_pc_ns = t_pc_ns
        self.state.temp = None if int(f["temp"]) == -32768 else int(f["temp"])
        self.state.hum = None if int(f["hum"]) == 65535 else int(f["hum"])
        self.state.db_low = None if int(f["db_low"]) == -32768 else int(f["db_low"])
        self.state.db_high = None if int(f["db_high"]) == -32768 else int(f["db_high"])
        self.state.ber = None if int(f["ber"]) == 65535 else int(f["ber"])
        raw_state = int(f["state"])
        self.state.state = raw_state if raw_state in (0, 1, 2) else 0
        self.state.lock = int(f["lock"])
        self.state.det = int(f["det"])
        self.state.supp = int(f["supp"])
        self.state.seq = int(f["seq"])
        self.state.latency_ms = latency

        if self.first_lock is None:
            self.first_lock = self.state.lock

        if self.state.det != (self.state.lock + self.state.supp) & U32_MASK:
            self.invariant_ok = False

        self.rx += 1
        self.arrivals.append(time.monotonic())
        self._purge_arrivals()

    def _on_e(self, parsed: Parsed, t_pc_ns: int) -> None:
        f = parsed.fields
        t = int(f["t_us"])

        if self.max_t_us is not None and t < self.max_t_us - 1_000_000:
            self._new_session("reboot")

        self._check_seq("E", int(f["seq"]))
        self.e_count += 1
        if int(f["lock"]) > self.state.lock:
            self.state.lock = int(f["lock"])

        event = {
            "type": "event",
            "seq": int(f["seq"]),
            "t_us": t,
            "t_pc_ns": t_pc_ns,
            "lock": int(f["lock"]),
            "reason": int(f["reason"]),
            "db_low": int(f["db_low"]),
            "db_high": int(f["db_high"]),
            "ber": int(f["ber"]),
            "thr": int(f["thr"]),
        }
        self.events.append(event)
        self._broadcast_event(event)

    def _on_s(self, parsed: Parsed) -> None:
        f = parsed.fields
        self.ovf = max(self.ovf, int(f["ovf_cnt"]))
        self.dev_err = max(self.dev_err, int(f["err_cnt"]))

    # ------------------------------------------------------------------
    # 会话 / 对账
    # ------------------------------------------------------------------
    def _new_session(self, reason: str, notify: bool = True) -> None:
        self.session += 1
        self.state.session = self.session
        self.last_seq.clear()
        self.lost.clear()
        self.e_count = 0
        self.first_lock = None
        self.last_t_us = None
        self.max_t_us = None
        self.offset_samples.clear()
        self.offset_ns = None
        self.invariant_ok = True
        self.ovf = 0
        self.rx = 0
        self.state.lock = 0
        self.state.det = 0
        self.state.supp = 0

        if notify:
            self._broadcast_event({
                "type": "session",
                "id": self.session,
                "reason": reason,
            })
            self._broadcast_status()

    def _check_seq(self, kind: str, seq: int) -> None:
        if kind in self.last_seq:
            expected = (self.last_seq[kind] + 1) & U32_MASK
            if seq != expected:
                delta = (seq - expected) & U32_MASK
                if delta > 1000:
                    self.errors["SEQ_RESET"] += 1
                else:
                    self.lost[kind] += delta
        self.last_seq[kind] = seq

    def _purge_arrivals(self) -> None:
        cutoff = time.monotonic() - 2.0
        while self.arrivals and self.arrivals[0] < cutoff:
            self.arrivals.popleft()

    def fps(self) -> float:
        self._purge_arrivals()
        if len(self.arrivals) < 2:
            return 0.0
        span = self.arrivals[-1] - self.arrivals[0]
        if span <= 0:
            return 0.0
        return (len(self.arrivals) - 1) / span

    # ------------------------------------------------------------------
    # 消息
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "t_us": self.state.t_us,
            "t_pc_ns": self.state.t_pc_ns,
            "temp": self.state.temp,
            "hum": self.state.hum,
            "db_low": self.state.db_low,
            "db_high": self.state.db_high,
            "ber": self.state.ber,
            "state": self.state.state,
            "lock": self.state.lock,
            "det": self.state.det,
            "supp": self.state.supp,
            "latency_ms": round(self.state.latency_ms, 2),
            "session": self.state.session,
            "connected": self.source_connected,
        }

    def telemetry_message(self) -> Dict[str, Any]:
        msg = {"type": "telemetry"}
        msg.update(self.snapshot())
        msg["diag"] = self.diag
        return msg

    def status_message(self) -> Dict[str, Any]:
        now = time.monotonic()
        age = (now - self.state.last_rx_wall) * 1000.0 if self.state.last_rx_wall else None
        return {
            "type": "status",
            "source": self.source_detail,
            "connected": self.source_connected,
            "mode": self.mode,
            "rx": self.rx,
            "fps": round(self.fps(), 1),
            "lost": sum(self.lost.values()),
            "lost_detail": dict(self.lost),
            "errors": sum(self.errors.values()),
            "error_detail": dict(self.errors),
            "dev_err": self.dev_err,
            "ovf": self.ovf,
            # 只统计“连接/会话开始之后”的锁存，避免把连接前的历史算成缺失
            "e_missing": max(
                0,
                (self.state.lock - (self.first_lock if self.first_lock is not None else 0))
                - self.e_count,
            ),
            "invariant_ok": self.invariant_ok,
            "session": self.session,
            "protocol_ok": self.protocol_ok,
            "protocol_error": self.protocol_error,
            "age_ms": None if age is None else round(age, 0),
        }

    def hello_message(self) -> Dict[str, Any]:
        return {
            "type": "hello",
            "protocol": f"{PROTO_MAJOR}.{PROTO_MINOR}",
            "scale": {"temp": 0.01, "hum": 0.01, "db": 0.1, "ber": 0.001},
            "state": self.snapshot(),
            "status": self.status_message(),
            "events": list(self.events)[-20:],
        }

    def api_state(self) -> Dict[str, Any]:
        return {
            "state": self.telemetry_message(),
            "status": self.status_message(),
            "events": list(self.events)[-20:],
        }

    def events_list(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 200))
        return list(self.events)[-limit:]

    # ------------------------------------------------------------------
    # 广播
    # ------------------------------------------------------------------
    def _put_latest(self, q: "asyncio.Queue[Dict[str, Any]]", item: Dict[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def _broadcast_event(self, msg: Dict[str, Any]) -> None:
        for client in list(self.clients):
            try:
                client.events.put_nowait(msg)
            except asyncio.QueueFull:
                client.slow = True

    def _broadcast_status(self) -> None:
        msg = self.status_message()
        for client in list(self.clients):
            self._put_latest(client.status, msg)

    async def broadcast_loop(self, hz: float = 25.0) -> None:
        interval = 1.0 / max(1.0, hz)
        next_tick = time.monotonic()
        next_status = next_tick + 1.0
        while True:
            next_tick += interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                next_tick = time.monotonic()

            tel = self.telemetry_message()
            for client in list(self.clients):
                self._put_latest(client.telemetry, tel)

            now = time.monotonic()
            if now >= next_status:
                status = self.status_message()
                for client in list(self.clients):
                    self._put_latest(client.status, status)
                next_status = now + 1.0
