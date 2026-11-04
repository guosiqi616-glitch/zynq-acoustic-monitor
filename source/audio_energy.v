`timescale 1ns / 1ps

// ---------------------------------------------------------------------------
// Dual-band 31-tap FIR + 512-sample sliding-average energy.
//
// FIR：时分复用，每个 48 kHz 样本用 32 个 50 MHz 时钟算完 31 个抽头。
//
// 能量：512 点滑动平均（指数加权，逐样本更新）：
//     acc <= acc + y^2 - acc/512
//   稳态时 acc = 512 * mean(y^2)，和"512 点块求和"同一量纲；
//   但每来一个样本就更新一次（20.8 us），而不是每 512 点才更新一次。
//   不需要历史缓冲区，避免 BRAM 推断带来的不确定性。
//
// 数值：|y| <= ~40000 -> 钳到 16 位 -> 16x16 无符号平方；
//       稳态 512*65535^2 = 2.2e12 < 2^42，不会溢出。
// ---------------------------------------------------------------------------
module audio_energy(
    input  wire               clk,
    input  wire               rst_n,

    input  wire               sample_valid,
    input  wire signed [15:0] sample,

    output reg  [31:0]        energy_low,
    output reg  [31:0]        energy_high,
    output reg                energy_valid,
    output reg  [31:0]        sample_count
);

    reg signed [15:0] c_low [0:30];
    reg signed [15:0] c_high [0:30];

    initial begin
        c_low[0]  = -16'sd462;  c_low[1]  = -16'sd474;  c_low[2]  = -16'sd520;
        c_low[3]  = -16'sd632;  c_low[4]  = -16'sd838;  c_low[5]  = -16'sd1137;
        c_low[6]  = -16'sd1471; c_low[7]  = -16'sd1726; c_low[8]  = -16'sd1752;
        c_low[9]  = -16'sd1420; c_low[10] = -16'sd669;  c_low[11] =  16'sd445;
        c_low[12] =  16'sd1746; c_low[13] =  16'sd2974; c_low[14] =  16'sd3851;
        c_low[15] =  16'sd4169; c_low[16] =  16'sd3851; c_low[17] =  16'sd2974;
        c_low[18] =  16'sd1746; c_low[19] =  16'sd445;  c_low[20] = -16'sd669;
        c_low[21] = -16'sd1420; c_low[22] = -16'sd1752; c_low[23] = -16'sd1726;
        c_low[24] = -16'sd1471; c_low[25] = -16'sd1137; c_low[26] = -16'sd838;
        c_low[27] = -16'sd632;  c_low[28] = -16'sd520;  c_low[29] = -16'sd474;
        c_low[30] = -16'sd462;

        c_high[0]  =  16'sd3;    c_high[1]  = -16'sd113;  c_high[2]  =  16'sd3;
        c_high[3]  =  16'sd4;    c_high[4]  =  16'sd3;    c_high[5]  =  16'sd562;
        c_high[6]  =  16'sd3;    c_high[7]  = -16'sd1107; c_high[8]  =  16'sd3;
        c_high[9]  =  16'sd7;    c_high[10] =  16'sd3;    c_high[11] =  16'sd3824;
        c_high[12] =  16'sd3;    c_high[13] = -16'sd8660; c_high[14] =  16'sd3;
        c_high[15] =  16'sd10915;c_high[16] =  16'sd3;    c_high[17] = -16'sd8660;
        c_high[18] =  16'sd3;    c_high[19] =  16'sd3824; c_high[20] =  16'sd3;
        c_high[21] =  16'sd7;    c_high[22] =  16'sd3;    c_high[23] = -16'sd1107;
        c_high[24] =  16'sd3;    c_high[25] =  16'sd562;  c_high[26] =  16'sd3;
        c_high[27] =  16'sd4;    c_high[28] =  16'sd3;    c_high[29] = -16'sd113;
        c_high[30] =  16'sd3;
    end

    reg signed [15:0] delay_line [0:30];
    reg [4:0]  tap_idx;          // 0..30 抽头，31 = 收尾拍
    reg        busy;

    reg signed [47:0] acc_low;
    reg signed [47:0] acc_high;

    reg [41:0] ema_low;          // 滑动平均累加器
    reg [41:0] ema_high;

    // ---- FIR 抽头 -----------------------------------------------------------
    wire signed [15:0] tap_sample = delay_line[tap_idx];
    wire signed [15:0] tap_coef_l = c_low[tap_idx];
    wire signed [15:0] tap_coef_h = c_high[tap_idx];

    wire signed [31:0] mul_low  = tap_sample * tap_coef_l;
    wire signed [31:0] mul_high = tap_sample * tap_coef_h;

    wire signed [47:0] acc_low_next  = acc_low  + mul_low;
    wire signed [47:0] acc_high_next = acc_high + mul_high;

    wire signed [31:0] yl_final = acc_low  >>> 15;
    wire signed [31:0] yh_final = acc_high >>> 15;

    // ---- 平方（无符号，物理上有界）------------------------------------------
    wire [31:0] abs_l = yl_final[31] ? (~yl_final + 32'd1) : yl_final;
    wire [31:0] abs_h = yh_final[31] ? (~yh_final + 32'd1) : yh_final;

    wire [15:0] mag_l = (|abs_l[31:16]) ? 16'hFFFF : abs_l[15:0];
    wire [15:0] mag_h = (|abs_h[31:16]) ? 16'hFFFF : abs_h[15:0];

    wire [31:0] y2_low  = mag_l * mag_l;
    wire [31:0] y2_high = mag_h * mag_h;

    // ---- 滑动平均：acc + y2 - acc/512 --------------------------------------
    wire [41:0] ema_low_n  = ema_low  + {10'd0, y2_low}  - {9'd0, ema_low[41:9]};
    wire [41:0] ema_high_n = ema_high + {10'd0, y2_high} - {9'd0, ema_high[41:9]};

    wire sample_done = busy && (tap_idx == 5'd31);   // FIR 收尾那一拍

    integer i;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < 31; i = i + 1)
                delay_line[i] <= 16'sd0;

            tap_idx <= 5'd0;
            busy <= 1'b0;
            acc_low <= 48'sd0;
            acc_high <= 48'sd0;
            ema_low  <= 42'd0;
            ema_high <= 42'd0;

            energy_low <= 32'd0;
            energy_high <= 32'd0;
            energy_valid <= 1'b0;
            sample_count <= 32'd0;
        end
        else begin
            energy_valid <= 1'b0;

            if (sample_valid && !busy) begin
                for (i = 30; i > 0; i = i - 1)
                    delay_line[i] <= delay_line[i - 1];
                delay_line[0] <= sample;

                tap_idx <= 5'd0;
                busy <= 1'b1;
                acc_low <= 48'sd0;
                acc_high <= 48'sd0;
                sample_count <= sample_count + 32'd1;
            end
            else if (busy) begin
                if (tap_idx == 5'd30) begin
                    acc_low  <= acc_low_next;
                    acc_high <= acc_high_next;
                    tap_idx  <= 5'd31;
                end
                else if (tap_idx == 5'd31) begin
                    // 收尾拍：acc 已是完整卷积结果，做平方 + 滑动平均 + 输出
                    busy <= 1'b0;
                    ema_low  <= ema_low_n;
                    ema_high <= ema_high_n;
                    energy_low  <= {6'd0, ema_low_n[41:16]};
                    energy_high <= {6'd0, ema_high_n[41:16]};
                    energy_valid <= 1'b1;
                end
                else begin
                    tap_idx <= tap_idx + 5'd1;
                    acc_low  <= acc_low_next;
                    acc_high <= acc_high_next;
                end
            end
        end
    end

endmodule
