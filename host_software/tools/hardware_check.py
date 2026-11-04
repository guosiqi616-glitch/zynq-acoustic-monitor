#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hardware_check.py — 硬件回来的“10 分钟接入”自检工具

流程：
  1. 打开串口，等 I 帧；3 秒没有就打印排查清单（波特率、TX/RX、板子是否在跑）
  2. 打印设备信息，确认 fs / win / bands 与预期一致
  3. 跑 60 秒，检查 seq 连续、det = lock + supp、t_us 单调、T 帧率 ~20 Hz
  4. 输出 PASS / FAIL、具体失败点，以及桥接 + 网页 BOARD 徽章的启动步骤

用法（在 host_software/ 目录下执行）：
  D:\\Python\\python.exe tools/hardware_check.py --port COM5 --baud 115200
  D:\\Python\\python.exe tools/hardware_check.py --source serial:COM5@115200
  D:\\Python\\python.exe tools/hardware_check.py --source file:records/sim60.log
  D:\\Python\\python.exe tools/hardware_check.py --checklist
  D:\\Python\\python.exe tools/hardware_check.py --list-ports

注意：
  - 串口是独占的：运行本工具前先关闭 XCOM / 串口助手 / relay/server.py。
  - 本工具只读，不向板子写任何字节。
  - 文件模式用于没硬件时自测，不按墙钟等待 60 秒。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.protocol import PROTO_MAJOR, U32_MAX, Parsed, parse_line  # noqa: E402

U32_MASK = U32_MAX
REBOOT_GAP_US = 1_000_000
SEQ_JUMP_LIMIT = 1000


