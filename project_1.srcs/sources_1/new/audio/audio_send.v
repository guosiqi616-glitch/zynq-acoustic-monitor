//****************************************Copyright (c)***********************************//
//原子哥在线教学平台：www.yuanzige.com
//技术支持：http://www.openedv.com/forum.php
//淘宝店铺：https://zhengdianyuanzi.tmall.com
//关注微信公众平台微信号："正点原子"，免费获取ZYNQ & FPGA & STM32 & LINUX资料。
//版权所有，盗版必究。
//Copyright(C) 正点原子 2023-2033
//All rights reserved                                  
//----------------------------------------------------------------------------------------
// File name:           audio_send
// Created by:          正点原子
// Created date:        2024年4月15日10:12:02
// Version:             V1.0
// Descriptions:        FPGA发送音频数据模块
//
//----------------------------------------------------------------------------------------
//****************************************************************************************//

module audio_send #(parameter WL = 6'd16) (    // WL(word length音频字长定义)
    //system reset
    input                    rst_n     ,         // 复位信号
                             
    //音频接口               
    input                    aud_bclk  ,         // 位时钟
    input                    aud_lrc   ,         // 左右通道时钟
    output   reg             aud_dacdat,         // 音频数据输出
    //user interface
    input         [WL-1:0]   dac_data  ,         // 预输出的音频数据
    output   reg             tx_done             // 发送一次音频位数完成
);

//reg define
reg                aud_lrc_d0;                   // 延迟一个时钟周期
reg    [ 5:0]      tx_cnt    ;                   // 发送数据计数
reg    [WL-1:0]    dac_data_t;                   // 预输出的音频数据的暂存值

//wire define
wire               lrc_edge  ;                    // 边沿信号

//*****************************************************
//**                    main code
//*****************************************************

assign  lrc_edge = aud_lrc ^ aud_lrc_d0;     // LRC信号的边沿检测

//为了在aud_lrc变化的第二个AUD_BCLK上升沿采集aud_adcdat,延迟打拍采集
always @(posedge aud_bclk or negedge rst_n) begin
    if(!rst_n)
        aud_lrc_d0 <= 1'b0;
    else
        aud_lrc_d0 <= aud_lrc;
end

//发送32位音频数据的计数
always @(posedge aud_bclk or negedge rst_n) begin
    if(!rst_n) begin
        tx_cnt     <=  6'd0;
        dac_data_t <= 16'd0;
    end
    else if(lrc_edge == 1'b1) begin
        tx_cnt     <= 6'd0;
        dac_data_t <= dac_data;
    end
    else if(tx_cnt <= WL)
        tx_cnt <= tx_cnt + 6'b1;
end

//把预发送的音频数据串行发送出去
always @(negedge aud_bclk or negedge rst_n) begin
    if(!rst_n) begin
        aud_dacdat <= 1'b0;
    end
    else if(tx_cnt < WL)
        aud_dacdat <= dac_data_t[WL - 1'd1 - tx_cnt];
    else
        aud_dacdat <= 1'b0;
end

//发送完成信号
always @(posedge aud_bclk or negedge rst_n) begin
    if(!rst_n) begin
        tx_done <= 1'b0;
    end
    else if(tx_cnt == WL)
        tx_done <= 1'b1;
    else
        tx_done <= 1'b0;
end

endmodule