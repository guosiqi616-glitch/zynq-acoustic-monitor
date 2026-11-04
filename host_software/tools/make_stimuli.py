#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_stimuli.py -- 生成抗噪声对比实验的测试素材

设计
----
背景噪声是自变量（类型 x 强度），异响是固定电平的"故障源"，
因此信噪比随背景噪声变化 —— 这更接近真实运维场景。

  噪声类型：white（白噪声，BER≈0.73） / hf（8-16k 为主，BER≈0.89）
  噪声强度：quiet 0.020 / mid 0.045 / loud 0.090 (RMS, 相对满幅)
  异响电平：固定 RMS 0.060  ->  SNR ≈ +9.5 / +2.5 / -3.5 dB

每个文件的结构：
  [60 s 噪声预热] [异响 0.8 s + 噪声 1.7 s] x 24

预热段用于让系统重建基线（持续告警 30 s 触发重建 + 2 s 收敛），
它的最后 20 s（38-58 s）被当作纯噪声段统计误报率。

输出：test_audio/_exp/*.wav + manifest.json
"""

from __future__ import annotations

import json
import os
import wave

import numpy as np

FS = 48000
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "..", "test_audio", "_exp")

WARMUP_S = 60.0
ANOM_S = 0.8
GAP_S = 1.7
TRIALS = 6

NOISE_LEVELS = {"quiet": 0.020, "mid": 0.045, "loud": 0.090}
NOISE_TYPES = ["white", "hf"]
HF_ALPHA = 0.65          # 高频噪声的功率占比，对应 BER≈0.89
AN_RMS = 0.060

SIGNALS = ["squeak", "sweep", "gear", "impact"]


# --------------------------------------------------------------------------
def _norm(x: np.ndarray, target_rms: float = 1.0) -> np.ndarray:
    r = float(np.sqrt(np.mean(x * x)))
    return x * (target_rms / r) if r > 0 else x


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / FS)
    spec[(f < lo) | (f > hi)] = 0.0
    return np.fft.irfft(spec, len(x))


def _fade(x: np.ndarray, ms: float = 5.0) -> np.ndarray:
    n = int(FS * ms / 1000.0)
    if n <= 0 or len(x) < 2 * n:
        return x
    w = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n))
    y = x.copy()
    y[:n] *= w
    y[-n:] *= w[::-1]
    return y


def make_noise(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    w = rng.standard_normal(n)
    if kind == "white":
        return _norm(w)
    h = _bandpass(w, 8000.0, 16000.0)
    mix = np.sqrt(1.0 - HF_ALPHA) * _norm(w) + np.sqrt(HF_ALPHA) * _norm(h)
    return _norm(mix)


def make_signal(name: str, n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / FS
    if name == "squeak":
        am = 0.7 + 0.3 * np.sin(2 * np.pi * 8.0 * t)
        x = np.sin(2 * np.pi * 13000.0 * t) * am
    elif name == "sweep":
        f0, f1 = 12000.0, 16000.0
        phase = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) / t[-1] * t * t)
        x = np.sin(phase)
    elif name == "gear":
        x = np.zeros(n)
        period = int(FS / 250.0)          # 250 Hz 啮合
        ring = np.exp(-np.arange(int(0.012 * FS)) / (0.0025 * FS))
        burst = ring * np.sin(2 * np.pi * 3000.0 * np.arange(len(ring)) / FS)
        for k in range(0, n - len(burst), period):
            x[k:k + len(burst)] += burst * (1.0 + 0.3 * rng.standard_normal())
    elif name == "impact":
        # 连串冲击（打齿/松动），间隔 60 ms：既保留冲击性，
        # 又保证 10 Hz 诊断帧能连续采到，否则会被"3 帧确认"直接滤掉
        x = np.zeros(n)
        for k in range(10):
            pos = int(k * 0.060 * FS)
            if pos >= n:
                break
            m = min(int(0.06 * FS), n - pos)
            tt = np.arange(m) / FS
            hit = np.exp(-tt / 0.008) * np.sin(2 * np.pi * 2500.0 * tt)
            click = rng.standard_normal(min(20, m)) * 0.6
            hit[:len(click)] += click
            x[pos:pos + m] += hit
    else:
        raise ValueError(name)

    # 软限幅把波峰因子压到 ~3，避免混音后整体被缩放、破坏各条件之间的相对电平
    x = np.tanh(x / (3.0 * AN_RMS)) * (3.0 * AN_RMS)
    return _fade(_norm(x, AN_RMS))


# --------------------------------------------------------------------------
def build_file(ntype: str, level: float, rng: np.random.Generator):
    warm_n = int(WARMUP_S * FS)
    anom_n = int(ANOM_S * FS)
    gap_n = int(GAP_S * FS)
    trial_n = anom_n + gap_n

    total = warm_n + trial_n * len(SIGNALS) * TRIALS
    buf = np.zeros(total)
    buf[:warm_n] = make_noise(ntype, warm_n, rng) * level

    segments = []
    pos = warm_n
    for sig in SIGNALS:
        for _ in range(TRIALS):
            buf[pos:pos + anom_n] += make_signal(sig, anom_n, rng)
            buf[pos:pos + trial_n] += make_noise(ntype, trial_n, rng) * level
            segments.append({
                "signal": sig,
                "t0": round(pos / FS, 4),
                "t1": round((pos + anom_n) / FS, 4),
            })
            pos += trial_n

    return buf, segments


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.default_rng(20261102)

    built = []
    peak = 0.0
    for ntype in NOISE_TYPES:
        for lname, level in NOISE_LEVELS.items():
            buf, segments = build_file(ntype, level, rng)
            peak = max(peak, float(np.max(np.abs(buf))))
            built.append({
                "name": "exp_%s_%s.wav" % (lname, ntype),
                "noise": ntype,
                "level_name": lname,
                "level_rms": level,
                "buf": buf,
                "segments": segments,
            })

    # 全局统一缩放，保持各条件之间的相对电平
    scale = min(1.0, 0.95 / peak) if peak > 0 else 1.0

    manifest = {
        "fs": FS,
        "warmup_s": WARMUP_S,
        "anom_s": ANOM_S,
        "gap_s": GAP_S,
        "trials_per_signal": TRIALS,
        "an_rms": AN_RMS * scale,
        "noise_levels": {k: v * scale for k, v in NOISE_LEVELS.items()},
        "hf_alpha": HF_ALPHA,
        "scale": scale,
        "far_start_s": 38.0,
        "far_end_s": 58.0,
        "files": [],
    }

    for item in built:
        data = item["buf"] * scale
        pcm = np.clip(data * 32767.0, -32768, 32767).astype("<i2")
        path = os.path.join(OUT_DIR, item["name"])
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(FS)
            w.writeframes(pcm.tobytes())

        manifest["files"].append({
            "name": item["name"],
            "path": path,
            "noise": item["noise"],
            "level_name": item["level_name"],
            "level_rms": round(item["level_rms"] * scale, 6),
            "duration_s": round(len(data) / FS, 3),
            "segments": item["segments"],
        })
        print("%-26s %5.1f s  %d trials  peak=%.3f" %
              (item["name"], len(data) / FS, len(item["segments"]),
               float(np.max(np.abs(data)))))

    mpath = os.path.join(OUT_DIR, "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("scale=%.4f" % scale)
    print("manifest ->", mpath)


if __name__ == "__main__":
    main()
