#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机械异响监测系统 · 协议一致性检查工具

用途：
  1) 跑协议测试向量（C/JS 解析器的共同标准）
  2) 连真实设备或回放日志，检查 seq、时间戳、计数不变量、事件完整性

用法（在 host_software/ 目录下执行）：
  python tools/conformance.py --vectors protocol/vectors.json
  python tools/conformance.py --source serial:COM5@115200 --seconds 60
  python tools/conformance.py --source file:records/session1.log

依赖：
  - 仅标准库即可跑 --vectors / file / stdin / udp
  - 串口模式需要 pyserial：pip install pyserial
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 协议常量与解析器（与桥接服务共用 lib/protocol.py）
# ---------------------------------------------------------------------------
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.protocol import PROTO_MAJOR, U32_MAX, Parsed, parse_line  # noqa: E402


# ---------------------------------------------------------------------------
# 测试向量
# ---------------------------------------------------------------------------
def run_vectors(path: str) -> int:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)

    cases = data.get("cases", [])
    if not cases:
        print("FAIL: 向量文件没有 cases")
        return 1

    fails = 0
    for case in cases:
        name = case.get("name", "?")
        parsed = parse_line(case.get("line", ""))
        problems: List[str] = []

        if parsed.ok != bool(case.get("ok")):
            problems.append(f"ok 期望 {case.get('ok')} 实际 {parsed.ok} ({parsed.err})")
        elif parsed.ok:
            if "kind" in case and parsed.kind != case["kind"]:
                problems.append(f"kind 期望 {case['kind']} 实际 {parsed.kind}")
            if "warn" in case and parsed.warn != case["warn"]:
                problems.append(f"warn 期望 {case['warn']} 实际 {parsed.warn}")
            for key, want in case.get("fields", {}).items():
                got = parsed.fields.get(key)
                if got != want:
                    problems.append(f"{key} 期望 {want} 实际 {got}")
        else:
            if parsed.err != case.get("err"):
                problems.append(f"err 期望 {case.get('err')} 实际 {parsed.err}")

        if problems:
            fails += 1
            print(f"FAIL  {name}: " + "; ".join(problems))
        else:
            print(f"PASS  {name}")

    print()
    print(f"向量总数 {len(cases)}，失败 {fails}")
    return 0 if fails == 0 else 1