# ---------------------------------------------------------------------------
# 数据源：真实串口 / 文件自测
# ---------------------------------------------------------------------------
class LineSource:
    name = "?"

    def __init__(self) -> None:
        self.bytes_seen = 0
        self.dropped_bytes = 0
        self.first_bytes = b""

    def readline(self, deadline: float) -> Optional[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SerialLineSource(LineSource):
    """带半行缓冲、CR/LF 兼容和乱码上限的串口行读取器。"""

    def __init__(self, ser: object, port: str, baud: int) -> None:
        super().__init__()
        self.ser = ser
        self.name = f"{port} @ {baud} 8N1"
        self.buf = b""
        self.queue: Deque[bytes] = deque()

    def _pump(self) -> bool:
        try:
            data = self.ser.read(512)  # type: ignore[attr-defined]
        except Exception as exc:  # 串口被拔掉/驱动异常
            raise RuntimeError(f"串口读取失败: {exc}") from exc

        if not data:
            return False

        self.bytes_seen += len(data)
        if len(self.first_bytes) < 96:
            self.first_bytes += data[: 96 - len(self.first_bytes)]

        self.buf += data

        # 同时兼容 \r\n、\n、\r；先归一化再按 \n 切。
        self.buf = self.buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self.queue.append(line)

        # 没有换行且缓存过长，通常是波特率不对产生的二进制乱码。
        if len(self.buf) > 8192:
            self.dropped_bytes += len(self.buf)
            self.buf = b""
        return True

    def readline(self, deadline: float) -> Optional[bytes]:
        while True:
            if self.queue:
                return self.queue.popleft()

            now = time.monotonic()
            if now >= deadline:
                return None

            if not self._pump():
                time.sleep(min(0.01, max(0.0, deadline - now)))

    def close(self) -> None:
        try:
            self.ser.close()  # type: ignore[attr-defined]
        except Exception:
            pass


class FileLineSource(LineSource):
    """回放日志自测；splitlines 同时处理 CRLF / LF / CR。"""

    def __init__(self, path: str) -> None:
        super().__init__()
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        with open(path, "rb") as fh:
            data = fh.read()
        self.lines = data.splitlines()
        self.name = f"file:{path}"
        self.bytes_seen = len(data)
        self.first_bytes = data[:96]
        self.idx = 0

    def readline(self, deadline: float) -> Optional[bytes]:
        if self.idx >= len(self.lines):
            return None
        line = self.lines[self.idx]
        self.idx += 1
        return line


def open_serial(port: str, baud: int) -> SerialLineSource:
    try:
        import serial  # type: ignore
    except ImportError:
        print("FAIL: 串口模式需要 pyserial。")
        print("  D:\\Python\\python.exe -m pip install pyserial")
        raise SystemExit(2)

    print(f"打开串口 {port} @ {baud} 8N1 ...")
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
    except (serial.SerialException, ValueError, OSError) as exc:
        print(f"FAIL: 打开 {port} 失败: {exc}")
        print("  最常见原因：XCOM/串口助手或 relay/server.py 还占着这个 COM 口。")
        print_checklist()
        list_ports()
        raise SystemExit(2)

    return SerialLineSource(ser, port, baud)


def build_source(args: argparse.Namespace) -> Tuple[LineSource, bool]:
    """返回 (source, is_serial)。"""
    spec = (args.source or "").strip()

    if spec:
        if spec.startswith("serial:"):
            rest = spec[len("serial:"):]
            port, _, baud_s = rest.partition("@")
            if not port:
                print(f"FAIL: --source 格式不对: {spec}")
                print("  例：serial:COM5@115200")
                raise SystemExit(2)
            return open_serial(port, int(baud_s or str(args.baud))), True

        if spec.startswith("file:"):
            path = spec[len("file:"):]
            try:
                return FileLineSource(path), False
            except FileNotFoundError:
                print(f"FAIL: 文件不存在: {path}")
                raise SystemExit(2)

        print(f"FAIL: 未知 --source: {spec}")
        print("  支持 serial:COM5@115200 或 file:records/sim60.log")
        raise SystemExit(2)

    if args.port:
        return open_serial(args.port, args.baud), True

    print("FAIL: 没有指定串口。")
    print("  先运行 --list-ports 看 COM 号，再用 --port COM5。")
    list_ports()
    print_checklist()
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------
@dataclass
class Row:
    name: str
    ok: bool
    detail: str
    gate: bool = True


@dataclass
class Checker:
    expect_fs: int = 48000
    expect_win: int = 512
    expect_bands: int = 2
    allow_errors: int = 0
    rate_min: float = 18.0
    rate_max: float = 22.0

    info: Optional[Parsed] = None
    i_count: int = 0
    i_config_changes: int = 0
    config_examples: List[str] = field(default_factory=list)

    counts: Counter = field(default_factory=Counter)
    parse_errors: int = 0
    parse_examples: List[str] = field(default_factory=list)

    last_seq: Dict[str, int] = field(default_factory=dict)
    seq_lost: Counter = field(default_factory=Counter)
    seq_jumps: int = 0
    seq_examples: List[str] = field(default_factory=list)

    t_count: int = 0
    first_t_us: Optional[int] = None
    last_t_us: Optional[int] = None
    final_t_us: Optional[int] = None
    t_backwards: int = 0
    t_examples: List[str] = field(default_factory=list)
    reboots: int = 0
    reboot_examples: List[str] = field(default_factory=list)
    intervals: List[int] = field(default_factory=list)

    det_fail: int = 0
    det_examples: List[str] = field(default_factory=list)
    counter_backwards: int = 0
    last_counters: Optional[Tuple[int, int, int]] = None

    e_count: int = 0
    e_lock_last: Optional[int] = None
    e_lock_errors: int = 0
    e_examples: List[str] = field(default_factory=list)

    s_count: int = 0
    ovf_max: int = 0
    flags_last: int = 0

    def feed(self, parsed: Parsed) -> None:
        if not parsed.ok:
            self.parse_errors += 1
            if len(self.parse_examples) < 5:
                self.parse_examples.append(
                    f"{parsed.err}: {preview(parsed.raw)}"
                )
            return

        if parsed.kind == "skip":
            return

        self.counts[parsed.kind] += 1
        f = parsed.fields

        if parsed.kind == "I":
            self._on_i(parsed)
        elif parsed.kind == "T":
            self._on_t(parsed)
        elif parsed.kind == "E":
            self._on_e(parsed)
        elif parsed.kind == "S":
            self._on_s(parsed)

    # ---- I ---------------------------------------------------------------
    def _on_i(self, parsed: Parsed) -> None:
        self.i_count += 1
        f = parsed.fields
        if self.info is not None:
            old = self.info.fields
            for key in ("major", "minor", "fs_hz", "win", "bands"):
                if old.get(key) != f.get(key):
                    self.i_config_changes += 1
                    if len(self.config_examples) < 5:
                        self.config_examples.append(
                            f"{key}: {old.get(key)} -> {f.get(key)}"
                        )
        self.info = parsed

    # ---- T ---------------------------------------------------------------
    def _on_t(self, parsed: Parsed) -> None:
        f = parsed.fields
        seq = f["seq"]
        t = f["t_us"]
        self.t_count += 1
        self.final_t_us = t

        self._check_seq("T", seq, parsed)

        if self.first_t_us is None:
            self.first_t_us = t
        if self.last_t_us is not None:
            if t < self.last_t_us:
                self.t_backwards += 1
                if len(self.t_examples) < 5:
                    self.t_examples.append(
                        f"T seq={seq} t_us={t} < 上一条 {self.last_t_us}"
                    )
                if self.last_t_us - t > REBOOT_GAP_US:
                    self.reboots += 1
                    if len(self.reboot_examples) < 5:
                        self.reboot_examples.append(
                            f"t_us {self.last_t_us} -> {t} "
                            f"(倒退回退 {self.last_t_us - t} us)"
                        )
                    self._reset_after_reboot(t)
            else:
                delta = t - self.last_t_us
                if delta > 0:
                    self.intervals.append(delta)

        self.last_t_us = t

        lock = f["lock"]
        det = f["det"]
        supp = f["supp"]
        expect = (lock + supp) & U32_MASK
        if det != expect:
            self.det_fail += 1
            if len(self.det_examples) < 5:
                self.det_examples.append(
                    f"T seq={seq} t={t} lock={lock} det={det} supp={supp} "
                    f"(期望 {expect})"
                )

        counters = (lock, det, supp)
        if self.last_counters is not None:
            if any(counters[i] < self.last_counters[i] for i in range(3)):
                self.counter_backwards += 1
        self.last_counters = counters

    def _reset_after_reboot(self, t: int) -> None:
        # 重启后 seq 会归零；清除跟踪，避免把重启算成上千帧丢失。
        self.last_seq.clear()
        self.last_counters = None
        self.e_lock_last = None
        self.intervals.clear()
        self.first_t_us = t

    # ---- E / S -----------------------------------------------------------
    def _on_e(self, parsed: Parsed) -> None:
        f = parsed.fields
        self._check_seq("E", f["seq"], parsed)
        self.e_count += 1
        lock = f["lock"]
        if self.e_lock_last is not None:
            expect = (self.e_lock_last + 1) & U32_MASK
            if lock != expect:
                self.e_lock_errors += 1
                if len(self.e_examples) < 5:
                    self.e_examples.append(
                        f"E lock {self.e_lock_last} -> {lock} (期望 {expect})"
                    )
        self.e_lock_last = lock

    def _on_s(self, parsed: Parsed) -> None:
        f = parsed.fields
        self._check_seq("S", f["seq"], parsed)
        self.s_count += 1
        self.ovf_max = max(self.ovf_max, int(f["ovf_cnt"]))
        self.flags_last = int(f["flags"])

    # ---- seq -------------------------------------------------------------
    def _check_seq(self, kind: str, seq: int, parsed: Parsed) -> None:
        if kind in self.last_seq:
            expected = (self.last_seq[kind] + 1) & U32_MASK
            if seq != expected:
                delta = (seq - expected) & U32_MASK
                if delta > SEQ_JUMP_LIMIT:
                    self.seq_jumps += 1
                    if len(self.seq_examples) < 5:
                        self.seq_examples.append(
                            f"{kind} seq {self.last_seq[kind]} -> {seq} "
                            f"(跳变 {delta}，可能重启/截断)"
                        )
                else:
                    self.seq_lost[kind] += delta
                    if len(self.seq_examples) < 5:
                        self.seq_examples.append(
                            f"{kind} seq {self.last_seq[kind]} -> {seq} "
                            f"(丢失 {delta})"
                        )
        self.last_seq[kind] = seq

    # ---- 统计 -------------------------------------------------------------
    def rate_hz(self) -> Optional[float]:
        if not self.intervals:
            return None
        mean = sum(self.intervals) / len(self.intervals)
        if mean <= 0:
            return None
        return 1_000_000.0 / mean

    def interval_range(self) -> Tuple[Optional[int], Optional[int]]:
        if not self.intervals:
            return None, None
        return min(self.intervals), max(self.intervals)

    def lost_total(self) -> int:
        return sum(self.seq_lost.values())

    # ---- 报告 -------------------------------------------------------------
    def build_rows(self) -> List[Row]:
        rows: List[Row] = []

        def add(name: str, ok: bool, detail: str, gate: bool = True) -> None:
            rows.append(Row(name, ok, detail, gate))

        f = self.info.fields if self.info else {}
        major = f.get("major")
        minor = f.get("minor")
        fs = f.get("fs_hz")
        win = f.get("win")
        bands = f.get("bands")
        caps = f.get("caps")

        add("I 帧", self.info is not None, f"收到 {self.i_count} 条")
        add(
            "协议版本",
            major == PROTO_MAJOR,
            f"{major}.{minor}（要求 {PROTO_MAJOR}.x）",
        )
        add(
            "fs_hz",
            fs == self.expect_fs,
            f"{fs}（期望 {self.expect_fs}）",
        )
        add(
            "win",
            win == self.expect_win,
            f"{win}（期望 {self.expect_win}）",
        )
        add(
            "bands",
            bands == self.expect_bands,
            f"{bands}（期望 {self.expect_bands}）",
        )
        add(
            "I 帧配置稳定",
            self.i_config_changes == 0,
            "无变化" if self.i_config_changes == 0 else f"变化 {self.i_config_changes} 次",
        )

        add("T 帧数", self.t_count > 0, f"{self.t_count} 条")

        lost = self.lost_total()
        seq_ok = lost == 0 and self.seq_jumps == 0
        if seq_ok:
            seq_detail = "丢失 0，跳变 0"
        else:
            parts = [f"{k}:{v}" for k, v in sorted(self.seq_lost.items()) if v]
            seq_detail = f"丢失 {lost}（{', '.join(parts) or '无'}），跳变 {self.seq_jumps}"
        add("seq 连续性", seq_ok, seq_detail)

        add(
            "det = lock + supp",
            self.det_fail == 0,
            "0 次违反" if self.det_fail == 0 else f"违反 {self.det_fail} 次",
        )

        add(
            "t_us 单调",
            self.t_backwards == 0,
            "0 次倒退" if self.t_backwards == 0 else f"倒退 {self.t_backwards} 次",
        )

        rate = self.rate_hz()
        rate_ok = rate is not None and self.rate_min <= rate <= self.rate_max
        if rate is None:
            rate_detail = "没有有效的 T 帧间隔"
        else:
            lo, hi = self.interval_range()
            rate_detail = (
                f"{rate:.2f} Hz（设备时间，要求 {self.rate_min:.0f}~{self.rate_max:.0f}）"
                f"，间隔 {lo}~{hi} us"
            )
        add("T 帧率", rate_ok, rate_detail)

        parse_ok = self.parse_errors <= self.allow_errors
        add(
            "解析错误",
            parse_ok,
            f"{self.parse_errors} 条（允许 {self.allow_errors}）",
        )

        add(
            "重启",
            self.reboots == 0,
            "0 次" if self.reboots == 0 else f"{self.reboots} 次",
        )

        # E：有事件才检查锁存连续性；没有事件不算失败。
        if self.e_count > 0:
            add(
                "E 帧锁存连续",
                self.e_lock_errors == 0,
                f"{self.e_count} 条，锁存跳变 {self.e_lock_errors} 次",
            )
        else:
            add("E 帧", True, "无锁存事件", gate=False)

        # S：1 Hz 健康帧；没有 S 给出警告，有 S 则检查 FIFO 溢出。
        if self.s_count > 0:
            add(
                "S 帧 / FIFO 溢出",
                self.ovf_max == 0,
                f"{self.s_count} 条，ovf_cnt={self.ovf_max}，flags=0x{self.flags_last:04X}",
            )
        else:
            add("S 帧", False, "未收到 1 Hz 健康帧", gate=False)

        if caps is not None:
            pl_ts = bool(caps & 0x1)
            add(
                "时间戳 caps.bit0",
                pl_ts,
                "PL 硬件时间戳" if pl_ts else "软件时间戳（延迟测量会偏大）",
                gate=False,
            )

        return rows

    def report(self, source_label: str, elapsed: float, file_mode: bool) -> int:
        rows = self.build_rows()
        failed = [r for r in rows if r.gate and not r.ok]
        warnings = [r for r in rows if not r.gate and not r.ok]
        passed = not failed

        print()
        print("=" * 74)
        print("机械异响监测 | 硬件接入检查")
        print(f"数据源    {source_label}")
        print(f"采集时长  {elapsed:.2f} s（墙钟）")
        if self.first_t_us is not None and self.final_t_us is not None:
            print(f"设备跨度  {(self.final_t_us - self.first_t_us) / 1e6:.2f} s")
        print("-" * 74)
        print(f"{'检查项':<24}{'结果':<8}说明")

        for r in rows:
            if r.gate:
                status = "PASS" if r.ok else "FAIL"
            else:
                status = "PASS" if r.ok else "WARN"
            print(f"{r.name:<24}{status:<8}{r.detail}")

        print("-" * 74)
        print(
            "帧统计: "
            + ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items()))
            + f"; 解析错误={self.parse_errors}"
        )

        rate = self.rate_hz()
        if rate is not None and not file_mode and elapsed > 0.5:
            print(
                f"墙钟帧率: {self.t_count / elapsed:.2f} Hz"
                f"（设备时间 {rate:.2f} Hz，二者差值主要来自 USB/串口延迟）"
            )

        if warnings:
            print()
            print("警告（不参与 PASS/FAIL）:")
            for r in warnings:
                print(f"  - {r.name}: {r.detail}")

        if failed:
            print()
            print("失败明细:")
            for r in failed:
                print(f"  - {r.name}: {r.detail}")
            for line in self.failure_examples():
                print(f"      {line}")

        print("-" * 74)
        print(f"结论: {'PASS' if passed else 'FAIL'}")
        print("=" * 74)
        return 0 if passed else 1

    def failure_examples(self) -> List[str]:
        out: List[str] = []
        if self.i_config_changes:
            out.extend(self.config_examples)
        if self.seq_lost or self.seq_jumps:
            out.extend(self.seq_examples)
        if self.det_fail:
            out.extend(self.det_examples)
        if self.t_backwards:
            out.extend(self.t_examples)
        if self.reboots:
            out.extend(self.reboot_examples)
        if self.parse_errors > self.allow_errors:
            out.extend(self.parse_examples)
        if self.e_lock_errors:
            out.extend(self.e_examples)
        if self.ovf_max:
            out.append(f"FIFO ovf_cnt 最大 {self.ovf_max}")
        seen = set()
        unique: List[str] = []
        for line in out:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        return unique[:10]


