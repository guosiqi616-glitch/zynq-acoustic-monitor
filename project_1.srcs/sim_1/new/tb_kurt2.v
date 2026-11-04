`timescale 1ns / 1ps

// tb_kurt2 -- 直接喂样本，验证峭度统计
//   A 段：高斯噪声（σ=100）       -> 峭度应≈3，不触发
//   B 段：每 128 点一个满幅尖峰     -> 峭度应≫8，触发
//   C 段：正弦（幅度 1000）        -> 峭度≈1.5，不触发
module tb_kurt2;

    reg clk = 0;
    reg rst_n = 0;
    always #10 clk = ~clk;      // 50 MHz

    reg        valid = 0;
    reg [15:0] samp  = 16'd0;

    wire        kurt_hit;
    wire [3:0]  kurt_lvl;
    wire [15:0] peak_mag;

    kurtosis_monitor #(.KURT_K(64'd8)) dut (
        .clk          (clk),
        .rst_n        (rst_n),
        .sample_valid (valid),
        .sample       (samp),
        .kurt_hit     (kurt_hit),
        .kurt_lvl     (kurt_lvl),
        .peak_mag     (peak_mag)
    );

    integer i, idx = 0;
    integer hits = 0;
    real    rnd;

    // 48 kHz 样本节拍：每 1042 个 clk 一个样本
    always begin
        #20813;                 // 48 kHz（避开时钟沿）
        valid = 1;
        idx   = idx + 1;

        if (idx < 2048) begin
            // A: 高斯噪声，σ=100
            rnd = $sqrt(-2.0 * $ln($urandom_range(1, 1000000) / 1000000.0)) *
                  $cos(6.2831853 * ($urandom_range(0, 1000000) / 1000000.0));
            samp = $rtoi(rnd * 100.0);
        end
        else if (idx < 4096) begin
            // B: 每 128 点一个尖峰
            samp = ((idx % 128) == 0) ? 16'sd20000 : 16'sd2;
        end
        else begin
            // C: 正弦
            samp = $rtoi($sin(6.2831853 * 5.0 * (idx - 4096) / 48.0) * 1000.0);
        end

        @(posedge clk);
        valid = 0;

        if (kurt_hit) hits = hits + 1;

        if ((idx % 512) == 256)
            $display("idx=%0d KL=%0d hit=%0d peak=%0d", idx, kurt_lvl, kurt_hit, peak_mag);

        if (idx > 6000) begin
            $display("=== hits=%0d ===", hits);
            $finish;
        end
    end

    // DBG
    integer dbg = 0;
    always @(posedge clk) begin
        if (dut.sample_valid === 1'b1) begin
            if (dbg < 6) $display("DBG valid=1 sample=%0d st=%0d cnt=%0d", dut.sample, dut.st, dut.cnt);
            dbg = dbg + 1;
        end
    end

    initial begin
        #500; rst_n = 1;
    end

endmodule
