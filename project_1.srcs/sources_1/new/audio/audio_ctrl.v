
module audio_ctrl(
    input                clk        ,   // 时钟信号
    input                rst_n      ,   // 复位信号
    
    //audio interface
(* mark_debug = "true" *) input                aud_bclk   ,
(* mark_debug = "true" *) input                aud_lrc    ,
(* mark_debug = "true" *) input                aud_adcdat ,
 output               aud_dacdat ,   // 音频输出
    output [15:0]        adc_data_dbg,
    output               rx_done_dbg ,
    
    //control interfac  
    output               aud_scl    ,   // 音频的SCL信号
    inout                aud_sda        // 音频的SDA信号

);

//parameter define
parameter    WL = 6'd16;                // word length音频字长定义

//wire define
//wire define
(* mark_debug = "true" *) wire               rx_done    ;
wire               tx_done    ;
(* mark_debug = "true" *) wire    [15:0]     data       ;
//*****************************************************
//**                    main code
//*****************************************************

//例化音频寄存器配置模块
audio_config #(
    .WL             (WL)
) u_audio_config(
    .clk            (clk),              // 时钟信号
    .rst_n          (rst_n),            // 复位信号
        
    .aud_scl        (aud_scl),          // 音频的SCL时钟
    .aud_sda        (aud_sda)           // 音频的SDA信号
);

//例化音频接收模块
audio_receive #(
    .WL             (WL)
) u_audio_receive(    
    .rst_n          (rst_n),            // 复位信号
    
    .aud_bclk       (aud_bclk),         // 位时钟
    .aud_lrc        (aud_lrc),          // 左右通道时钟
    .aud_adcdat     (aud_adcdat),       // 音频输入
        
    .adc_data       (data),             // FPGA接收的数据
    .rx_done        (rx_done)           // FPGA接收数据完成
);

//例化音频发送模块
audio_send #(
    .WL             (WL)
) u_audio_send(
    .rst_n          (rst_n),            // 复位信号
        
    .aud_bclk       (aud_bclk),         // 位时钟
    .aud_lrc        (aud_lrc),          // 左右通道时钟
    .aud_dacdat     (aud_dacdat),       // 音频数据输出
        
    .dac_data       (data),             // 预输出的音频数据
    .tx_done        (tx_done)           // 发送完成信号
);

assign adc_data_dbg = data;
assign rx_done_dbg = rx_done;

endmodule 