# ---------------------------------------------------------------------------
# 输出工具
# ---------------------------------------------------------------------------
def preview(raw: object, limit: int = 60) -> str:
    if isinstance(raw, bytes):
        text = raw[:limit].decode("ascii", errors="replace")
        return repr(text)
    return repr(str(raw)[:limit])


def bytes_preview(data: bytes, limit: int = 96) -> str:
    if not data:
        return "(无)"
    chunk = data[:limit]
    ascii_text = chunk.decode("ascii", errors="replace")
    hex_text = " ".join(f"{b:02X}" for b in chunk[:32])
    return f"ascii={ascii_text!r} hex={hex_text}"


def print_info(info: Parsed, expect_fs: int, expect_win: int, expect_bands: int) -> None:
    f = info.fields
    caps = int(f.get("caps", 0))
    flags = []
    if caps & 0x1:
        flags.append("PL 硬件时间戳")
    if caps & 0x2:
        flags.append("事件 FIFO")
    print()
    print("[1/4] I 帧 OK")
    print(f"  dev_id   {f.get('dev_id')}")
    print(f"  fw       {f.get('fw')}")
    print(f"  protocol {f.get('major')}.{f.get('minor')}")
    print(f"  fs_hz    {f.get('fs_hz')}   期望 {expect_fs}   "
          f"{'PASS' if f.get('fs_hz') == expect_fs else 'FAIL'}")
    print(f"  win      {f.get('win')}   期望 {expect_win}   "
          f"{'PASS' if f.get('win') == expect_win else 'FAIL'}")
    print(f"  bands    {f.get('bands')}   期望 {expect_bands}   "
          f"{'PASS' if f.get('bands') == expect_bands else 'FAIL'}")
    print(f"  caps     0x{caps:04X}  ({', '.join(flags) if flags else '无'})")
    if not (caps & 0x1):
        print("  警告: caps.bit0=0，t_us 来自软件计时，延迟数字会偏大；"
              "硬件时间戳应置 bit0。")


