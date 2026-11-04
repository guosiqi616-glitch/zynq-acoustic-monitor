# =============================================================================
# timing_cdc.xdc
#
# aud_mclk 由 clk_wiz 从 sys_clk 生成（12.288 MHz，非整数分频），aud_bclk /
# aud_lrc 再在 fabric 里分频得到。这两个域之间的数据在 audio_loopback 里
# 只经过两级同步器（rx_done_s1/s2、lrc_s1/s2、adc_s1/s2），属于真正的
# 异步跨时钟域。
#
# 本文件 used_in_synthesis = false，只在 implementation 阶段读取：
# clk_out1_design_1_clk_wiz_0_0 这个时钟由 clk_wiz 生成，综合阶段用户 XDC
# 解析时还不存在；而且 implementation 阶段的 XDC 解析器不支持 if / puts，
# 所以这里只能写裸约束。
# =============================================================================

set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks clk_out1_design_1_clk_wiz_0_0]
