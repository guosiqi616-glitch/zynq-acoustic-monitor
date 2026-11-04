module audio_loopback(
    input           sys_clk   ,
    input           sys_rst_n ,

    output          aud_lrc   ,
    output          aud_bclk  ,
    input           aud_adcdat,
    input           aud_mclk  ,
    output          aud_dacdat,

    output          aud_scl   ,
    inout           aud_sda   ,

    output          led,
    output [31:0]   energy_low,
    output [31:0]   energy_high
);

parameter WL = 6'd16;

slave_signal_gen u_slave_signal_gen(
    .clk      (aud_mclk),
    .rst_n    (sys_rst_n),
    .aud_bclk (aud_bclk),
    .aud_lrc  (aud_lrc)
);

wire [15:0] adc_data;
wire        rx_done;

audio_ctrl #(
    .WL (WL)
) u_audio_ctrl(
    .clk        (sys_clk),
    .rst_n      (sys_rst_n),
    .aud_bclk   (aud_bclk),
    .aud_lrc    (aud_lrc),
    .aud_adcdat (aud_adcdat),
    .aud_dacdat (aud_dacdat),
    .aud_scl    (aud_scl),
    .aud_sda    (aud_sda),
    .adc_data_dbg(adc_data),
    .rx_done_dbg (rx_done)
);

// ---------------------------------------------------------------------------
// CDC: aud_bclk domain  ->  sys_clk domain
//
// audio_receive 工作在 aud_bclk 域，rx_done / adc_data 直接进 sys_clk 域
// 会造成跨时钟域违例。这里用两级同步器把 rx_done / aud_lrc 搬到 sys_clk 域，
// 再用同步后的 rx_done 上升沿去锁存已经在数据线上稳定了十几微秒的 adc_data。
// ---------------------------------------------------------------------------
reg        rx_done_s1, rx_done_s2, rx_done_s3;
reg        lrc_s1, lrc_s2;
reg [15:0] adc_s1, adc_s2;

always @(posedge sys_clk or negedge sys_rst_n) begin
    if (!sys_rst_n) begin
        rx_done_s1 <= 1'b0; rx_done_s2 <= 1'b0; rx_done_s3 <= 1'b0;
        lrc_s1     <= 1'b0; lrc_s2     <= 1'b0;
        adc_s1     <= 16'd0; adc_s2    <= 16'd0;
    end
    else begin
        rx_done_s1 <= rx_done;    rx_done_s2 <= rx_done_s1; rx_done_s3 <= rx_done_s2;
        lrc_s1     <= aud_lrc;    lrc_s2     <= lrc_s1;
        adc_s1     <= adc_data;   adc_s2     <= adc_s1;
    end
end

wire rx_rise  = rx_done_s2 & ~rx_done_s3;   // 同步后的 rx_done 上升沿

// ---------------------------------------------------------------------------
// 左右声道都收，每帧末尾取幅度更大的那个送去算能量。
// 原因：3.5mm 插头如果 OUT 接在环(R)而不是尖端(T)，只取左声道就完全收不到；
// 取两路里幅度大的那一路，接尖端或接环都能工作。
// ---------------------------------------------------------------------------
reg signed [15:0] samp_l;
reg signed [15:0] samp_pick;
reg               pick_valid;

wire [15:0] abs_l = samp_l[15]  ? (~samp_l  + 16'd1) : samp_l;
wire [15:0] abs_r = adc_s2[15]  ? (~adc_s2  + 16'd1) : adc_s2;

always @(posedge sys_clk or negedge sys_rst_n) begin
    if (!sys_rst_n) begin
        samp_l     <= 16'sd0;
        samp_pick  <= 16'sd0;
        pick_valid <= 1'b0;
    end
    else begin
        pick_valid <= 1'b0;
        if (rx_rise) begin
            if (~lrc_s2) begin
                samp_l <= adc_s2;                 // 左槽：先存着
            end
            else begin
                samp_pick  <= (abs_l > abs_r) ? samp_l : adc_s2;
                pick_valid <= 1'b1;               // 右槽结束 = 一帧收完
            end
        end
    end
end

audio_energy u_audio_energy(
    .clk          (sys_clk),
    .rst_n        (sys_rst_n),
    .sample_valid (pick_valid),
    .sample       (samp_pick),
    .energy_low   (energy_low),
    .energy_high  (energy_high),
    .energy_valid (),
    .sample_count ()
);

// audio MCLK activity indicator
reg [23:0] led_cnt;

always @(posedge aud_mclk or negedge sys_rst_n) begin
    if (!sys_rst_n)
        led_cnt <= 24'd0;
    else
        led_cnt <= led_cnt + 24'd1;
end

assign led = led_cnt[23];

endmodule