def print_checklist() -> None:
    print()
    print("---- 硬件接入排查清单 ----")
    print("[ ] 1. 串口助手（XCOM）先关掉：它占着 COM 口，本工具和桥接都打不开。")
    print("[ ] 2. relay/server.py 也先停掉：它同样独占串口。")
    print("[ ] 3. 设备管理器 -> 端口 -> 高级 -> Latency Timer 改 1 ms。")
    print("       CH340/FTDI 默认 16 ms，会污染延迟测量。")
    print("[ ] 4. 波特率两端一致：板子固定 115200 8N1，改过一端就要同步改另一端。")
    print("[ ] 5. TX/RX 交叉，GND 共地，3.3V TTL 电平；不要接 RS232 电平。")
    print("[ ] 6. 板子确实在跑：LED、复位、PS 程序、PL bitstream；I 帧每 1 s 重发。")
    print("[ ] 7. 裸机不要用 %f：I/T/E/S 全部整数传输，上位机按 scale 还原。")
    print("[ ] 8. 解析器要处理半行、CRLF/LF 混用、乱码字节；")
    print("       seq 回绕用 (last + 1) & 0xFFFFFFFF，不要直接比大小。")
    print("[ ] 9. PS 已烧 v1 协议固件（I/T/E/S），不是旧版 Temp:... 字符串；")
    print("       旧版只发温湿度文本，不会有 I 帧。")
    print()
    print("本工具已处理：半行缓冲、CRLF/LF/CR、乱码行丢弃、seq 32 位模运算、")
    print("det=(lock+supp)&0xFFFFFFFF、t_us 倒退与重启判定。")
    print()


