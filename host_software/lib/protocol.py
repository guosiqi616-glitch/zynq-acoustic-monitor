#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
protocol.py — 设备—上位机协议 v1.0 的共享解析器

被 tools/conformance.py、relay/hub.py、tools/ws_probe.py 共用。
只做“一行文本 -> 结构化字段”，不依赖任何硬件或网络库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PROTO_MAJOR = 1
PROTO_MINOR = 1
MAX_LINE = 256
FRAME_PARAMS = {"I": 8, "T": 11, "E": 8, "S": 5, "X": 15}

DEC_RE = re.compile(r"^-?[0-9]+$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,16}$")

U32_MAX = 0xFFFFFFFF
U16_MAX = 0xFFFF
U8_MAX = 0xFF
U64_MAX = (1 << 63) - 1


class PErr(Exception):
    """解析错误，携带协议错误码。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Parsed:
    ok: bool
    kind: str = ""
    err: Optional[str] = None
    warn: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)
    raw: str = ""


def _dec(tok: str) -> int:
    if not DEC_RE.match(tok):
        raise PErr("E_BAD_INT")
    try:
        return int(tok, 10)
    except ValueError:
        raise PErr("E_BAD_INT")


def _u(tok: str, hi: int) -> int:
    v = _dec(tok)
    if v < 0 or v > hi:
        raise PErr("E_RANGE")
    return v


def _s(tok: str, lo: int, hi: int, sentinel: Optional[int] = None) -> int:
    v = _dec(tok)
    if sentinel is not None and v == sentinel:
        return v
    if v < lo or v > hi:
        raise PErr("E_RANGE")
    return v


def _special_u(tok: str, lo: int, hi: int, sentinel: int) -> int:
    v = _dec(tok)
    if v == sentinel:
        return v
    if v < lo or v > hi:
        raise PErr("E_RANGE")
    return v


def _parse_i(p: List[str]) -> Dict[str, Any]:
    major = _u(p[1], U16_MAX)
    minor = _u(p[2], U16_MAX)
    if not ID_RE.match(p[3]) or not ID_RE.match(p[4]):
        raise PErr("E_BAD_TOKEN")
    fs_hz = _u(p[5], U32_MAX)
    win = _u(p[6], U16_MAX)
    bands = _u(p[7], U8_MAX)
    caps = _u(p[8], U16_MAX)
    if fs_hz == 0 or win == 0:
        raise PErr("E_RANGE")
    return {
        "major": major,
        "minor": minor,
        "dev_id": p[3],
        "fw": p[4],
        "fs_hz": fs_hz,
        "win": win,
        "bands": bands,
        "caps": caps,
    }


def _parse_t(p: List[str]) -> Dict[str, Any]:
    return {
        "seq": _u(p[1], U32_MAX),
        "t_us": _u(p[2], U64_MAX),
        "temp": _s(p[3], -4000, 12500, -32768),
        "hum": _special_u(p[4], 0, 10000, 65535),
        "db_low": _s(p[5], -1200, 0, -32768),
        "db_high": _s(p[6], -1200, 0, -32768),
        "ber": _special_u(p[7], 0, 1000, 65535),
        "state": _special_u(p[8], 0, 2, 255),
        "lock": _u(p[9], U32_MAX),
        "det": _u(p[10], U32_MAX),
        "supp": _u(p[11], U32_MAX),
    }


def _parse_e(p: List[str]) -> Dict[str, Any]:
    return {
        "seq": _u(p[1], U32_MAX),
        "t_us": _u(p[2], U64_MAX),
        "db_low": _s(p[3], -1200, 0, -32768),
        "db_high": _s(p[4], -1200, 0, -32768),
        "ber": _special_u(p[5], 0, 1000, 65535),
        "thr": _special_u(p[6], 0, 1000, 65535),
        "lock": _u(p[7], U32_MAX),
        "reason": _special_u(p[8], 0, 2, 255),
    }


def _parse_s(p: List[str]) -> Dict[str, Any]:
    return {
        "seq": _u(p[1], U32_MAX),
        "t_us": _u(p[2], U64_MAX),
        "flags": _u(p[3], U16_MAX),
        "err_cnt": _u(p[4], U32_MAX),
        "ovf_cnt": _u(p[5], U32_MAX),
    }


def _parse_x(p: List[str]) -> Dict[str, Any]:
    """X 帧：扩展诊断（多维判决中间量），v1.1 新增，可被旧上位机忽略。"""
    return {
        "seq": _u(p[1], U32_MAX),
        "t_us": _u(p[2], U64_MAX),
        "level": _u(p[3], 3),
        "score": _u(p[4], 20000000),
        "ber": _u(p[5], 1000),
        "ber_res": _u(p[6], 1000),
        "kurt": _u(p[7], 15),
        "thr": _u(p[8], 1000),
        "mu_ber": _u(p[9], 1000),
        "sd_ber": _u(p[10], 1000),
        "nf_low": _u(p[11], U32_MAX),
        "nf_high": _u(p[12], U32_MAX),
        "flags": _u(p[13], U16_MAX),
        "ev_pl": _u(p[14], U16_MAX),
        "ev_ps": _u(p[15], U32_MAX),
    }


def parse_line(raw: Any) -> Parsed:
    """解析一行。永不抛异常；非法输入返回 ok=False + err。"""
    if isinstance(raw, bytes):
        try:
            line = raw.decode("ascii")
        except UnicodeDecodeError:
            return Parsed(False, err="E_BAD_ASCII", raw=repr(raw[:32]))
    else:
        line = str(raw)
        try:
            line.encode("ascii")
        except UnicodeEncodeError:
            return Parsed(False, err="E_BAD_ASCII", raw=line[:32])

    line = line.rstrip("\r\n")

    # R5：空行与注释在长度检查之前跳过
    if line == "" or line.startswith("#"):
        return Parsed(True, kind="skip", raw=line)

    # R6：正文长度限制
    if len(line.encode("ascii", errors="ignore")) > MAX_LINE:
        return Parsed(False, err="E_TOO_LONG", raw=line[:32])

    # R1：可打印 ASCII
    for ch in line:
        if ord(ch) < 0x20 or ord(ch) > 0x7E:
            return Parsed(False, err="E_BAD_ASCII", raw=line[:32])

    parts = line.split(",")
    ftype = parts[0]
    if ftype not in FRAME_PARAMS:
        return Parsed(False, err="E_UNKNOWN_FRAME", raw=line)

    need = FRAME_PARAMS[ftype] + 1
    if len(parts) < need:
        return Parsed(False, err="E_FIELD_COUNT", raw=line)

    warn = "E_EXTRA_FIELDS" if len(parts) > need else None

    try:
        if ftype == "I":
            fields = _parse_i(parts)
            if fields["bands"] != 2 and warn is None:
                warn = "E_UNSUPPORTED_BANDS"
        elif ftype == "T":
            fields = _parse_t(parts)
        elif ftype == "E":
            fields = _parse_e(parts)
        elif ftype == "X":
            fields = _parse_x(parts)
        else:
            fields = _parse_s(parts)
    except PErr as exc:
        return Parsed(False, err=exc.code, raw=line)

    return Parsed(True, kind=ftype, warn=warn, fields=fields, raw=line)


__all__ = [
    "PROTO_MAJOR",
    "PROTO_MINOR",
    "MAX_LINE",
    "FRAME_PARAMS",
    "Parsed",
    "PErr",
    "parse_line",
]
