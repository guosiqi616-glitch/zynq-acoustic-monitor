//Copyright 1986-2020 Xilinx, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2020.2 (win64) Build 3064766 Wed Nov 18 09:12:45 MST 2020
//Date        : Thu Oct 29 17:46:14 2026
//Host        : LAPTOP-ATCMBB92 running 64-bit major release  (build 9200)
//Command     : generate_target design_1_wrapper.bd
//Design      : design_1_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module design_1_wrapper
   (DDR_addr,
    DDR_ba,
    DDR_cas_n,
    DDR_ck_n,
    DDR_ck_p,
    DDR_cke,
    DDR_cs_n,
    DDR_dm,
    DDR_dq,
    DDR_dqs_n,
    DDR_dqs_p,
    DDR_odt,
    DDR_ras_n,
    DDR_reset_n,
    DDR_we_n,
    FIXED_IO_ddr_vrn,
    FIXED_IO_ddr_vrp,
    FIXED_IO_mio,
    FIXED_IO_ps_clk,
    FIXED_IO_ps_porb,
    FIXED_IO_ps_srstb,
    IIC_0_0_scl_io,
    IIC_0_0_sda_io,
    aud_adcdat,
    aud_bclk,
    aud_dacdat,
    aud_lrc,
    aud_mclk,
    aud_scl,
    aud_sda,
    led_alarm,
    led_ok,
    sys_clk,
    sys_rst_n);
  inout [14:0]DDR_addr;
  inout [2:0]DDR_ba;
  inout DDR_cas_n;
  inout DDR_ck_n;
  inout DDR_ck_p;
  inout DDR_cke;
  inout DDR_cs_n;
  inout [3:0]DDR_dm;
  inout [31:0]DDR_dq;
  inout [3:0]DDR_dqs_n;
  inout [3:0]DDR_dqs_p;
  inout DDR_odt;
  inout DDR_ras_n;
  inout DDR_reset_n;
  inout DDR_we_n;
  inout FIXED_IO_ddr_vrn;
  inout FIXED_IO_ddr_vrp;
  inout [53:0]FIXED_IO_mio;
  inout FIXED_IO_ps_clk;
  inout FIXED_IO_ps_porb;
  inout FIXED_IO_ps_srstb;
  inout IIC_0_0_scl_io;
  inout IIC_0_0_sda_io;
  input aud_adcdat;
  output aud_bclk;
  output aud_dacdat;
  output aud_lrc;
  output aud_mclk;
  output aud_scl;
  inout aud_sda;
  output led_alarm;
  output led_ok;
  input sys_clk;
  input sys_rst_n;

  wire [14:0]DDR_addr;
  wire [2:0]DDR_ba;
  wire DDR_cas_n;
  wire DDR_ck_n;
  wire DDR_ck_p;
  wire DDR_cke;
  wire DDR_cs_n;
  wire [3:0]DDR_dm;
  wire [31:0]DDR_dq;
  wire [3:0]DDR_dqs_n;
  wire [3:0]DDR_dqs_p;
  wire DDR_odt;
  wire DDR_ras_n;
  wire DDR_reset_n;
  wire DDR_we_n;
  wire FIXED_IO_ddr_vrn;
  wire FIXED_IO_ddr_vrp;
  wire [53:0]FIXED_IO_mio;
  wire FIXED_IO_ps_clk;
  wire FIXED_IO_ps_porb;
  wire FIXED_IO_ps_srstb;
  wire IIC_0_0_scl_i;
  wire IIC_0_0_scl_io;
  wire IIC_0_0_scl_o;
  wire IIC_0_0_scl_t;
  wire IIC_0_0_sda_i;
  wire IIC_0_0_sda_io;
  wire IIC_0_0_sda_o;
  wire IIC_0_0_sda_t;
  wire aud_adcdat;
  wire aud_bclk;
  wire aud_dacdat;
  wire aud_lrc;
  wire aud_mclk;
  wire aud_scl;
  wire aud_sda;
  wire [31:0]audio_energy_high;
  wire [31:0]audio_energy_low;
  wire [31:0]gpio_energy_high;
  wire [31:0]gpio_energy_low;
  wire [31:0]gpio_ctrl_o;
  wire [31:0]anomaly_status;
  wire        kurt_hit;
  wire [15:0] kurt_peak;
  wire [3:0]  kurt_lvl;
  wire sys_clk;
  wire sys_rst_n;

  IOBUF IIC_0_0_scl_iobuf
       (.I(IIC_0_0_scl_o),
        .IO(IIC_0_0_scl_io),
        .O(IIC_0_0_scl_i),
        .T(IIC_0_0_scl_t));
  IOBUF IIC_0_0_sda_iobuf
       (.I(IIC_0_0_sda_o),
        .IO(IIC_0_0_sda_io),
        .O(IIC_0_0_sda_i),
        .T(IIC_0_0_sda_t));
  design_1 design_1_i
       (.DDR_addr(DDR_addr),
        .DDR_ba(DDR_ba),
        .DDR_cas_n(DDR_cas_n),
        .DDR_ck_n(DDR_ck_n),
        .DDR_ck_p(DDR_ck_p),
        .DDR_cke(DDR_cke),
        .DDR_cs_n(DDR_cs_n),
        .DDR_dm(DDR_dm),
        .DDR_dq(DDR_dq),
        .DDR_dqs_n(DDR_dqs_n),
        .DDR_dqs_p(DDR_dqs_p),
        .DDR_odt(DDR_odt),
        .DDR_ras_n(DDR_ras_n),
        .DDR_reset_n(DDR_reset_n),
        .DDR_we_n(DDR_we_n),
        .FIXED_IO_ddr_vrn(FIXED_IO_ddr_vrn),
        .FIXED_IO_ddr_vrp(FIXED_IO_ddr_vrp),
        .FIXED_IO_mio(FIXED_IO_mio),
        .FIXED_IO_ps_clk(FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb(FIXED_IO_ps_porb),
        .FIXED_IO_ps_srstb(FIXED_IO_ps_srstb),
        .IIC_0_0_scl_i(IIC_0_0_scl_i),
        .IIC_0_0_scl_o(IIC_0_0_scl_o),
        .IIC_0_0_scl_t(IIC_0_0_scl_t),
        .IIC_0_0_sda_i(IIC_0_0_sda_i),
        .IIC_0_0_sda_o(IIC_0_0_sda_o),
        .IIC_0_0_sda_t(IIC_0_0_sda_t),
        .aud_adcdat(aud_adcdat),
        .aud_bclk(aud_bclk),
        .aud_dacdat(aud_dacdat),
        .aud_lrc(aud_lrc),
        .aud_mclk(aud_mclk),
        .aud_scl(aud_scl),
        .aud_sda(aud_sda),
        .audio_energy_high(audio_energy_high),
        .audio_energy_low(audio_energy_low),
        .gpio_ctrl_o(gpio_ctrl_o),
        .gpio_energy_high(gpio_energy_high),
        .gpio_energy_low(gpio_energy_low),
        .gpio_status_i(anomaly_status),
        .sys_clk(sys_clk),
        .sys_rst_n(sys_rst_n));
  assign gpio_energy_low  = audio_energy_low;
  assign gpio_energy_high = audio_energy_high;

  // -------------------------------------------------------------------------
  // 频带能量比 + 滞回 + 事件锁存（PL 侧）
  //   gpio_ctrl_o  : AXI GPIO1 ch2 输出 -> 阈值/清锁存/使能
  //   anomaly_status: 事件计数/锁存标志 -> AXI GPIO1 ch1 输入
  // -------------------------------------------------------------------------
  anomaly_detect u_anomaly_detect (
      .clk         (sys_clk),
      .rst_n       (sys_rst_n),
      .energy_low  (audio_energy_low),
      .energy_high (audio_energy_high),
      .ctrl        (gpio_ctrl_o),
      .status      (anomaly_status)
  );

  // -------------------------------------------------------------------------
  // 板载 LED
  //   L15 (led_ok)    : 心跳，约 1.5 Hz 慢闪，证明 PL 在跑
  //   H15 (led_alarm) : 报警灯，平时灭，事件锁存（status[16]）时约 6 Hz 快闪
  // 完全不经过 PS —— 即使 PS 卡死或串口断了，灯照常工作
  // -------------------------------------------------------------------------
  reg [24:0] led_div;

  always @(posedge sys_clk or negedge sys_rst_n) begin
      if (!sys_rst_n)
          led_div <= 25'd0;
      else
          led_div <= led_div + 25'd1;
  end

  assign led_ok = led_div[24];                                     // 50MHz/2^25 ≈ 1.5 Hz

  // 报警保持：锁存期间不断续期，解除后再闪 3 s（与网页弹窗的 4 s 基本对齐）
  localparam [27:0] HOLD_TICKS = 28'd150_000_000;                  // 3 s @ 50 MHz

  reg [27:0] hold_cnt;

  always @(posedge sys_clk or negedge sys_rst_n) begin
      if (!sys_rst_n)
          hold_cnt <= 28'd0;
      else if (anomaly_status[16])
          hold_cnt <= HOLD_TICKS;
      else if (hold_cnt != 28'd0)
          hold_cnt <= hold_cnt - 28'd1;
  end

  wire alarm_on = anomaly_status[16] | (hold_cnt != 28'd0);

  // 本板 LED 高电平点亮：平时输出 0（灭），报警时输出 6 Hz 方波
  assign led_alarm = alarm_on ? led_div[22] : 1'b0;
endmodule