def list_ports() -> None:
    try:
        from serial.tools import list_ports as lp  # type: ignore
    except ImportError:
        print("未安装 pyserial，无法列出串口。")
        print("  D:\\Python\\python.exe -m pip install pyserial")
        return

    ports = list(lp.comports())
    if not ports:
        print("可用串口: 未发现。检查 USB 线、驱动和设备管理器。")
        return

    print("可用串口:")
    for p in ports:
        print(f"  {p.device:<8} {p.description}  [{p.hwid}]")
    print()


def print_no_i(
    source: LineSource,
    wait_s: float,
    errors: List[str],
) -> None:
    print()
    print("=" * 74)
    print(f"FAIL: {wait_s:.1f} s 内没有收到合法 I 帧")
    print(f"已收到字节: {source.bytes_seen}")
    if source.dropped_bytes:
        print(f"因无换行丢弃: {source.dropped_bytes} 字节（疑似波特率不对/二进制乱码）")
    print(f"前 96 字节: {bytes_preview(source.first_bytes)}")
    if errors:
        print("启动阶段解析错误:")
        for e in errors[:3]:
            print(f"  - {e}")
    if source.bytes_seen == 0:
        print("串口一个字节都没收到：优先查 TX/RX 是否交叉、GND、板子是否上电/在跑。")
    else:
        text = source.first_bytes.decode("ascii", errors="replace")
        if "Temp:" in text or "Humi:" in text or "BER:" in text:
            print("检测到旧版 Temp:/Humi:/BER: 文本：PS 还在发 v0 字符串，")
            print("不是 v1 的 I/T/E/S 协议。先烧录 v1 协议固件，再跑本工具。")
        else:
            print("收到数据但没有 I 帧：优先查波特率是否一致、TX/RX、是否把调试打印混进了协议流。")
    print_checklist()
    list_ports()
    print("=" * 74)