# ---------------------------------------------------------------------------
# 在线检查
# ---------------------------------------------------------------------------
@dataclass
class Checker:
    allow_errors: int = 0

    seen_i: int = 0
    info: Optional[Parsed] = None
    counts: Counter = field(default_factory=Counter)
    last_seq: Dict[str, int] = field(default_factory=dict)
    lost: Counter = field(default_factory=Counter)
    seq_resets: int = 0
    parse_err: Counter = field(default_factory=Counter)

    last_t_us: Optional[int] = None
    max_t_us: Optional[int] = None
    first_t_us: Optional[int] = None
    backwards: int = 0
    reboots: int = 0
    t_intervals: List[int] = field(default_factory=list)

    last_counters: Optional[Tuple[int, int, int]] = None
    first_lock: Optional[int] = None
    counter_backwards: int = 0
    invariant_fail: int = 0

    e_count: int = 0
    final_t: Optional[Parsed] = None
    ovf_max: int = 0
    flags_last: int = 0

    def feed(self, parsed: Parsed) -> None:
        if not parsed.ok:
            self.parse_err[parsed.err] += 1
            return
        if parsed.kind == "skip":
            return

        self.counts[parsed.kind] += 1

        if parsed.kind == "I":
            self.seen_i += 1
            self.info = parsed
            return

        # 任意带 t_us 的帧都可以触发重启判定（E 可能先于重启后的首条 T 到达）
        t = parsed.fields.get("t_us")
        if t is not None:
            if self.max_t_us is not None and t < self.max_t_us - 1_000_000:
                self.reboots += 1
                self._reset_session()
            self.max_t_us = t if self.max_t_us is None else max(self.max_t_us, t)

        self._check_seq(parsed.kind, parsed.fields["seq"])

        if parsed.kind == "T":
            self._on_t(parsed)
        elif parsed.kind == "E":
            self.e_count += 1
        elif parsed.kind == "S":
            self.ovf_max = max(self.ovf_max, parsed.fields["ovf_cnt"])
            self.flags_last = parsed.fields["flags"]

    def _check_seq(self, kind: str, seq: int) -> None:
        if kind in self.last_seq:
            expected = (self.last_seq[kind] + 1) & U32_MAX
            if seq != expected:
                delta = (seq - expected) & U32_MAX
                if delta > 1000:
                    # 视为设备重启/日志截断，不计入丢帧
                    self.seq_resets += 1
                else:
                    self.lost[kind] += delta
        self.last_seq[kind] = seq

    def _reset_session(self) -> None:
        self.last_seq.clear()
        self.last_counters = None
        self.first_lock = None
        self.e_count = 0
        self.first_t_us = None
        self.last_t_us = None
        self.max_t_us = None
        self.t_intervals.clear()

    def _on_t(self, parsed: Parsed) -> None:
        f = parsed.fields
        t = f["t_us"]

        if self.last_t_us is not None:
            if t < self.last_t_us:
                self.backwards += 1
            else:
                self.t_intervals.append(t - self.last_t_us)

        self.last_t_us = t
        if self.first_t_us is None:
            self.first_t_us = t

        counters = (f["lock"], f["det"], f["supp"])
        if self.first_lock is None:
            self.first_lock = f["lock"]

        if self.last_counters is not None:
            if any(counters[i] < self.last_counters[i] for i in range(3)):
                self.counter_backwards += 1
        self.last_counters = counters

        if f["det"] != (f["lock"] + f["supp"]) & U32_MAX:
            self.invariant_fail += 1

        self.final_t = parsed

    # ---- 报告 -------------------------------------------------------------
    def rate_hz(self) -> Optional[float]:
        if not self.t_intervals:
            return None
        mean = sum(self.t_intervals) / len(self.t_intervals)
        if mean <= 0:
            return None
        return 1_000_000.0 / mean

    def report(self, wall_elapsed: float) -> int:
        rows: List[Tuple[str, bool, str]] = []

        def add(name: str, ok: bool, detail: str) -> None:
            rows.append((name, ok, detail))

        # 版本
        if self.info:
            major = self.info.fields["major"]
            minor = self.info.fields["minor"]
            dev = self.info.fields["dev_id"]
            fw = self.info.fields["fw"]
            add(
                "I 帧 / 版本",
                major == PROTO_MAJOR,
                f"{dev} fw{fw} 协议 {major}.{minor}（支持 {PROTO_MAJOR}.x）",
            )
        else:
            add("I 帧 / 版本", False, "未收到 I 帧")

        # 帧率
        rate = self.rate_hz()
        if rate is None:
            add("T 帧率", False, "没有有效的 T 帧间隔")
        else:
            add("T 帧率", 5.0 <= rate <= 60.0, f"{rate:.2f} Hz（按 t_us 计算）")

        # seq
        lost_total = sum(self.lost.values())
        lost_detail = ", ".join(f"{k}:{v}" for k, v in sorted(self.lost.items())) or "无"
        add("seq 连续性", lost_total == 0, f"丢失 {lost_total}（{lost_detail}），重置 {self.seq_resets}")

        # 时间戳
        add("t_us 单调", self.backwards == 0, f"倒退 {self.backwards} 次，重启 {self.reboots} 次")

        # 不变量
        add("det = lock + supp", self.invariant_fail == 0, f"违反 {self.invariant_fail} 次")
        add("计数单调", self.counter_backwards == 0, f"倒退 {self.counter_backwards} 次")

        # 溢出
        add("ovf_cnt = 0", self.ovf_max == 0, f"最大 {self.ovf_max}")

        # 事件完整性
        if self.final_t is None:
            add("E 帧完整性", False, "没有 T 帧")
        else:
            final_lock = self.final_t.fields["lock"]
            first_lock = self.first_lock or 0
            expected = final_lock - first_lock if final_lock >= first_lock else final_lock
            add(
                "E 帧完整性",
                self.e_count == expected,
                f"收到 {self.e_count} / 期望 {expected}（lock {first_lock}→{final_lock}）",
            )

        # 解析错误
        err_total = sum(self.parse_err.values())
        err_detail = ", ".join(f"{k}:{v}" for k, v in sorted(self.parse_err.items())) or "无"
        add("解析错误", err_total <= self.allow_errors, f"{err_total} 条（{err_detail}）")

        # 输出
        print("=" * 68)
        print("协议一致性检查")
        if self.info:
            print(
                f"设备 {self.info.fields['dev_id']}  "
                f"固件 {self.info.fields['fw']}  "
                f"协议 {self.info.fields['major']}.{self.info.fields['minor']}  "
                f"fs={self.info.fields['fs_hz']}  win={self.info.fields['win']}"
            )
        print(f"采样时长（墙钟） {wall_elapsed:.2f} s")
        if self.first_t_us is not None and self.last_t_us is not None:
            print(f"设备时间跨度     {(self.last_t_us - self.first_t_us) / 1e6:.2f} s")
        print("-" * 68)
        print(f"{'检查项':<20}{'结果':<8}说明")
        for name, ok, detail in rows:
            print(f"{name:<20}{'PASS' if ok else 'FAIL':<8}{detail}")
        print("-" * 68)

        passed = all(ok for _, ok, _ in rows)
        print(f"帧统计: " + ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items())))
        print(f"结论: {'PASS' if passed else 'FAIL'}")
        print("=" * 68)
        return 0 if passed else 1


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------
def iter_source(source: str, seconds: float) -> Iterator[bytes]:
    if source.startswith("serial:"):
        try:
            import serial  # type: ignore
        except ImportError:
            raise SystemExit("串口模式需要 pyserial：pip install pyserial")

        spec = source[len("serial:"):]
        port, _, baud_s = spec.partition("@")
        baud = int(baud_s or "115200")
        ser = serial.Serial(port, baud, timeout=0.2)
        deadline = time.monotonic() + seconds
        buf = b""
        try:
            while time.monotonic() < deadline:
                chunk = ser.read(512)
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    yield line
        finally:
            ser.close()

    elif source.startswith("udp:"):
        spec = source[len("udp:"):]
        host, _, port_s = spec.rpartition(":")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host or "0.0.0.0", int(port_s or "9000")))
        sock.settimeout(0.2)
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                for line in data.split(b"\n"):
                    if line:
                        yield line
        finally:
            sock.close()

    elif source.startswith("file:"):
        path = source[len("file:"):]
        with open(path, "rb") as fh:
            for line in fh:
                yield line

    elif source == "stdin":
        for line in sys.stdin.buffer:
            yield line

    else:
        raise SystemExit(
            "未知数据源。支持：serial:COM5@115200 / udp:0.0.0.0:9000 / "
            "file:path / stdin"
        )


