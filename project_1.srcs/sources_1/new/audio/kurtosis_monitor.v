`timescale 1ns / 1ps

// ---------------------------------------------------------------------------
// kurtosis_monitor -- 峭度（kurtosis）统计
//
//   kurt = mean(y^4) / mean(y^2)^2
//   高斯噪声 ~3，正弦 ~1.5，真正的冲击 10~50
//
// 本模块只做"统计"：接收已经采好的 16 位样本（左声道，48 kHz），
// 每 512 点算一次。I2S 接收由调用方（audio_receive + CDC）负责，
// 这样职责单一，也便于用仿真直接喂样本验证。
//
// 数值范围（y2 钳到 2^26）：
//   sum(y^2) <= 512*2^26 = 2^35      -> 36 bit
//   sum(y^4) <= 512*2^52 = 2^61      -> 62 bit
// 判据 kurt > K  <=>  sum(y^4) > K*sum(y^2)^2/512
// ---------------------------------------------------------------------------
module kurtosis_monitor #(
    parameter [31:0] Y2_CAP  = 32'h0400_0000,   // 2^26，约 |y| <= 8192
    parameter [63:0] KURT_K  = 64'd15,          // 峭度阈值
    parameter [9:0]  WIN_END = 10'd511          // 512 点一个窗口
)(
    input  wire        clk,
    input  wire        rst_n,

    input  wire        sample_valid,   // 一个采样有效
    input  wire [15:0] sample,         // 左声道样本（补码）

    output reg         kurt_hit,       // 本窗口峭度超阈值（保持到下一窗口）
    output reg  [3:0]  kurt_lvl,       // 近似 log2(峭度)
    output reg  [15:0] peak_mag        // 本窗口峰值
);

    wire signed [15:0] y    = sample;
    wire        [15:0] absy = y[15] ? (~y + 16'd1) : y;
    wire        [31:0] y2   = absy * absy;
    wire        [31:0] y2c  = (y2 > Y2_CAP) ? Y2_CAP : y2;
    wire        [63:0] y4   = y2c * y2c;

    localparam [1:0] S_ACC = 2'd0, S_MUL = 2'd1, S_CMP = 2'd2;

    reg [1:0]  st;
    reg [35:0] s2k;
    reg [61:0] s4k;
    reg [9:0]  cnt;

    reg [35:0] s2_snap;
    reg [61:0] s4_snap;
    reg [71:0] prod;

    wire [67:0] thr = (prod >> 9) * KURT_K;

    // 近似 log2(峭度)
    reg [5:0] e4, e2;
    integer   k;
    always @(*) begin
        e4 = 6'd0;
        for (k = 0; k < 62; k = k + 1)
            if (s4_snap[k]) e4 = k[5:0];
        e2 = 6'd0;
        for (k = 0; k < 36; k = k + 1)
            if (s2_snap[k]) e2 = k[5:0];
    end

    wire signed [7:0] lvl_raw = ({2'd0, e4}) - ({1'd0, e2, 1'b0}) + 8'sd9;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            st       <= S_ACC;
            s2k      <= 36'd0;
            s4k      <= 62'd0;
            cnt      <= 10'd0;
            s2_snap  <= 36'd0;
            s4_snap  <= 62'd0;
            prod     <= 72'd0;
            kurt_hit <= 1'b0;
            kurt_lvl <= 4'd0;
            peak_mag <= 16'd0;
        end
        else begin
            case (st)
                S_ACC: begin
                    if (sample_valid) begin
                        if (absy > peak_mag)
                            peak_mag <= absy;

                        if (cnt == WIN_END) begin
                            s2_snap <= s2k + {26'd0, y2c};
                            s4_snap <= s4k + y4;
                            st      <= S_MUL;
                        end
                        else begin
                            s2k <= s2k + {26'd0, y2c};
                            s4k <= s4k + y4;
                            cnt <= cnt + 10'd1;
                        end
                    end
                end

                S_MUL: begin
                    prod <= s2_snap * s2_snap;
                    st   <= S_CMP;
                end

                S_CMP: begin
                    kurt_hit <= ({6'd0, s4_snap} > thr);
                    kurt_lvl <= (lvl_raw < 0) ? 4'd0 : (lvl_raw > 15 ? 4'd15 : lvl_raw[3:0]);
                    peak_mag <= 16'd0;
                    s2k      <= 36'd0;
                    s4k      <= 62'd0;
                    cnt      <= 10'd0;
                    st       <= S_ACC;
                end

                default: st <= S_ACC;
            endcase
        end
    end

endmodule
