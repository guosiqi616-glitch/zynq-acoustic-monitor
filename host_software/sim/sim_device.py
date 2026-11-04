#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim_device.py — Zynq-7010 机械异响监测系统 设备模拟器

按 docs/PROTOCOL.md v1.0 输出 I/T/E/S 四帧，支持：
  - 1 kHz 信号模型，20 Hz 发 T，1 Hz 发 I/S，越阈立即发 E
  - 基线 AR(1) 游走 + 5~15 s 随机突发（200 ms 钟形包络）
  - 迟滞 + 不应期，保证 det = lock + supp
  - 故障注入：断线、重启、丢 T、丢 E、手动事件、传感器失效
  - 30 秒演示序列
  - stdout / file / tcp / serial 多种输出
  - --fast / --seed / --selftest，便于回放与 CI

用法（在 host_software/ 目录下执行）：
  python sim/sim_device.py                          # 实时，stdout
  python sim/sim_device.py --sink tcp:0.0.0.0:9100  # 给中继/前端用
  python sim/sim_device.py --fast --duration 60 --seed 42 --sink file:records/sim60.log
  python sim/sim_device.py --selftest
  python sim/sim_device.py --script "3:d,6:b,9:x,11:v,12:l"

键位（stdin 是 TTY 时生效）：
  D 断线3s   B 重启   X 丢T   V 丢E   P 演示30s
  L 手动事件 S 传感器失效2s   Q 退出
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 协议常量（与 docs/PROTOCOL.md 保持一致）
# ---------------------------------------------------------------------------
PROTO_MAJOR = 1
PROTO_MINOR = 0
T_PERIOD = 0.05       # 20 Hz
S_PERIOD = 1.0        # 1 Hz
TICK = 0.001          # 1 kHz 模型步进


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def db10(db: float) -> int:
    return int(round(db * 10.0))


def ber1000(ber: float) -> int:
    return int(round(ber * 1000.0))


def temp100(t: Optional[float]) -> int:
    return -32768 if t is None else int(round(t * 100.0))


def hum100(h: Optional[float]) -> int:
    return 65535 if h is None else int(round(h * 100.0))


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class Config:
    dev_id: str = "ZYNQ7010-SIM"
    fw: str = "0.3.1-sim"
    fs_hz: int = 48000
    win: int = 512
    bands: int = 2
    caps: int = 3

    warn: float = 0.55
    trig: float = 0.65
    hys: float = 0.03
    refr: float = 0.05

    burst_min: float = 8.0
    burst_max: float = 30.0
    burst_every: Tuple[float, float] = (5.0, 15.0)
    sensor_every: Tuple[float, float] = (35.0, 70.0)


# 30 秒演示：安静 → 预警 → 报警 → 恢复
DEMO: List[Tuple[float, float, float]] = [
    (2.0, 3.0, 0.20),    # 小突发，只预警
    (6.0, 10.0, 0.20),   # 第 1 次锁存
    (10.0, 18.0, 0.50),  # 持续异响，第 2 次锁存
    (16.0, 12.0, 0.20),  # 第 3 次锁存
    (22.0, 3.5, 0.20),   # 余震，只预警
]


