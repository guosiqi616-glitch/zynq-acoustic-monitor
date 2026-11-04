# -*- coding: utf-8 -*-
"""状态中枢（Hub）单元测试。

覆盖：状态更新、哨兵值映射、det=lock+supp 不变式、丢帧统计、
      设备重启（t_us 回退）导致的会话切换、事件帧入队。
运行：python -m unittest discover -s host_software/tests -v
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from relay.hub import Hub  # noqa: E402

# 每个测试用固定的“到达时间”，避免依赖真实时钟
T0 = 1_700_000_000_000_000_000


def t_frame(seq, t_us, temp=2537, hum=5012, db_low=-812, db_high=-643,
            ber=523, state=0, lock=0, det=0, supp=0):
    return f"T,{seq},{t_us},{temp},{hum},{db_low},{db_high},{ber},{state},{lock},{det},{supp}"


class HubCase(unittest.TestCase):
    def setUp(self):
        self.hub = Hub()
        self.hub.on_line("I,1,0,ZYNQ7010,0.1.0,48000,512,2,0", T0)

    def feed(self, line, dt_ns=20_000_000):
        self.hub.on_line(line, T0 + dt_ns)


class TestStateUpdate(HubCase):
    def test_telemetry_updates_state(self):
        self.feed(t_frame(0, 1_000_000))
        s = self.hub.state
        self.assertEqual(s.temp, 2537)
        self.assertEqual(s.db_low, -812)
        self.assertEqual(s.ber, 523)
        self.assertEqual(self.hub.rx, 1)

    def test_sentinels_become_none(self):
        self.feed(t_frame(0, 1_000_000, temp=-32768, hum=65535,
                          db_low=-32768, db_high=-32768, ber=65535))
        s = self.hub.state
        self.assertIsNone(s.temp)
        self.assertIsNone(s.hum)
        self.assertIsNone(s.db_low)
        self.assertIsNone(s.ber)

    def test_protocol_version_checked(self):
        self.hub.on_line("I,2,0,ZYNQ7010,0.1.0,48000,512,2,0", T0)
        self.assertFalse(self.hub.protocol_ok)
        self.assertTrue(self.hub.protocol_error)


class TestInvariant(HubCase):
    def test_det_equals_lock_plus_supp(self):
        self.feed(t_frame(0, 1_000_000, lock=8, det=9, supp=1))
        self.assertTrue(self.hub.invariant_ok)

    def test_invariant_violation_detected(self):
        self.feed(t_frame(0, 1_000_000, lock=2, det=5, supp=0))
        self.assertFalse(self.hub.invariant_ok)


class TestSequencing(HubCase):
    def test_lost_frames_counted(self):
        for seq, t in enumerate([1_000_000, 1_020_000, 1_040_000]):
            self.feed(t_frame(seq, t), dt_ns=(seq + 1) * 20_000_000)
        # 跳过 seq=3,4，直接发 5
        self.feed(t_frame(5, 1_060_000), dt_ns=80_000_000)
        self.assertEqual(self.hub.lost["T"], 2)

    def test_parse_errors_counted(self):
        self.feed("T,坏数据")
        self.assertEqual(sum(self.hub.errors.values()), 1)


class TestSession(HubCase):
    def test_reboot_detected_by_time_rewind(self):
        self.feed(t_frame(0, 10_000_000))
        before = self.hub.session
        # t_us 回退超过 1 s → 判定设备重启
        self.feed(t_frame(0, 1_000), dt_ns=40_000_000)
        self.assertGreater(self.hub.session, before)

    def test_monotonic_time_keeps_session(self):
        self.feed(t_frame(0, 1_000_000))
        before = self.hub.session
        self.feed(t_frame(1, 1_020_000))
        self.assertEqual(self.hub.session, before)


class TestEvents(HubCase):
    def test_event_enqueued_and_lock_updated(self):
        self.feed("E,0,1500000,-800,-700,950,850,1,1")
        self.assertEqual(len(self.hub.events), 1)
        ev = self.hub.events[0]
        self.assertEqual(ev["lock"], 1)
        self.assertEqual(ev["thr"], 850)
        self.assertEqual(self.hub.state.lock, 1)

    def test_status_frame_updates_error_counters(self):
        self.feed("S,0,2000000,2,7,3")
        self.assertEqual(self.hub.dev_err, 7)
        self.assertEqual(self.hub.ovf, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
