#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_experiment.py -- 三组判决规则在同一特征流上的离线对比

三组规则（全部在 10 Hz 的 X 帧上评估，同样要求"连续 3 帧确认"，保证公平）：

  A 固定能量阈值 : 10*log10(E_low+E_high) > EdB_th
                   （阈值按"安静白噪声 +6 dB"标定，相当于现场拍脑袋定一个数）
  B 固定 BER 阈值: ber > 850
  C 多维判决 v2  : score > chi2(2,0.001) 或 能量突增标志

用法：
    python tools/analyze_experiment.py --csv ../records/exp_run.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MANIFEST = os.path.join(ROOT, "test_audio", "_exp", "manifest.json")

CHI2_L2 = 1381          # score 显示单位（score*100/65536）
BER_FIXED = 850
BER_LOW = 650
CONFIRM = 3             # 10 Hz 下 == 300 ms
POD_WINDOW_S = 1.2


# --------------------------------------------------------------------------
def parse_csv(path):
    """返回 [(pc, line)]，保持时间顺序。"""
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            parts = raw.rstrip("\n").split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                pc = float(parts[0])
            except ValueError:
                continue
            rows.append((pc, parts[1]))
    return rows


def split_blocks(rows):
    """按 #PLAY 标记切分为 [(filename, t_play_pc, [(pc,line)])]。"""
    blocks = []
    cur = None
    for pc, line in rows:
        if line.startswith("#PLAY"):
            if cur is not None:
                blocks.append(cur)
            name = line.split("\t")[-1]
            cur = [name, pc, []]
        elif cur is not None:
            cur[2].append((pc, line))
    if cur is not None:
        blocks.append(cur)
    return blocks


def parse_frames(lines):
    xs, ts = [], []
    for pc, line in lines:
        if line.startswith("X,"):
            p = line.split(",")
            if len(p) < 16:
                continue
            try:
                xs.append({
                    "t": int(p[2]) / 1e6,
                    "level": int(p[3]),
                    "score": int(p[4]),
                    "ber": int(p[5]),
                    "mu": int(p[9]),
                    "sd": int(p[10]),
                    "flags": int(p[13]),
                })
            except ValueError:
                continue
        elif line.startswith("T,"):
            p = line.split(",")
            if len(p) < 12:
                continue
            try:
                db_l = int(p[5])
                db_h = int(p[6])
            except ValueError:
                continue
            if db_l <= -32768 + 1 or db_h <= -32768 + 1:
                continue
            e_l = 10.0 ** ((db_l + 1000) / 100.0)
            e_h = 10.0 ** ((db_h + 1000) / 100.0)
            ts.append({"t": int(p[2]) / 1e6,
                       "db": 10.0 * math.log10(max(e_l + e_h, 1e-9))})
    xs.sort(key=lambda r: r["t"])
    ts.sort(key=lambda r: r["t"])
    return xs, ts


def nearest_db(ts, t):
    if not ts:
        return None
    lo, hi = 0, len(ts) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid]["t"] < t:
            lo = mid + 1
        else:
            hi = mid
    best = ts[lo]
    if lo > 0:
        if abs(ts[lo - 1]["t"] - t) < abs(best["t"] - t):
            best = ts[lo - 1]
    return best["db"] if abs(best["t"] - t) < 0.15 else None


def confirm(flags):
    out = []
    run = 0
    for v in flags:
        run = run + 1 if v else 0
        out.append(run >= CONFIRM)
    return out


