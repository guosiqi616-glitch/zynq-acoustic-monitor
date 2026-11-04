#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
longrun_monitor.py -- 长时间稳定性观测（不占用串口，走 relay 的 HTTP 接口）

用法：
    python tools/longrun_monitor.py --minutes 120

每 10 s 采样一次 /api/state，记录丢帧、解析错误、协议不变量、BER、事件数，
结束时输出一份可直接贴进测试报告的汇总。

若 relay 未启动，脚本会持续重试并记录断链时长。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def fetch(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--minutes", type=float, default=120.0)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "records", "longrun.csv"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fh = open(args.out, "w", newline="", encoding="utf-8")
    w = csv.writer(fh)
    w.writerow(["pc_time", "ok", "rx", "fps", "lost", "errors",
                "e_missing", "dev_err", "ovf", "invariant_ok",
                "ber", "state", "lock", "age_ms", "connected"])

    t0 = time.time()
    t_end = t0 + args.minutes * 60.0
    samples = 0
    down = 0
    events = 0
    fps_list = []
    ber_list = []
    last_rx = None
    rx_stall = 0

    print("monitoring %s for %.0f min ..." % (args.base, args.minutes))
    try:
        while time.time() < t_end:
            ok = False
            try:
                st = fetch(args.base + "/api/state")
                ok = True
            except Exception:
                st = None

            if ok:
                s = st["state"]
                p = st["status"]
                ev = st.get("events") or []
                events = max(events, len(ev))
                w.writerow([("%.3f" % time.time()), 1,
                            p.get("rx"), p.get("fps"), p.get("lost"),
                            p.get("errors"), p.get("e_missing"),
                            p.get("dev_err"), p.get("ovf"),
                            int(bool(p.get("invariant_ok"))),
                            s.get("ber"), s.get("state"), s.get("lock"),
                            s.get("latency_ms"), int(bool(p.get("connected")))])
                if p.get("fps"):
                    fps_list.append(float(p["fps"]))
                if s.get("ber") is not None:
                    ber_list.append(int(s["ber"]))
                if last_rx is not None and p.get("rx") == last_rx:
                    rx_stall += 1
                last_rx = p.get("rx")
                samples += 1
            else:
                down += 1
                w.writerow([("%.3f" % time.time()), 0] + [""] * 13)
            fh.flush()

            if samples and samples % 30 == 0:
                el = (time.time() - t0) / 60.0
                print("  +%.1f min  samples=%d down=%d ber=%s" %
                      (el, samples, down, st["state"]["ber"] if ok else "-"))

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()

    dur = (time.time() - t0) / 60.0
    lines = []
    lines.append("# 长时运行稳定性（%.0f 分钟）" % dur)
    lines.append("")
    lines.append("| 指标 | 结果 |")
    lines.append("|---|---|")
    lines.append("| 采样次数 | %d |" % samples)
    lines.append("| 通信中断采样 | %d |" % down)
    lines.append("| 平均帧率 | %.1f Hz |" % (statistics.mean(fps_list) if fps_list else 0))
    lines.append("| 帧率范围 | %.1f – %.1f Hz |" % (
        (min(fps_list) if fps_list else 0), (max(fps_list) if fps_list else 0)))
    lines.append("| BER 范围 | %d – %d |" % (
        (min(ber_list) if ber_list else 0), (max(ber_list) if ber_list else 0)))
    lines.append("| 记录到的事件数 | %d |" % events)
    lines.append("| rx 停滞次数 | %d |" % rx_stall)
    lines.append("")
    lines.append("原始采样：`%s`" % args.out)
    report = "\n".join(lines)

    md = os.path.join(os.path.dirname(os.path.abspath(args.out)), "longrun.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
