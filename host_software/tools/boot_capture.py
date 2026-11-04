#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓串口 12 秒原始输出，用于看启动日志。"""
import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
out = sys.argv[3] if len(sys.argv) > 3 else r"D:\Xilinx\S\project\project_1\records\boot_capture.txt"

s = serial.Serial(port, 115200, timeout=0.3)
end = time.time() + secs
buf = []
while time.time() < end:
    data = s.read(512)
    if data:
        buf.append(data.decode("ascii", errors="replace"))
s.close()
text = "".join(buf)
with open(out, "w", encoding="utf-8") as f:
    f.write(text)
print("captured %d chars -> %s" % (len(text), out))