# ---------------------------------------------------------------------------
# 信号模型
# ---------------------------------------------------------------------------
class SignalModel:
    """基线 AR(1) + 慢漂移 + 钟形突发 + 温湿度。"""

    def __init__(self, rng: random.Random, cfg: Config):
        self.rng = rng
        self.cfg = cfg
        self.nl = 0.0
        self.nh = 0.0
        self.bursts: List[Tuple[float, float, float, float]] = []
        self.next_burst = rng.uniform(2.0, 5.0)
        self.random_bursts = True
        self.invalid_until = -1.0
        self.next_sensor = rng.uniform(*cfg.sensor_every)

    def burst(self, t0: float, amp_h: float,
              amp_l: Optional[float] = None,
              dur: Optional[float] = None) -> None:
        r = self.rng
        if amp_l is None:
            amp_l = r.uniform(0.0, 5.0)
        if dur is None:
            dur = r.uniform(0.15, 0.30)
        self.bursts.append((t0, amp_h, amp_l, dur))

    def _sensor(self, t: float) -> Tuple[Optional[float], Optional[float], bool]:
        r = self.rng
        if t >= self.next_sensor:
            self.invalid_until = t + 2.0
            self.next_sensor = t + r.uniform(*self.cfg.sensor_every)
        invalid = t < self.invalid_until
        if invalid:
            return None, None, True
        temp = 25.0 + 0.4 * math.sin(2.0 * math.pi * t / 120.0) + r.gauss(0.0, 0.03)
        hum = 50.0 + 2.5 * math.sin(2.0 * math.pi * t / 180.0 + 1.0) + r.gauss(0.0, 0.15)
        return temp, hum, False

    def at(self, t: float) -> Tuple[float, float, Optional[float], Optional[float], bool]:
        r = self.rng

        # 基线游走：0.995 的 AR(1)，约 200 ms 时间常数
        # 噪声标准差控制在 ~0.5 dB，避免基线自己越过触发阈值
        self.nl = 0.995 * self.nl + r.gauss(0.0, 0.04)
        self.nh = 0.995 * self.nh + r.gauss(0.0, 0.06)
        drift = 1.2 * math.sin(2.0 * math.pi * t / 90.0)

        base_low = clamp(-80.0 + drift + self.nl, -90.0, -75.0)
        base_high = clamp(-81.5 + drift + self.nh, -90.0, -75.0)

        # 随机突发调度
        if self.random_bursts:
            while t >= self.next_burst:
                self.burst(
                    self.next_burst,
                    r.uniform(self.cfg.burst_min, self.cfg.burst_max),
                    r.uniform(0.0, 5.0),
                    r.uniform(0.15, 0.30),
                )
                self.next_burst += r.uniform(*self.cfg.burst_every)

        # 叠加突发包络
        dbl, dbh = base_low, base_high
        keep: List[Tuple[float, float, float, float]] = []
        for t0, amp_h, amp_l, dur in self.bursts:
            if t0 <= t <= t0 + dur:
                u = (t - t0) / dur
                env = math.sin(math.pi * u) ** 2
                dbh += amp_h * env
                dbl += amp_l * env
            if t < t0 + dur:
                keep.append((t0, amp_h, amp_l, dur))
        self.bursts = keep

        dbl = clamp(dbl, -95.0, -30.0)
        dbh = clamp(dbh, -95.0, -20.0)

        temp, hum, invalid = self._sensor(t)
        return dbl, dbh, temp, hum, invalid


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------
class Detector:
    """迟滞 + 不应期。只有这里能改 det/lock/supp，也只有这里能产生 E。"""

    def __init__(self, warn: float, trig: float, hys: float, refr: float):
        self.warn = warn
        self.trig = trig
        self.hys = hys
        self.refr = refr
        self.reset()

    def reset(self) -> None:
        self.armed = True
        self.det = 0
        self.lock = 0
        self.supp = 0
        self.last_lock = -1e9

    def state(self, ber: float) -> int:
        if ber >= self.trig:
            return 2
        if ber >= self.warn:
            return 1
        return 0

    def step(self, t: float, dbl: float, dbh: float,
             ber: float) -> Optional[Tuple[float, float, float]]:
        if ber < self.trig - self.hys:
            self.armed = True
        if not (self.armed and ber >= self.trig):
            return None

        self.armed = False
        self.det += 1
        if t - self.last_lock < self.refr:
            self.supp += 1
            return None

        self.lock += 1
        self.last_lock = t
        return (dbl, dbh, ber)


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
class Sink:
    def write(self, line: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class StdoutSink(Sink):
    def write(self, line: str) -> None:
        sys.stdout.buffer.write(line.encode("ascii"))
        sys.stdout.buffer.flush()


class FileSink(Sink):
    def __init__(self, path: str, append: bool = False):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self.f = open(path, "ab" if append else "wb", buffering=0)

    def write(self, line: str) -> None:
        self.f.write(line.encode("ascii"))

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


class MemorySink(Sink):
    def __init__(self) -> None:
        self.lines: List[str] = []

    def write(self, line: str) -> None:
        self.lines.append(line.rstrip("\r\n"))


class TcpSink(Sink):
    """监听 TCP，向所有已连接客户端广播。"""

    def __init__(self, host: str, port: int):
        self._lock = threading.Lock()
        self._clients: List[socket.socket] = []
        self._stop = False
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host or "0.0.0.0", port))
        self._srv.listen(8)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop:
            try:
                client, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(1.0)
            with self._lock:
                self._clients.append(client)

    def write(self, line: str) -> None:
        data = line.encode("ascii")
        dead: List[socket.socket] = []
        with self._lock:
            for client in self._clients:
                try:
                    client.sendall(data)
                except OSError:
                    dead.append(client)
            for client in dead:
                try:
                    self._clients.remove(client)
                except ValueError:
                    pass
                try:
                    client.close()
                except OSError:
                    pass

    def close(self) -> None:
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass
        with self._lock:
            for client in self._clients:
                try:
                    client.close()
                except OSError:
                    pass
            self._clients.clear()


