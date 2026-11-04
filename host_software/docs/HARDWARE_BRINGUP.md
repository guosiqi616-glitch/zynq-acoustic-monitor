# 硬件接入：10 分钟检查

> 目标：板子第一次接上 PC，10 分钟内确认“串口通、I 帧对、60 秒数据可信、网页徽章变 BOARD”。
>
> 现状：`tools/hardware_check.py` 已写好，并用模拟器日志验证了 PASS / FAIL 两条路径；**实板还没接**，明天按本文顺序做即可。

## 0. 先做 4 件事（约 2 分钟）

1. **关掉 XCOM / 串口助手**：它占着 COM 口，`hardware_check.py` 和桥接都打不开。
2. **停掉 `relay/server.py`**：桥接也独占串口。先检查，再启动桥接。
3. **改 Latency Timer**：设备管理器 → 端口 (COM 和 LPT) → 你的 CH340/FTDI → 属性 → 端口设置 → 高级 → **Latency Timer = 1 ms**。默认 16 ms 会把延迟测量污染成“假高延迟”。
4. **确认 COM 号**：

```powershell
cd D:\Xilinx\S\project\project_1\host_software
D:\Python\python.exe tools\hardware_check.py --list-ports
```

蓝牙的 COM3/COM4 不是板子，选 `USB-SERIAL CH340 (COMx)` 或 FTDI 那个。

## 1. 接线与板子状态（约 1 分钟）

- TX/RX **交叉**：板子 TX → USB-TTL RX，板子 RX → USB-TTL TX。
- **GND 共地**；3.3V TTL 电平，不要接 RS232 电平。
- 板子确实在跑：LED、复位、PS 程序、PL bitstream 都正常。
- 协议串口固定 **115200 8N1**（PS 侧 50 Hz 输出约 2.6 KB/s，余量约 4 倍）；两端必须一致，否则只会看到乱码。
- 裸机发送**不要用 `%f`**：I/T/E/S 全整数，上位机按 scale 还原（温度 ×100、湿度 ×100、dBFS ×10、BER ×1000）。

## 2. 跑 60 秒检查（约 2 分钟）

```powershell
D:\Python\python.exe tools\hardware_check.py --port COM5 --baud 115200
```

等 I 帧最多 3 秒；没有就打印排查清单。收到 I 后打印：

```
dev_id / fw / protocol / fs_hz / win / bands / caps
fs_hz   48000  期望 48000  PASS
win     512    期望 512    PASS
bands   2      期望 2      PASS
```

如果实际配置不是 48000/512/2，用参数覆盖：

```powershell
D:\Python\python.exe tools\hardware_check.py --port COM5 --baud 115200 --expect-fs 48000 --expect-win 512 --expect-bands 2
```

如果板子只在复位时发一次 I，先让工具进入等待，再按板子复位；或把 `--wait-i` 调到 10：

```powershell
D:\Python\python.exe tools\hardware_check.py --port COM5 --baud 115200 --wait-i 10
```

然后采集 60 秒，检查：

| 检查 | 通过标准 |
|---|---|
| `seq` 连续性 | T（以及 E/S）无丢失、无跳变；回绕按 `(last+1) & 0xFFFFFFFF` 判断 |
| `det = lock + supp` | 每次 T 都满足 32 位无符号等式 |
| `t_us` 单调 | 单次上电内不倒退；倒退超过 1 s 视为重启 |
| T 帧率 | 设备时间 18~22 Hz（默认目标 20 Hz） |
| 解析错误 | 默认 0 条；开串口时的半行/乱码不计入 |
| 重启 | 60 秒内 0 次 |

输出 `PASS` 或 `FAIL`，失败时给前几条具体帧，例如：

```
- seq 连续性: 丢失 1（T:1），跳变 0
- det = lock + supp: 违反 1 次
- t_us 单调: 倒退 1 次
    T seq 198 -> 200 (丢失 1)
    T seq=99 t=5001000 lock=1 det=2 supp=0 (期望 1)
    T seq=299 t_us=14851999 < 上一条 14951999
```

## 3. 启动桥接，看 BOARD 徽章（约 2 分钟）

`hardware_check.py` 退出后会释放串口。然后：

```powershell
D:\Python\python.exe relay\server.py --source serial:COM5@115200 --port 8080 --mode board
```

