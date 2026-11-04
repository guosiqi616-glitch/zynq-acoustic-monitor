#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
detector_probe.py -- 直读串口，验证 PS 侧多维检测器（v2）的 X 帧

用法（在 host_software 目录下）：
    python tools/detector_probe.py --port COM6 --seconds 20
    python tools/detector_probe.py --port COM6 --seconds 20 --verbose

输出：
    - 启动横幅（# 开头）与自检行
    - 每条 E 帧（事件）立即打印
    - X 帧按 1 Hz 汇总打印一行
    - 结束时给出统计：学习期长度、BER 基线、score 分位数、事件数、级别分布
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.protocol import parse_line  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--verbose", action="store_true", help="逐条打印 X 帧")
    ap.add_argument("--save", default="", help="原始行保存到文件")
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        print("need pyserial: py -m pip install pyserial", file=sys.stderr)
        return 2

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    t0 = time.time()
    t_end = t0 + args.seconds

    levels: Counter = Counter()
    scores: list[int] = []
    bers: list[int] = []
    sds: list[int] = []
    n_t = n_e = n_x = 0
    events: list[str] = []
    last_x_print = 0.0
    fh = open(args.save, "w", encoding="utf-8") if args.save else None

    print("== listening %s @ %d for %.1f s ==" % (args.port, args.baud, args.seconds))

    try:
        while time.time() < t_end:
            raw = ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            if fh:
                fh.write(line + "\n")

            if line.startswith("#"):
                print("  " + line)
                continue

            p = parse_line(line)
            if not p.ok:
                continue
            if p.kind == "T":
                n_t += 1
            elif p.kind == "X":
                n_x += 1
                f = p.fields
                levels[int(f["level"])] += 1
                scores.append(int(f["score"]))
                bers.append(int(f["ber"]))
                sds.append(int(f["sd_ber"]))
                now = time.time()
                if args.verbose or (now - last_x_print) >= 1.0:
                    last_x_print = now
                    print(
                        "X lvl=%d score=%8d ber=%4d res=%4d kurt=%2d "
                        "thr=%3d mu=%4d sd=%3d nf=%6d/%6d flags=0x%02x"
                        % (
                            int(f["level"]), int(f["score"]), int(f["ber"]),
                            int(f["ber_res"]), int(f["kurt"]), int(f["thr"]),
                            int(f["mu_ber"]), int(f["sd_ber"]),
                            int(f["nf_low"]), int(f["nf_high"]), int(f["flags"]),
                        )
                    )
            elif p.kind == "E":
                n_e += 1
                f = p.fields
                msg = "E  #%d t=%sus ber=%d thr=%d reason=%d" % (
                    len(events) + 1, f["t_us"], f["ber"], f["thr"], f["reason"])
                events.append(msg)
                print(">>> " + msg)
    finally:
        ser.close()
        if fh:
            fh.close()

    el = time.time() - t0
    print()
    print("== summary (%.1f s) ==" % el)
    print("frames: T=%d X=%d E=%d  (T rate %.1f Hz, X rate %.1f Hz)"
          % (n_t, n_x, n_e, n_t / el, n_x / el))
    if scores:
        sc = sorted(scores)
        def q(p: float) -> int:
            return sc[min(len(sc) - 1, int(p * len(sc)))]
        print("score p50=%d p90=%d p99=%d max=%d" % (q(0.5), q(0.9), q(0.99), sc[-1]))
        print("ber   min=%d median=%d max=%d" % (min(bers), statistics.median(bers), max(bers)))
        if sds:
            print("sd_ber median=%d" % statistics.median(sds))
        print("level distribution: " + ", ".join(
            "L%d=%d" % (k, v) for k, v in sorted(levels.items())))
    else:
        print("no X frames -- 检查波特率 / 串口是否被其他程序占用 / 板子是否在跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