def onsets(conf, times):
    """确认状态的上升沿时刻列表。"""
    res = []
    prev = False
    for c, t in zip(conf, times):
        if c and not prev:
            res.append(t)
        prev = c
    return res


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "records", "exp_run.csv"))
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--outdir", default=os.path.join(ROOT, "records"))
    args = ap.parse_args()

    with open(args.manifest, "r", encoding="utf-8") as f:
        man = json.load(f)
    finfo = {f["name"]: f for f in man["files"]}

    rows = parse_csv(args.csv)
    blocks = split_blocks(rows)
    print("blocks: %d" % len(blocks))

    # ---- 第一遍：解析 + 计算时间偏移 ----
    parsed = []
    for name, t_play, lines in blocks:
        xs, ts = parse_frames(lines)
        if not xs or not ts:
            print("  [skip] %s: no frames" % name)
            continue
        # 时间基准：必须用"文件起始后的第一帧"，不能用整块中位数
        # （中位数会落在文件中间，等于把窗口整体平移半个文件长度）
        dev0 = xs[0]["t"]
        parsed.append({"name": name, "xs": xs, "ts": ts, "dev0": dev0})
        print("  %-24s X=%4d T=%4d" % (name, len(xs), len(ts)))

    # ---- 能量阈值标定：安静白噪声 FAR 窗中位数 + 6 dB ----
    edb_th = None
    for b in parsed:
        if b["name"] == "exp_quiet_white.wav":
            vals = []
            for x in b["xs"]:
                if man["far_start_s"] <= (x["t"] - b["dev0"]) <= man["far_end_s"]:
                    d = nearest_db(b["ts"], x["t"])
                    if d is not None:
                        vals.append(d)
            if vals:
                edb_th = statistics.median(vals) + 6.0
    if edb_th is None:
        edb_th = 40.0
    print("EdB threshold = %.1f dB" % edb_th)

    # ---- 逐条件评估 ----
    results = []
    for b in parsed:
        f = finfo[b["name"]]
        xs = b["xs"]
        times = [x["t"] for x in xs]

        db = [nearest_db(b["ts"], x["t"]) for x in xs]
        raw_a = [(d is not None and d > edb_th) for d in db]
        raw_b = [x["ber"] > BER_FIXED for x in xs]
        raw_b2 = [(x["ber"] > BER_FIXED) or (x["ber"] < BER_LOW) for x in xs]
        raw_c = [(x["score"] > CHI2_L2) or (x["flags"] & 0x10) for x in xs]

        conf = {"A": confirm(raw_a), "B": confirm(raw_b),
                "B2": confirm(raw_b2), "C": confirm(raw_c)}

        # FAR 窗口（相对文件起始）
        t0 = man["far_start_s"] + b["dev0"]
        t1 = man["far_end_s"] + b["dev0"]
        far = {}
        duty = {}
        for k, c in conf.items():
            ons = [t for t in onsets(c, times) if t0 <= t <= t1]
            far[k] = len(ons) * 60.0 / (man["far_end_s"] - man["far_start_s"])
            idx = [i for i, t in enumerate(times) if t0 <= t <= t1]
            duty[k] = (100.0 * sum(1 for i in idx if c[i]) / len(idx)) if idx else 0.0

        # POD
        hit = {"A": 0, "B": 0, "B2": 0, "C": 0}
        tot = 0
        per_sig = {}
        for seg in f["segments"]:
            s0 = seg["t0"] + b["dev0"]
            s1 = s0 + POD_WINDOW_S
            tot += 1
            got = {}
            for k, c in conf.items():
                ok = any(c[i] for i, t in enumerate(times) if s0 <= t <= s1)
                got[k] = ok
                if ok:
                    hit[k] += 1
            key = seg["signal"]
            per_sig.setdefault(key, {"n": 0, "A": 0, "B": 0, "B2": 0, "C": 0})
            per_sig[key]["n"] += 1
            for k in "ABC":
                if got[k]:
                    per_sig[key][k] += 1

        results.append({
            "name": b["name"], "noise": f["noise"], "level": f["level_name"],
            "n": tot,
            "pod": {k: 100.0 * hit[k] / tot if tot else 0.0
                    for k in ("A", "B", "B2", "C")},
            "far": far,
            "duty": duty,
            "per_sig": per_sig,
            "ber_med": statistics.median([x["ber"] for x in xs]),
            "mu_med": statistics.median([x["mu"] for x in xs]),
            "sd_med": statistics.median([x["sd"] for x in xs]),
        })

    # ---- 输出 ----
    os.makedirs(args.outdir, exist_ok=True)
    out_csv = os.path.join(args.outdir, "exp_results.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["condition", "noise", "level", "trials",
                    "POD_A", "POD_B", "POD_B2", "POD_C",
                    "DUTY_A", "DUTY_B", "DUTY_B2", "DUTY_C",
                    "ONSETS_A_permin", "ONSETS_B_permin",
                    "ONSETS_B2_permin", "ONSETS_C_permin",
                    "ber_median", "mu_median", "sd_median"])
        for r in results:
            w.writerow([r["name"], r["noise"], r["level"], r["n"],
                        "%.1f" % r["pod"]["A"], "%.1f" % r["pod"]["B"],
                        "%.1f" % r["pod"]["B2"], "%.1f" % r["pod"]["C"],
                        "%.1f" % r["duty"]["A"], "%.1f" % r["duty"]["B"],
                        "%.1f" % r["duty"]["B2"], "%.1f" % r["duty"]["C"],
                        "%.1f" % r["far"]["A"], "%.1f" % r["far"]["B"],
                        "%.1f" % r["far"]["B2"], "%.1f" % r["far"]["C"],
                        r["ber_med"], r["mu_med"], r["sd_med"]])

    md = []
    md.append("## 检出率 POD（异常窗口内是否触发）\n")
    md.append("| 条件 | 噪声 | 强度 | 试验 | A 能量阈值 | B 单侧固定BER | B2 双侧固定BER | C 多维判决 |")
    md.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        md.append("| %s | %s | %s | %d | %.1f%% | %.1f%% | %.1f%% | %.1f%% |" % (
            r["name"].replace("exp_", "").replace(".wav", ""),
            r["noise"], r["level"], r["n"],
            r["pod"]["A"], r["pod"]["B"], r["pod"]["B2"], r["pod"]["C"]))

    md.append("\n## 纯噪声段告警占空比（常亮 = 判据失效）\n")
    md.append("| 条件 | BER 中位 | A | B | B2 | C |")
    md.append("|---|---|---|---|---|---|")
    for r in results:
        md.append("| %s | %d | %.1f%% | %.1f%% | %.1f%% | %.1f%% |" % (
            r["name"].replace("exp_", "").replace(".wav", ""),
            r["ber_med"],
            r["duty"]["A"], r["duty"]["B"], r["duty"]["B2"], r["duty"]["C"]))

    md.append("\n> 占空比 = 纯噪声 20 s 窗口内处于「确认告警」状态的帧占比。")
    md.append("> 超过 50% 说明判据在背景噪声下恒为真，此时的 POD 没有意义。\n")
    table = "\n".join(md)
    with open(os.path.join(args.outdir, "exp_results.md"), "w", encoding="utf-8") as f:
        f.write("# 三组判决规则对比（能量阈值 %.1f dB）\n\n" % edb_th)
        f.write(table + "\n")

    print()
    print(table)
    print()
    print("csv ->", out_csv)

    # ---- 分信号明细（C 组） ----
    print("\nC 组分信号检出率：")
    for r in results:
        parts = []
        for sig in ["squeak", "sweep", "gear", "impact"]:
            d = r["per_sig"].get(sig)
            if d:
                parts.append("%s %d/%d" % (sig, d["C"], d["n"]))
        print("  %-24s %s" % (r["name"].replace("exp_", ""), "  ".join(parts)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
