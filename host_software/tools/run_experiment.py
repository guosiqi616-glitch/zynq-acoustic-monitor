#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_experiment.py -- 播放测试素材并同步记录串口原始行

用法（在 host_software 目录下，且 COM 口空闲）：
    python tools/run_experiment.py --port COM6 --out ..\\records\\exp_run.csv

输出 CSV：每行 "pc_time <TAB> 原始行"，另有 "#PLAY <file> <pc_time>" 标记行，
供 analyze_experiment.py 做时间对齐。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import winsound

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MANIFEST = os.path.join(ROOT, "test_audio", "_exp", "manifest.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM6")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--out", default=os.path.join(ROOT, "records", "exp_run.csv"))
    ap.add_argument("--gap", type=float, default=3.0)
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        print("need pyserial", file=sys.stderr)
        return 2

    with open(args.manifest, "r", encoding="utf-8") as f:
        man = json.load(f)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fh = open(args.out, "w", encoding="utf-8", newline="")

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    stop = threading.Event()

    def reader() -> None:
        buf = bytearray()
        while not stop.is_set():
            try:
                chunk = ser.read(256)
            except Exception:
                break
            if not chunk:
                continue
            now = time.time()
            buf.extend(chunk)
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                text = line.decode("ascii", errors="replace").strip("\r")
                if text:
                    fh.write("%.6f\t%s\n" % (now, text))
                    fh.flush()

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    total = sum(f["duration_s"] for f in man["files"]) + args.gap * len(man["files"])
    print("total playback %.1f s over %d files" % (total, len(man["files"])))

    t_all = time.time()
    try:
        for i, f in enumerate(man["files"], 1):
            fh.write("%.6f\t#PLAY\t%s\n" % (time.time(), f["name"]))
            fh.flush()
            print("[%d/%d] %-24s %5.1f s  (elapsed %.0f s)" %
                  (i, len(man["files"]), f["name"], f["duration_s"],
                   time.time() - t_all))
            winsound.PlaySound(f["path"], winsound.SND_FILENAME | winsound.SND_ASYNC)
            deadline = time.time() + f["duration_s"] + 0.4
            while time.time() < deadline:
                time.sleep(0.2)
            winsound.PlaySound(None, winsound.SND_PURGE)
            time.sleep(args.gap)
    finally:
        stop.set()
        th.join(timeout=2.0)
        try:
            ser.close()
        except Exception:
            pass
        fh.close()

    print("done in %.0f s -> %s" % (time.time() - t_all, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