class SerialSink(Sink):
    def __init__(self, port: str, baud: int):
        try:
            import serial  # type: ignore
        except ImportError:
            raise SystemExit("串口输出需要 pyserial：pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=0, write_timeout=1)

    def write(self, line: str) -> None:
        self.ser.write(line.encode("ascii"))

    def close(self) -> None:
        try:
            self.ser.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------
class Device:
    def __init__(self, cfg: Config, sinks: Sequence[Sink], seed: Optional[int]):
        self.cfg = cfg
        self.sinks = list(sinks)
        self.rng = random.Random(seed)
        self.sig = SignalModel(self.rng, cfg)
        self.det = Detector(cfg.warn, cfg.trig, cfg.hys, cfg.refr)

        self.seq: Dict[str, int] = {"T": 0, "E": 0, "S": 0}
        self.reboot_epoch = 0.0
        self.mute_until = -1.0
        self.drop_t = 0
        self.drop_e = 0

        self.next_t = 0.0
        self.next_s = 0.0
        self.started = False

        self.demo_until = -1.0
        self.sensor_was_invalid = False
        self.err_cnt = 0
        self.e_count = 0
        self.events = 0
        self.last = (-80.0, -81.5, 0.41)

    # -- 基础 -------------------------------------------------------------
    def us(self, t: float) -> int:
        return int(max(0.0, t - self.reboot_epoch) * 1e6)

    def next_seq(self, kind: str) -> int:
        v = self.seq[kind]
        self.seq[kind] = (v + 1) & 0xFFFFFFFF
        return v

    def emit(self, line: str, t: float) -> None:
        if t < self.mute_until:
            return
        payload = line + "\r\n"
        for sink in self.sinks:
            sink.write(payload)

    # -- 帧 ---------------------------------------------------------------
    def emit_i(self, t: float) -> None:
        line = "I,{},{},{},{},{},{},{},{}".format(
            PROTO_MAJOR, PROTO_MINOR, self.cfg.dev_id, self.cfg.fw,
            self.cfg.fs_hz, self.cfg.win, self.cfg.bands, self.cfg.caps,
        )
        self.emit(line, t)

    def send_t(self, t: float, dbl: float, dbh: float, ber: float,
               state: int, temp: Optional[float], hum: Optional[float]) -> None:
        seq = self.next_seq("T")
        line = "T,{},{},{},{},{},{},{},{},{},{},{}".format(
            seq, self.us(t), temp100(temp), hum100(hum),
            db10(dbl), db10(dbh), ber1000(ber), state,
            self.det.lock, self.det.det, self.det.supp,
        )
        self.emit(line, t)

    def send_e(self, t: float, dbl: float, dbh: float, ber: float,
               reason: int) -> None:
        seq = self.next_seq("E")
        line = "E,{},{},{},{},{},{},{},{}".format(
            seq, self.us(t), db10(dbl), db10(dbh), ber1000(ber),
            ber1000(self.cfg.trig), self.det.lock, reason,
        )
        self.e_count += 1
        self.events += 1
        if self.drop_e:
            self.drop_e = 0
            return
        self.emit(line, t)

    def send_s(self, t: float, invalid: bool) -> None:
        seq = self.next_seq("S")
        flags = 1 | (0 if invalid else 2)
        line = "S,{},{},{},{},{}".format(
            seq, self.us(t), flags, self.err_cnt, 0,
        )
        self.emit(line, t)

    # -- 主循环 -----------------------------------------------------------
    def tick(self, t: float) -> None:
        if not self.started:
            self.started = True
            self.emit_i(t)
            self.next_t = t + T_PERIOD
            self.next_s = t + S_PERIOD

        if self.demo_until > 0 and t >= self.demo_until:
            self.demo_until = -1.0
            self.sig.random_bursts = True
            self.sig.next_burst = t + self.rng.uniform(*self.cfg.burst_every)

        dbl, dbh, temp, hum, invalid = self.sig.at(t)
        el = 10.0 ** (dbl / 10.0)
        eh = 10.0 ** (dbh / 10.0)
        ber = eh / (el + eh)
        self.last = (dbl, dbh, ber)

        # 传感器失效只通过 T 帧哨兵和 S 帧 flags 表达，
        # 不计入 err_cnt（err_cnt 表示真实的 I2C/传感器错误）
        self.sensor_was_invalid = invalid

        state = self.det.state(ber)
        snap = self.det.step(t, dbl, dbh, ber)
        if snap is not None:
            self.send_e(t, snap[0], snap[1], snap[2], 0)

        if t >= self.next_t:
            if self.drop_t:
                self.drop_t = 0
                self.next_seq("T")
            else:
                self.send_t(t, dbl, dbh, ber, state, temp, hum)
            self.next_t += T_PERIOD
            if self.next_t < t - 0.5:
                self.next_t = t + T_PERIOD

        if t >= self.next_s:
            self.emit_i(t)
            self.send_s(t, invalid)
            self.next_s += S_PERIOD
            if self.next_s < t - 0.5:
                self.next_s = t + S_PERIOD

    # -- 故障注入 ---------------------------------------------------------
    def disconnect(self, t: float) -> None:
        self.mute_until = t + 3.0

    def reboot(self, t: float) -> None:
        self.reboot_epoch = t
        self.seq = {"T": 0, "E": 0, "S": 0}
        self.det.reset()
        self.mute_until = -1.0
        self.e_count = 0
        self.err_cnt = 0
        self.sensor_was_invalid = False
        self.next_t = t + T_PERIOD
        self.next_s = t + S_PERIOD
        self.emit_i(t)

    def drop_t_frame(self) -> None:
        self.drop_t = 1

    def drop_e_frame(self) -> None:
        self.drop_e = 1

    def manual(self, t: float) -> None:
        dbl, dbh, ber = self.last
        self.det.det += 1
        self.det.lock += 1
        self.det.last_lock = t
        self.det.armed = False
        self.send_e(t, dbl, dbh, ber, 1)

    def sensor_invalid(self, t: float) -> None:
        self.sig.invalid_until = t + 2.0

    def start_demo(self, t: float) -> None:
        self.demo_until = t + 30.0
        self.sig.random_bursts = False
        self.sig.bursts.clear()
        for offset, amp_h, dur in DEMO:
            self.sig.burst(t + offset, amp_h, self.rng.uniform(0.0, 4.0), dur)


# ---------------------------------------------------------------------------
# 键盘 / 控制端口
# ---------------------------------------------------------------------------
class Keyboard:
    def __init__(self) -> None:
        self.enabled = sys.stdin.isatty()
        self.fd: Optional[int] = None
        self.old = None
        self._msvcrt = None
        if not self.enabled:
            return
        if os.name == "nt":
            import msvcrt  # type: ignore
            self._msvcrt = msvcrt
        else:
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)

    def poll(self) -> Optional[str]:
        if not self.enabled:
            return None
        if os.name == "nt":
            if self._msvcrt.kbhit():
                ch = self._msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    self._msvcrt.getwch()
                    return None
                return ch.lower()
            return None
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            ch = sys.stdin.read(1)
            return ch.lower() if ch else None
        return None

    def close(self) -> None:
        if self.enabled and os.name != "nt" and self.fd is not None and self.old is not None:
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)


