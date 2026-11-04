`timescale 1ns / 1ps

// ---------------------------------------------------------------------------
// anomaly_detect -- 双判据异常检测 + 滞回 + 连续 N 次确认 + 事件锁存
//
// 判据 A（稳态高频占比）：ratio > T/1000
//   <=>  E_high * 1000 > T * (E_low + E_high)
//
// 判据 B（瞬态能量突增）：当前总能量 > 2 x 慢速参考，且比值 >= 0.5
//   slow_ref 是每 1 ms 更新的慢速跟踪（alpha=1/128，时间常数约 0.13 s）。
//   这条专门抓"安静环境下突然一声异响"——不依赖底噪的绝对比值，
//   所以即使环境底噪本身高频偏多也不会误判。
//
// 两条判据取"或"：A 抓持续的高频异常，B 抓瞬态冲击。
//
// ctrl  ：[15:0] 阈值千分比  [16] clear  [17] enable
// status：[15:0] 事件计数    [16] latched [17] above [18] enabled
// ---------------------------------------------------------------------------
module anomaly_detect #(
    parameter [18:0] STROBE_DIV  = 19'd49999,   // 50 MHz / 50000 = 1 kHz 判定节拍
    parameter [5:0]  CONFIRM_ON  = 6'd3,        // 连续 8 ms 满足才判定
    parameter [5:0]  CONFIRM_OFF = 6'd40,       // 连续 40 ms 不满足才解除
    parameter [31:0] GATE        = 32'd100000,    // 绝对能量下限（噪声门限）
    parameter [15:0] JUMP_RATIO  = 16'd500      // 判据 B 要求的最低比值（千分比）
)(
    input  wire        clk,
    input  wire        rst_n,

    input  wire [31:0] energy_low,
    input  wire [31:0] energy_high,

    input  wire        kurt_i,      // 峭度超限（来自 kurtosis_monitor）
    input  wire [3:0]  kurt_lvl,    // 近似 log2(峭度)，仅用于观测

    input  wire [31:0] ctrl,
    output reg  [31:0] status,
    output reg         event_irq     // 新事件锁存时输出单周期脉冲
);

    // ---- 判定节拍 ----------------------------------------------------------
    reg [18:0] div_cnt;
    reg        strobe;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt <= 19'd0;
            strobe  <= 1'b0;
        end
        else if (div_cnt == STROBE_DIV) begin
            div_cnt <= 19'd0;
            strobe  <= 1'b1;
        end
        else begin
            div_cnt <= div_cnt + 19'd1;
            strobe  <= 1'b0;
        end
    end

    // ---- 判据 A：比值 ------------------------------------------------------
    wire [15:0] threshold = ctrl[15:0];
    wire        clear_ev  = ctrl[16];
    wire        enable_ev = ctrl[17];

    wire [31:0] den     = energy_low + energy_high;
    wire [63:0] lhs     = {32'd0, energy_high} * 64'd1000;
    wire [63:0] rhs     = {48'd0, threshold}   * {32'd0, den};
    wire [63:0] rhs_low = {48'd0, JUMP_RATIO}  * {32'd0, den};

    wire ratio_hit = (lhs > rhs);

    // ---- 判据 B：瞬态突增 --------------------------------------------------
    reg [31:0] slow_ref;
    reg [10:0] warm_cnt;          // 上电 1 s 内不判突增，避免参考值为 0

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            slow_ref <= 32'd0;
            warm_cnt <= 11'd0;
        end
        else if (strobe) begin
            if (warm_cnt != 11'h7FF)
                warm_cnt <= warm_cnt + 11'd1;

            // 一阶低通，alpha = 1/128，按 1 kHz 采样 -> 时间常数约 0.13 s
            slow_ref <= slow_ref - (slow_ref >> 7) + (den >> 7);   /* tau ~ 128 ms */
        end
    end

    wire [32:0] den_long = {1'b0, den};
    wire [32:0] slow_x2  = {1'b0, slow_ref} << 1;
    wire        jump_hit = (warm_cnt == 11'h7FF) && (slow_ref > GATE) && (den_long > slow_x2);
    // 电平门限：底噪不上报。实测静音底噪（编码器 +18 dB PGA 放大 + 悬空输入）
    // 的总能量峰值约 10^4，原来的 10000 正好被它顶穿，导致判据 B 每 20~30 s
    // 误锁存一次。抬到 40000（约 -54 dB）后，底噪要再涨 4 倍才够得着。
    wire        jump_ok  = jump_hit && (lhs > rhs_low) && (den >= 32'd150000);

    // ---- 综合 --------------------------------------------------------------
    // 峭度判据暂不参与判定：kurtosis_monitor 已实现并上板验证（KL 会随信号变化），
    // 但当前麦克风电平太低（|y| 约 40），量化噪声把峭度抬到 200 以上，
    // 与真正的冲击无法区分。等输入电平提高 10 倍以上再启用。
    // 现在只在 status[19]（kurt_i）和 status[31:28]（kurt_lvl）里观测。
    wire above_i = enable_ev && (den >= GATE) && (ratio_hit || jump_ok);

    reg [5:0]  cnt_on;
    reg [5:0]  cnt_off;
    reg        latched;
    reg [15:0] lock_count;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cnt_on     <= 6'd0;
            cnt_off    <= 6'd0;
            latched    <= 1'b0;
            lock_count <= 16'd0;
            status     <= 32'd0;
            event_irq  <= 1'b0;
        end
        else begin
            event_irq <= 1'b0;

            if (clear_ev) begin
                cnt_on     <= 6'd0;
                cnt_off    <= 6'd0;
                latched    <= 1'b0;
                lock_count <= 16'd0;
                status     <= {kurt_lvl, 8'd0, kurt_i, enable_ev, above_i, 1'b0, 16'd0};
            end
            else if (strobe) begin
                if (above_i) begin
                    cnt_off <= 6'd0;

                    if (cnt_on < CONFIRM_ON)
                        cnt_on <= cnt_on + 6'd1;

                    if ((cnt_on == (CONFIRM_ON - 6'd1)) && !latched) begin
                        latched    <= 1'b1;
                        lock_count <= lock_count + 16'd1;
                        event_irq  <= 1'b1;
                    end
                end
                else begin
                    cnt_on <= 6'd0;

                    if (cnt_off < CONFIRM_OFF)
                        cnt_off <= cnt_off + 6'd1;

                    if ((cnt_off == (CONFIRM_OFF - 6'd1)) && latched)
                        latched <= 1'b0;
                end

                status <= {kurt_lvl, 8'd0, kurt_i, enable_ev, above_i, latched, lock_count};
            end
        end
    end

endmodule
