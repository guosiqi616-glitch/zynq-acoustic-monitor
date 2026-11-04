#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_results.py -- 把 exp_results.csv 画成可直接插进申报书的 SVG 图

不依赖 matplotlib，手写 SVG，避免额外安装。
输出：records/exp_results.svg
"""

from __future__ import annotations

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

W, H = 980, 760
PAD_L, PAD_R, PAD_T, PAD_B = 70, 30, 60, 60

ALGOS = [("POD_A", "A 固定能量阈值", "#8fb3d9"),
         ("POD_B", "B 单侧固定BER", "#4f81bd"),
         ("POD_B2", "B2 双侧固定BER", "#2e5f8a"),
         ("POD_C", "C 多维判决", "#c0504d")]
DUTY = [("DUTY_A", "A", "#8fb3d9"),
        ("DUTY_B", "B", "#4f81bd"),
        ("DUTY_B2", "B2", "#2e5f8a"),
        ("DUTY_C", "C", "#c0504d")]


def load(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def panel(x0, y0, w, h, rows, cols, title, ymax, unit):
    out = []
    out.append('<text x="%.1f" y="%.1f" font-size="16" font-weight="bold" '
               'fill="#222">%s</text>' % (x0, y0 - 14, esc(title)))
    # 轴
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999"/>'
               % (x0, y0, x0, y0 + h))
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999"/>'
               % (x0, y0 + h, x0 + w, y0 + h))
    for k in range(0, 5):
        v = ymax * k / 4.0
        yy = y0 + h - h * v / ymax
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#eee"/>'
                   % (x0, yy, x0 + w, yy))
        out.append('<text x="%.1f" y="%.1f" font-size="11" fill="#666" '
                   'text-anchor="end">%g</text>' % (x0 - 8, yy + 4, round(v, 1)))
    out.append('<text x="%.1f" y="%.1f" font-size="11" fill="#666">%s</text>'
               % (x0 - 46, y0 - 4, unit))

    n = len(rows)
    group_w = w / n
    bar_w = group_w * 0.8 / len(cols)
    for i, r in enumerate(rows):
        gx = x0 + group_w * i + group_w * 0.1
        for j, (key, label, color) in enumerate(cols):
            val = float(r[key])
            bh = h * min(val, ymax) / ymax
            bx = gx + bar_w * j
            by = y0 + h - bh
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                       'fill="%s"/>' % (bx, by, bar_w - 2, bh, color))
            if val > 0:
                out.append('<text x="%.1f" y="%.1f" font-size="9" fill="#444" '
                           'text-anchor="middle">%.0f</text>'
                           % (bx + bar_w / 2 - 1, by - 3, val))
        lab = r["condition"].replace("exp_", "").replace(".wav", "")
        out.append('<text x="%.1f" y="%.1f" font-size="11" fill="#333" '
                   'text-anchor="middle">%s</text>'
                   % (x0 + group_w * i + group_w / 2, y0 + h + 16, esc(lab)))
    return out


def main() -> None:
    src = os.path.join(ROOT, "records", "exp_results.csv")
    rows = load(src)

    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d" font-family="Microsoft YaHei, sans-serif">'
           % (W, H, W, H)]
    svg.append('<rect width="%d" height="%d" fill="#fff"/>' % (W, H))
    svg.append('<text x="%.1f" y="30" font-size="19" font-weight="bold" '
               'fill="#111">抗噪声对比实验：检出率与误报占空比</text>'
               % (W / 2 - 230))

    pw = W - PAD_L - PAD_R
    ph = (H - PAD_T - PAD_B - 120) / 2

    svg += panel(PAD_L, PAD_T + 20, pw, ph, rows, ALGOS,
                 "检出率 POD（%）", 100.0, "POD / %")
    svg += panel(PAD_L, PAD_T + 20 + ph + 100, pw, ph, rows, DUTY,
                 "纯噪声段告警占空比（%，100% = 判据常亮失效）", 100.0, "占空比 / %")

    # 图例
    lx, ly = PAD_L, H - 46
    for key, label, color in ALGOS:
        svg.append('<rect x="%.1f" y="%.1f" width="14" height="14" fill="%s"/>'
                   % (lx, ly, color))
        svg.append('<text x="%.1f" y="%.1f" font-size="12" fill="#333">%s</text>'
                   % (lx + 20, ly + 12, esc(label)))
        lx += 210

    svg.append("</svg>")
    out = os.path.join(ROOT, "records", "exp_results.svg")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))
    print("svg ->", out)


if __name__ == "__main__":
    main()