class ControlServer:
    """单字符控制端口，方便 Web 页面注入故障。"""

    KEYS = set("dbxvplsq")

    def __init__(self, host: str, port: int):
        self.q: "queue.Queue[str]" = queue.Queue()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host or "0.0.0.0", port))
        self._sock.listen(4)
        self._sock.settimeout(0.2)
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop:
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(0.5)
            with client:
                while not self._stop:
                    try:
                        data = client.recv(64)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    for byte in data:
                        ch = chr(byte).lower()
                        if ch in self.KEYS:
                            self.q.put(ch)

    def poll(self) -> Optional[str]:
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 时钟
# ---------------------------------------------------------------------------
class Clock:
    def __init__(self, speed: float, fast: bool):
        self.fast = fast or speed <= 0.0
        self.speed = speed if speed > 0.0 else 1.0
        self.wall0 = time.monotonic()
        self.sim = 0.0
        self.next_deadline = 0.0

    def advance(self) -> Optional[float]:
        if self.fast:
            self.sim += TICK
            return self.sim
        target = (time.monotonic() - self.wall0) * self.speed
        if target < self.next_deadline:
            return None
        self.sim += TICK
        self.next_deadline += TICK
        if self.next_deadline < target - 0.02:
            # Windows 定时器粒度/CPU 抖动导致落后时，直接对齐墙钟，
            # 避免“设备时间”越拖越久，把上报延迟显示成几十毫秒
            self.sim = target
            self.next_deadline = target
        return self.sim


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def handle_key(dev: Device, ch: str, t: float) -> bool:
    if ch == "d":
        dev.disconnect(t)
    elif ch == "b":
        dev.reboot(t)
    elif ch == "x":
        dev.drop_t_frame()
    elif ch == "v":
        dev.drop_e_frame()
    elif ch == "p":
        dev.start_demo(t)
    elif ch == "l":
        dev.manual(t)
    elif ch == "s":
        dev.sensor_invalid(t)
    elif ch == "q":
        return False
    return True


