`timescale 1ns / 1ps

// ---------------------------------------------------------------------------
// tb_kurt -- 给 kurtosis_monitor 喂一段"已知峭度"的 I2S 数据流
//
//   每帧 32 个 bclk：前 16 位左声道（bitc[4]=0），后 16 位右声道
//   样本序列：平时是 ±(1~3) 的小噪声，每 128 帧插入一个满幅尖峰
//   已知：512 点窗口里有 4 个尖峰 -> 峭度应当远超阈值 8
//   平时纯小噪声 -> 峭度约 3
// ---------------------------------------------------------------------------
module tb_kurt;

    reg clk = 1'b0;
    reg rst_n = 1'b0;

    always #10 clk = ~clk;          // 50 MHz

    // ---- bclk / lrc 生成 ------------------------------------------------
    reg [4:0] div = 5'd0;
    reg       bclk = 1'b0;
    reg [4:0] bitc = 5'd0;
    wire      lrc  = bitc[4];

    always @(posedge clk) begin
        div <= div + 5'd1;
        bclk <= div[4];             // 50MHz/32 = 1.5625 MHz
    end

    always @(posedge bclk) begin
        bitc <= bitc + 5'd1;
    end

    // ---- 样本产生 --------------------------------------------------------
    reg [15:0] cur = 16'd0;
    reg [7:0]  fidx = 8'd0;
    integer    k;

    always @(posedge bclk) begin
        if (bitc == 5'd31) begin
            fidx <= fidx + 8'd1;
            if ((fidx % 8'd128) == 8'd0)
                cur <= 16'sd20000;              // 尖峰
            else
                cur <= (fidx[0] ? 16'sd2 : -16'sd2);  // 小噪声
        end
    end

    reg adc_dat = 1'b0;
    always @(negedge bclk) begin
        adc_dat <= cur[5'd15 - bitc[3:0]];
    end

    // ---- DUT -------------------------------------------------------------
    wire        kurt_hit;
    wire [3:0]  kurt_lvl;
    wire [15:0] peak_mag;

    kurtosis_monitor dut (
        .clk      (clk),
        .rst_n    (rst_n),
        .adc_dat  (adc_dat),
        .bclk     (bclk),
        .lrc      (lrc),
        .kurt_hit (kurt_hit),
        .kurt_lvl (kurt_lvl),
        .peak_mag (peak_mag)
    );

    // ---- 观测 ------------------------------------------------------------
    integer hits = 0;
    integer n    = 0;

    initial begin
        rst_n = 1'b0;
        #2000;
        rst_n = 1'b1;
    end

    // 每 300 us 打印一次（大约 30 个样本一个点）
    always #300000 begin
        n = n + 1;
        if (kurt_hit) hits = hits + 1;
        if (n % 8 == 0)
            $display("[%0t] KL=%0d K=%0d peak=%0d hits=%0d  y=%0d shreg=%h cnt=%0d s2=%0d s4=%0d",
                     $time, kurt_lvl, kurt_hit, peak_mag, hits,
                     dut.left_sample, dut.shreg, dut.cnt, dut.s2k, dut.s4k);
    end

    // CMPTRACE：比较那一拍打印
    always @(posedge clk) begin
        if (dut.st == 2'd2 && n < 40) begin
            $display("CMPTRACE s2=%0d s4=%0d prod=%0d thr=%0d hit=%0d",
                     dut.s2_snap, dut.s4_snap, dut.prod, dut.thr,
                     ({{6{1'b0}}, dut.s4_snap} > dut.thr));
            n = n + 1;
        end
    end

    initial begin
        #40000000;                  // 40 ms
        $display("=== 结束: hits=%0d, 最后 KL=%0d ===", hits, kurt_lvl);
        $finish;
    end

endmodule
