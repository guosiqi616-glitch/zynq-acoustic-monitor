#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recorder.py — JSONL 录制，供回放与赛后分析。"""

from __future__ import annotations

import json
import os
import time


class Recorder:
    def __init__(self, path: str):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self.path = path
        self.f = open(path, "a", encoding="utf-8", buffering=1)

    def record(self, line: str, t_pc_ns: int) -> None:
        self.f.write(
            json.dumps(
                {"t_pc_ns": t_pc_ns, "line": line},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


def default_path(record_dir: str = "records") -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(record_dir, f"relay_{stamp}.jsonl")
