# 预留给 SHT30 传感器的 P2 接口管脚约束 (T10=SCL, T11=SDA)
set_property PACKAGE_PIN T10 [get_ports IIC_0_0_scl_io]
set_property IOSTANDARD LVCMOS33 [get_ports IIC_0_0_scl_io]
set_property PULLUP true [get_ports IIC_0_0_scl_io]

set_property PACKAGE_PIN T11 [get_ports IIC_0_0_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports IIC_0_0_sda_io]
set_property PULLUP true [get_ports IIC_0_0_sda_io]

# Audio / ES8388 PL pins
set_property -dict {PACKAGE_PIN U18 IOSTANDARD LVCMOS33} [get_ports sys_clk]
set_property -dict {PACKAGE_PIN N16 IOSTANDARD LVCMOS33} [get_ports sys_rst_n]
set_property -dict {PACKAGE_PIN E18 IOSTANDARD LVCMOS33 PULLUP true} [get_ports aud_scl]
set_property -dict {PACKAGE_PIN F17 IOSTANDARD LVCMOS33 PULLUP true} [get_ports aud_sda]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports aud_adcdat]
set_property -dict {PACKAGE_PIN L20 IOSTANDARD LVCMOS33} [get_ports aud_lrc]
set_property -dict {PACKAGE_PIN G17 IOSTANDARD LVCMOS33} [get_ports aud_dacdat]
set_property -dict {PACKAGE_PIN E19 IOSTANDARD LVCMOS33} [get_ports aud_mclk]
set_property -dict {PACKAGE_PIN M18 IOSTANDARD LVCMOS33} [get_ports aud_bclk]
create_clock -period 20.000 -name sys_clk [get_ports sys_clk]

# 注：aud_mclk / sys_clk 之间的异步时钟组约束放在 timing_cdc.xdc，
#     那个文件 used_in_synthesis = false，只在 implementation 阶段生效
#     （clk_out1 要等 clk_wiz 生成后 get_clocks 才拿得到）。

# 板载用户 LED（正点原子底板，与 10_flow_led 例程一致）
set_property -dict {PACKAGE_PIN H15 IOSTANDARD LVCMOS33} [get_ports led_alarm]
set_property -dict {PACKAGE_PIN L15 IOSTANDARD LVCMOS33} [get_ports led_ok]