def print_status(dev: Device, t: float) -> None:
    dbl, dbh, ber = dev.last
    flags = []
    if t < dev.mute_until:
        flags.append("MUTE")
    if dev.demo_until > 0:
        flags.append("DEMO")
    print(
        "[{:8.2f}s] dB({:6.1f},{:6.1f}) BER={:.3f} st={} "
        "lock={} det={} supp={} seqT={} E={} {}".format(
            t, dbl, dbh, ber, dev.det.state(ber),
            dev.det.lock, dev.det.det, dev.det.supp,
            dev.seq["T"], dev.e_count, " ".join(flags),
        ),
        file=sys.stderr,
    )


def parse_script(script: Optional[str]) -> List[Tuple[float, str]]:
    if not script:
        return []
    items: List[Tuple[float, str]] = []
    for part in script.split(","):
        part = part.strip()
        if not part:
            continue
        when_s, _, key = part.partition(":")
        try:
            when = float(when_s)
        except ValueError:
            raise SystemExit(f"脚本时间非法: {part}")
        key = key.strip().lower()
        if key not in set("dbxvplsq"):
            raise SystemExit(f"脚本键位非法: {part}")
        items.append((when, key))
    items.sort(key=lambda x: x[0])
    return items


def build_sinks(specs: Sequence[str]) -> List[Sink]:
    if not specs:
        specs = ["stdout"]
    sinks: List[Sink] = []
    for spec in specs:
        if spec == "stdout":
            sinks.append(StdoutSink())
        elif spec.startswith("file+:"):
            sinks.append(FileSink(spec[6:], append=True))
        elif spec.startswith("file:"):
            sinks.append(FileSink(spec[5:], append=False))
        elif spec.startswith("tcp:"):
            host, _, port_s = spec[4:].rpartition(":")
            sinks.append(TcpSink(host, int(port_s or "9100")))
        elif spec.startswith("serial:"):
            body = spec[7:]
            port, _, baud_s = body.partition("@")
            sinks.append(SerialSink(port, int(baud_s or "115200")))
        else:
            raise SystemExit(f"未知 sink: {spec}")
    return sinks