浏览器打开：

```
http://127.0.0.1:8080/
```

顶栏徽章应从 **SIM** 变成 **BOARD**。

- 如果还是 SIM：确认 `--source` 是 `serial:` 而不是 `tcp:`；或显式加 `--mode board`；然后 `Ctrl+F5` 强刷。
- 如果仍打不开 COM：先关 XCOM，再确认上一节 `hardware_check.py` 已经退出。
- 页面上顺便确认 SHT30 温湿度不是 `--`、`RX` 在增长、锁存一次能出事件。

## 4. 必踩的坑（贴在显示器边上）

| # | 坑 | 处理 |
|---|---|---|
| 1 | XCOM / 串口助手占着 COM | 先关掉；一个 COM 同时只能一个程序 |
| 2 | CH340/FTDI Latency Timer 默认 16 ms | 设备管理器改成 **1 ms**，否则延迟数字不可信 |
| 3 | 裸机用 `%f` / 浮点文本 | v1 固定全整数；`fs/win/bands` 和缩放见协议 |
| 4 | 半行、`\r\n`/`\n` 混用、乱码字节 | `hardware_check.py` 已用行缓冲 + CR/LF 归一化 + 丢弃非法行处理 |
| 5 | `seq` 直接比大小 | 用 `(last + 1) & 0xFFFFFFFF` 和 `(seq - expected) & 0xFFFFFFFF` |
| 6 | 波特率两端不一致 | 115200 8N1；不行再 460800 / 115200，两端一起改 |
| 7 | TX/RX 没交叉 / 没共地 | 交换 TX/RX，接 GND；用 3.3V TTL |
| 8 | 板子没在跑 | 看 LED、按复位、确认 PS 程序与 PL bitstream |
| 9 | PS 还是旧版 `Temp:...` 字符串 | 旧版没有 I 帧；先烧 v1 协议固件（I/T/E/S） |

## 5. 没硬件时先自测

代码已经用模拟器日志跑过，不接板子也能验证检查逻辑：

```powershell
# 正常 60 秒日志：应 PASS
D:\Python\python.exe tools\hardware_check.py --source file:records/sim60.log

# 只打印清单
D:\Python\python.exe tools\hardware_check.py --checklist
```

文件模式不按墙钟等 60 秒，只按日志内容检查；真实板子用 `--port` / `--source serial:`。

## 6. 常见失败速查

| 现象 | 优先检查 |
|---|---|
| `打开 COMx 失败` / 拒绝访问 | XCOM、桥接是否还占着；COM 号是否选错 |
| 3 秒没 I，收到 0 字节 | TX/RX 交叉、GND、板子是否上电/复位、USB-TTL 驱动 |
| 收到字节但没有 I | 波特率不一致、调试打印混进协议流、TX/RX 接反；如果看到 `Temp:` / `Humi:`，说明 PS 还在发旧版 v0 字符串，先烧 v1 固件 |
| I 帧 `fs/win/bands` FAIL | PL 配置或固件常量与预期不一致 |
| `seq` FAIL | UART 丢字节 / T 循环超时 / 采集被阻塞 |
| `det = lock + supp` FAIL | 计数逻辑写错，或 T 帧字段顺序错位 |
| `t_us` 倒退 / 重启 FAIL | 复位、掉电、时钟源切换、`t_us` 回绕处理 |
| 帧率 10 / 40 Hz | 定时器周期、循环体耗时、`t_us` 单位（us vs ticks） |
| `caps.bit0=0` 警告 | t_us 来自软件计时，延迟数字偏大；应使用 PL 硬件计数器 |
| 页面徽章还是 SIM | 桥接 `--source serial:` / `--mode board` / 强刷 |

## 7. 一分钟版命令

```powershell
cd D:\Xilinx\S\project\project_1\host_software

# 1) 看端口
D:\Python\python.exe tools\hardware_check.py --list-ports

# 2) 自检（关掉 XCOM 和桥接）
D:\Python\python.exe tools\hardware_check.py --port COM5 --baud 115200

# 3) PASS 后启动桥接
D:\Python\python.exe relay\server.py --source serial:COM5@115200 --port 8080 --mode board

# 4) 打开 http://127.0.0.1:8080/ ，确认徽章 BOARD
```
