# -*- coding: utf-8 -*-
"""协议解析器单元测试（v1.0）。

覆盖：合法帧、哨兵值、越界、类型错误、字段数、注释/空行、超长、非 ASCII、扩展字段。
运行：python -m unittest discover -s host_software/tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.protocol import MAX_LINE, parse_line  # noqa: E402

T_OK = "T,1024,123456789,2537,5012,-812,-643,523,1,8,9,1"
I_OK = "I,1,0,ZYNQ7010,0.1.0,48000,512,2,0"
E_OK = "E,3,456789,-800,-700,900,850,3,1"
S_OK = "S,5,1000000,2,0,0"


class TestFrameShapes(unittest.TestCase):
    def test_t_frame_fields(self):
        p = parse_line(T_OK)
        self.assertTrue(p.ok)
        self.assertEqual(p.kind, "T")
        self.assertEqual(p.fields["seq"], 1024)
        self.assertEqual(p.fields["temp"], 2537)
        self.assertEqual(p.fields["db_low"], -812)
        self.assertEqual(p.fields["ber"], 523)
        self.assertEqual(p.fields["supp"], 1)

    def test_i_frame_fields(self):
        p = parse_line(I_OK)
        self.assertTrue(p.ok)
        self.assertEqual(p.fields["dev_id"], "ZYNQ7010")
        self.assertEqual(p.fields["fs_hz"], 48000)
        self.assertEqual(p.fields["bands"], 2)
        self.assertIsNone(p.warn)

    def test_e_and_s_frames(self):
        pe, ps = parse_line(E_OK), parse_line(S_OK)
        self.assertTrue(pe.ok and ps.ok)
        self.assertEqual(pe.fields["lock"], 3)
        self.assertEqual(pe.fields["thr"], 850)
        self.assertEqual(ps.fields["flags"], 2)

    def test_crlf_and_bytes_input(self):
        self.assertTrue(parse_line(T_OK + "\r\n").ok)
        self.assertTrue(parse_line((T_OK + "\r\n").encode("ascii")).ok)


class TestSentinels(unittest.TestCase):
    def test_missing_sensor_values(self):
        p = parse_line("T,1,1000,-32768,65535,-32768,-32768,65535,0,0,0,0")
        self.assertTrue(p.ok)
        self.assertEqual(p.fields["temp"], -32768)
        self.assertEqual(p.fields["hum"], 65535)
        self.assertEqual(p.fields["ber"], 65535)


class TestSkips(unittest.TestCase):
    def test_empty_and_comment(self):
        # 注释行同样要求可打印 ASCII（协议 R1 先于跳过规则生效），
        # 设备端打印的横幅都是 "# [INFO] ..." 这类纯 ASCII。
        for line in ("", "\r\n", "# boot banner", "#[RAW] 0 0000f1c4"):
            with self.subTest(line=line):
                p = parse_line(line)
                self.assertTrue(p.ok)
                self.assertEqual(p.kind, "skip")

    def test_non_ascii_comment_is_error(self):
        self.assertEqual(parse_line("# 中文注释").err, "E_BAD_ASCII")


class TestRejects(unittest.TestCase):
    def test_unknown_frame(self):
        self.assertEqual(parse_line("t,1,2,3").err, "E_UNKNOWN_FRAME")
        self.assertEqual(parse_line("X,1,2").err, "E_UNKNOWN_FRAME")

    def test_field_count(self):
        self.assertEqual(parse_line("T,1,2,3").err, "E_FIELD_COUNT")
        # 字段数够，但中间有空字段 —— 属于整数格式错误而非缺字段
        self.assertEqual(parse_line("T,1024,,2537,5012,-812,-643,523,1,8,9,1").err, "E_BAD_INT")

    def test_bad_integer(self):
        self.assertEqual(parse_line("T,1024,123,25.37,5012,-812,-643,523,1,8,9,1").err, "E_BAD_INT")
        self.assertEqual(parse_line("T,1024,abc,2537,5012,-812,-643,523,1,8,9,1").err, "E_BAD_INT")

    def test_range(self):
        # ber 上限 1000
        self.assertEqual(parse_line("T,1,2,2537,5012,-812,-643,1001,1,0,0,0").err, "E_RANGE")
        # 湿度负值
        self.assertEqual(parse_line("T,1,2,2537,-1,-812,-643,0,1,0,0,0").err, "E_RANGE")
        # state 越界
        self.assertEqual(parse_line("T,1,2,2537,5012,-812,-643,0,9,0,0,0").err, "E_RANGE")

    def test_too_long(self):
        line = "T," + "1" * (MAX_LINE + 10)
        self.assertEqual(parse_line(line).err, "E_TOO_LONG")

    def test_non_ascii(self):
        self.assertEqual(parse_line("T,1,2,温度,5012,-812,-643,0,1,0,0,0").err, "E_BAD_ASCII")
        self.assertEqual(parse_line(b"T,1,2,\xff\xfe").err, "E_BAD_ASCII")


class TestWarnings(unittest.TestCase):
    def test_extra_fields(self):
        p = parse_line(T_OK + ",0")
        self.assertTrue(p.ok)
        self.assertEqual(p.warn, "E_EXTRA_FIELDS")

    def test_unsupported_bands(self):
        p = parse_line("I,1,0,ZYNQ7010,0.1.0,48000,512,3,0")
        self.assertTrue(p.ok)
        self.assertEqual(p.warn, "E_UNSUPPORTED_BANDS")

    def test_zero_sample_rate(self):
        self.assertEqual(parse_line("I,1,0,ZYNQ7010,0.1.0,0,512,2,0").err, "E_RANGE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