def run(cfg: Config, sinks: Sequence[Sink], seed: Optional[int],
        speed: float, fast: bool, duration: float, quiet: bool,
        control: Optional[str], script: Optional[str]) -> int:
    dev = Device(cfg, sinks, seed)
    keyboard = Keyboard()
    ctrl: Optional[ControlServer] = None
    if control:
        host, _, port_s = control[4:].rpartition(":") if control.startswith("tcp:") else ("0.0.0.0", "", control)
        ctrl = ControlServer(host, int(port_s or "9101"))

    clock = Clock(speed, fast)
    script_items = parse_script(script)
    next_status = 0.0
    next_key = 0.0

    print(
        "sim_device: dev={} proto={}.{}  keys D/B/X/V/P/L/S/Q".format(
            cfg.dev_id, PROTO_MAJOR, PROTO_MINOR,
        ),
        file=sys.stderr,
    )

    try:
        while True:
            t = clock.advance()
            if t is None:
                time.sleep(0.0002)
                continue
            if duration > 0 and t >= duration:
                break

            if t >= next_key:
                ch = keyboard.poll()
                if ch is None and ctrl is not None:
                    ch = ctrl.poll()
                if ch is not None and not handle_key(dev, ch, t):
                    break
                next_key = t + 0.01

            while script_items and t >= script_items[0][0]:
                _, ch = script_items.pop(0)
                if not handle_key(dev, ch, t):
                    return 0

            dev.tick(t)

            if not quiet and t >= next_status:
                print_status(dev, t)
                next_status = t + 1.0
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.close()
        if ctrl is not None:
            ctrl.close()
        for sink in sinks:
            sink.close()
    return 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def run_selftest(cfg: Config, seed: int, duration: float) -> int:
    sink = MemorySink()
    dev = Device(cfg, [sink], seed)
    t = 0.0
    while t < duration:
        t += TICK
        dev.tick(t)

    errors: List[str] = []
    if dev.det.det != dev.det.lock + dev.det.supp:
        errors.append(
            "det != lock + supp: {} != {} + {}".format(
                dev.det.det, dev.det.lock, dev.det.supp))
    if dev.e_count != dev.det.lock:
        errors.append(
            "E 帧数 != lock: {} != {}".format(dev.e_count, dev.det.lock))
    if dev.events < 3:
        errors.append("60 秒内事件太少: {}".format(dev.events))

    last_t = -1
    last_tseq: Optional[int] = None
    t_count = 0
    for line in sink.lines:
        if not line:
            errors.append("出现空行")
            continue
        parts = line.split(",")
        kind = parts[0]
        if kind not in ("I", "T", "E", "S"):
            errors.append("未知帧: {}".format(line[:40]))
            continue
        if kind == "I":
            continue
        try:
            if kind == "T":
                seq = int(parts[1])
                tu = int(parts[2])
                if last_tseq is not None and ((last_tseq + 1) & 0xFFFFFFFF) != seq:
                    errors.append("T seq 跳号: {} -> {}".format(last_tseq, seq))
                last_tseq = seq
                t_count += 1
                if not (0 <= int(parts[7]) <= 1000):
                    errors.append("BER 越界: {}".format(parts[7]))
                lock, det, supp = int(parts[9]), int(parts[10]), int(parts[11])
                if det != (lock + supp) & 0xFFFFFFFF:
                    errors.append("T 中 det != lock + supp")
            else:
                tu = int(parts[2])
        except (IndexError, ValueError) as exc:
            errors.append("字段非法: {} ({})".format(line[:40], exc))
            continue
        if tu < last_t:
            errors.append("t_us 倒退: {} -> {}".format(last_t, tu))
        last_t = tu

    if t_count == 0:
        errors.append("没有 T 帧")

    print(
        "selftest: {:.0f}s  T={} E={} lock={} det={} supp={} events={}".format(
            duration, t_count, dev.e_count, dev.det.lock,
            dev.det.det, dev.det.supp, dev.events,
        ),
        file=sys.stderr,
    )

    if errors:
        print("FAIL")
        for err in errors[:20]:
            print("  -", err)
        return 1
    print("PASS")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Zynq-7010 机械异响监测系统设备模拟器")
    parser.add_argument("--sink", action="append", default=[],
                        help="stdout / file:PATH(覆盖) / file+:PATH(追加) / tcp:HOST:PORT / serial:COM5@115200")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--speed", type=float, default=1.0, help="时间倍速，默认 1.0")
    parser.add_argument("--fast", action="store_true", help="不按墙钟，尽快生成")
    parser.add_argument("--duration", type=float, default=0.0, help="模拟时长（秒）")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="不打印状态行")
    parser.add_argument("--control", default=None, help="tcp:0.0.0.0:9101")
    parser.add_argument("--script", default=None, help='定时注入，如 "3:d,6:b,9:x"')
    parser.add_argument("--dev-id", default="ZYNQ7010-SIM")
    parser.add_argument("--trig", type=float, default=0.65)
    parser.add_argument("--warn", type=float, default=0.55)
    args = parser.parse_args(argv)

    cfg = Config(dev_id=args.dev_id, warn=args.warn, trig=args.trig)

    if args.selftest:
        return run_selftest(cfg, args.seed if args.seed is not None else 42,
                            args.duration if args.duration > 0 else 60.0)

    if args.fast and args.duration <= 0:
        parser.error("--fast 需要同时指定 --duration")

    sinks = build_sinks(args.sink)
    return run(
        cfg, sinks, args.seed, args.speed, args.fast, args.duration,
        args.quiet, args.control, args.script,
    )


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