def print_bridge_steps(port: str, baud: int, url: str, open_browser: bool) -> None:
    print()
    print("[4/4] 网页徽章")
    print("  本工具退出后会释放串口。接着：")
    print("  1) 启动桥接（BOARD 模式）：")
    print(
        f"     D:\\Python\\python.exe relay/server.py "
        f"--source serial:{port}@{baud} --port 8080 --mode board"
    )
    print(f"  2) 浏览器打开 {url}")
    print("  3) 顶栏数据源徽章应从 SIM 变成 BOARD。")
    print()
    print("  如果还是 SIM：")
    print("     - 确认 --source 是 serial:... 而不是 tcp:...")
    print("     - 或显式加 --mode board")
    print("     - 浏览器 Ctrl+F5 强制刷新")
    print("  注意：桥接和本工具不能同时占用同一个 COM 口。")
    if open_browser:
        print("  已尝试打开浏览器；请先启动桥接再刷新页面。")
        try:
            webbrowser.open(url)
        except Exception:
            pass


def wait_for_i(
    source: LineSource,
    wait_s: float,
) -> Tuple[Optional[Parsed], List[str]]:
    deadline = time.monotonic() + wait_s
    errors: List[str] = []
    while True:
        raw = source.readline(deadline)
        if raw is None:
            return None, errors
        parsed = parse_line(raw)
        if parsed.ok and parsed.kind == "I":
            return parsed, errors
        if not parsed.ok:
            if len(errors) < 5:
                errors.append(f"{parsed.err}: {preview(parsed.raw)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="机械异响监测系统 | 硬件接入 60s 自检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python tools/hardware_check.py --port COM5 --baud 115200\n"
            "  python tools/hardware_check.py --source serial:COM5@115200\n"
            "  python tools/hardware_check.py --source file:records/sim60.log\n"
            "  python tools/hardware_check.py --checklist\n"
            "  python tools/hardware_check.py --list-ports\n"
        ),
    )
    parser.add_argument("--port", help="串口，例如 COM5")
    parser.add_argument("--baud", type=int, default=115200, help="波特率，默认 115200")
    parser.add_argument(
        "--source",
        help="serial:COM5@115200 或 file:records/sim60.log（文件模式用于自测）",
    )
    parser.add_argument("--seconds", type=float, default=60.0, help="采集秒数，默认 60")
    parser.add_argument("--wait-i", type=float, default=3.0, help="等 I 帧秒数，默认 3")
    parser.add_argument("--expect-fs", type=int, default=48000, help="期望采样率")
    parser.add_argument("--expect-win", type=int, default=512, help="期望窗口样本数")
    parser.add_argument("--expect-bands", type=int, default=2, help="期望频带数")
    parser.add_argument("--allow-errors", type=int, default=0, help="允许的解析错误条数")
    parser.add_argument("--rate-min", type=float, default=18.0, help="最低 T 帧率")
    parser.add_argument("--rate-max", type=float, default=22.0, help="最高 T 帧率")
    parser.add_argument("--list-ports", action="store_true", help="列出串口后退出")
    parser.add_argument("--checklist", action="store_true", help="只打印排查清单")
    parser.add_argument("--open-browser", action="store_true", help="PASS 后尝试打开看板")
    parser.add_argument("--url", default="http://127.0.0.1:8080/", help="看板地址")
    args = parser.parse_args(argv)

    if args.checklist:
        print_checklist()
        return 0

    if args.list_ports:
        list_ports()
        return 0

    source, is_serial = build_source(args)
    checker = Checker(
        expect_fs=args.expect_fs,
        expect_win=args.expect_win,
        expect_bands=args.expect_bands,
        allow_errors=args.allow_errors,
        rate_min=args.rate_min,
        rate_max=args.rate_max,
    )

    try:
        print(f"[0/4] 等待 I 帧（最多 {args.wait_i:.1f} s）...")
        info, startup_errors = wait_for_i(source, args.wait_i)
        if info is None:
            print_no_i(source, args.wait_i, startup_errors)
            return 2

        if startup_errors:
            print(f"启动阶段忽略解析错误 {len(startup_errors)} 条（通常是开串口时的半行/乱码）")
            for e in startup_errors[:3]:
                print(f"  - {e}")

        print_info(info, args.expect_fs, args.expect_win, args.expect_bands)
        checker.feed(info)

        if is_serial:
            print(f"\n[2/4] 采集 {args.seconds:.0f} s ...")
        else:
            print(f"\n[2/4] 读取文件（不按墙钟等待）...")

        start = time.monotonic()
        deadline = start + args.seconds
        try:
            while True:
                raw = source.readline(deadline)
                if raw is None:
                    break
                parsed = parse_line(raw)
                checker.feed(parsed)
        except KeyboardInterrupt:
            print("\n[中断] 用户中断，输出已采集部分。")
            elapsed = time.monotonic() - start
            return checker.report(source.name, elapsed, not is_serial)

        elapsed = time.monotonic() - start
        rc = checker.report(source.name, elapsed, not is_serial)
        source.close()

        if rc == 0 and is_serial:
            port, _, baud_s = source.name.partition(" @ ")
            print_bridge_steps(port, int(baud_s.split()[0]), args.url, args.open_browser)
        elif rc == 0 and not is_serial:
            print("\n文件自测 PASS。接硬件时改用 --port COMx 或 --source serial:COMx@115200。")

        return rc
    finally:
        source.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