def run_live(source: str, seconds: float, allow_errors: int, verbose: bool) -> int:
    checker = Checker(allow_errors=allow_errors)
    start = time.monotonic()
    try:
        for raw in iter_source(source, seconds):
            parsed = parse_line(raw)
            checker.feed(parsed)
            if verbose:
                if parsed.ok:
                    print(f"OK   {parsed.kind:<2} {parsed.fields}")
                elif parsed.kind != "skip":
                    print(f"ERR  {parsed.err:<18} {parsed.raw!r}")
    except KeyboardInterrupt:
        pass

    elapsed = time.monotonic() - start
    return checker.report(elapsed)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="机械异响监测系统协议一致性检查")
    parser.add_argument("--vectors", help="跑测试向量 JSON")
    parser.add_argument("--source", help="serial:COM5@115200 / udp:0.0.0.0:9000 / file:path / stdin")
    parser.add_argument("--seconds", type=float, default=60.0, help="采集时长，默认 60 s")
    parser.add_argument("--allow-errors", type=int, default=0, help="允许的解析错误条数")
    parser.add_argument("--verbose", action="store_true", help="逐帧打印")
    args = parser.parse_args(argv)

    if args.vectors:
        return run_vectors(args.vectors)

    source = args.source or "stdin"
    return run_live(source, args.seconds, args.allow_errors, args.verbose)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
