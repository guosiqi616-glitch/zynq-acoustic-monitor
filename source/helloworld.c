/******************************************************************************
 * Zynq-7010 mechanical anomaly monitor
 * PL 音频监测 + v1 协议输出
 *
 * - SHT30 real temperature / humidity (I2C0, address 0x44)
 * - 50 Hz T frames, 1 Hz I frames, 1 Hz S frames
 * - 音频链路：ES8388 ADC -> I2S -> PL 双带 FIR / 能量 -> AXI GPIO -> PS
 *     db_low/db_high = 10*log10(E) - 100（dBFS×10，E 为 PL 送来的 26 位能量）
 *     ber = 高频能量占比千分比（不是误码率）
 * - UART 115200 8N1, integer-only frames (no %f)
 *
 * Replace helloworld.c with this file, rebuild, and run.
 ******************************************************************************/

#include "platform.h"
#include "xil_printf.h"
#include "xil_types.h"
#include "xil_io.h"
#include "xiicps.h"
#include "sleep.h"
#include "xtime_l.h"
#include "detector.h"

#define IIC_DEVICE_ID   XPAR_XIICPS_0_DEVICE_ID
#define IIC_SCLK_RATE   100000
#define SHT30_ADDR      0x44

/* AXI GPIO dual-channel input, assigned by the PS7 address editor. */
#define AUDIO_GPIO_BASE 0x41200000U
#define AUDIO_GPIO_DATA1 (AUDIO_GPIO_BASE + 0x0000U)
#define AUDIO_GPIO_DATA2 (AUDIO_GPIO_BASE + 0x0008U)

/* 第二个 AXI GPIO：ch1 读事件状态，ch2 写阈值/清锁存/使能 */
#define EV_GPIO_BASE    0x41210000U
#define EV_GPIO_STATUS  (EV_GPIO_BASE + 0x0000U)   /* ch1 输入 */
#define EV_GPIO_CTRL    (EV_GPIO_BASE + 0x0008U)   /* ch2 输出 */

#define EV_ENABLE       (1U << 17)
#define EV_CLEAR        (1U << 16)
/* 阈值 0.85：实测静音底噪的频带能量比稳定在 0.753（= 两条 FIR 的带宽比 8/(2.9+8)，
   说明是白噪声），偶尔会短时冲到 0.78~0.79，用 0.78 做阈值会锁存假事件。
   真实齿轮打齿实测 0.97，0.85 仍留 12% 余量。 */
#define EV_THRESHOLD    780U                       /* 千分比，780 = ratio 0.78 */

#define FRAME_HZ        50
#define FRAME_PERIOD_US 20000

/*
 * XTime_GetTime() reads the Zynq global timer, which is clocked at CPU/2.
 * COUNTS_PER_SECOND can resolve to the sleep-timer frequency (a different
 * clock), so use the global timer frequency explicitly here.
 */
#define GLOBAL_TIMER_HZ (XPAR_CPU_CORTEXA9_CORE_CLOCK_FREQ_HZ / 2U)

static XIicPs Iic;

static int sht_ok = 0;
static int temp100 = -32768;
static int hum100 = 65535;
static u32 sht_err = 0;

static u32 seq_t = 0;
static u32 seq_s = 0;

static u32 energy_low = 0;
static u32 energy_high = 0;
static int audio_ok = 0;

/* PL 侧事件锁存状态 */
static u32 ev_count   = 0;   /* status[15:0]  事件计数 */
static u32 ev_latched = 0;   /* status[16]    锁存标志 */
static u32 ev_above   = 0;   /* status[17]    当前超阈值 */
static u32 ev_kurt    = 0;   /* status[19]    峭度超限 */
static u32 ev_klvl    = 0;   /* status[31:28] 近似 log2(峭度) */

static u32 seq_e        = 0; /* E 帧序号 */

static void read_audio_energy(void)
{
    energy_low  = Xil_In32(AUDIO_GPIO_DATA1);
    energy_high = Xil_In32(AUDIO_GPIO_DATA2);
    /* 0xFFFFFFFF 既可能是浮空输入，也可能是 AXI DECERR，不能算“读到了” */
    if ((energy_low  != 0U && energy_low  != 0xFFFFFFFFU) ||
        (energy_high != 0U && energy_high != 0xFFFFFFFFU)) {
        audio_ok = 1;
    }
}

/* 多维异常检测核心（谱形 + 时间结构 + 能量突增，见 detector.h） */
static det_t g_det;

/* PL 当前使用的比值阈值（千分比），由 PS 基线同步下发 */
static u32 g_thr = 990U;

/* 事件状态机：PS 判决为准，PL 的锁存只作为辅助观测 */
static u8  in_event  = 0;      /* 当前是否处于一次事件中 */
static u32 lvl2_ticks = 0;     /* level>=2 连续帧数，用于事件开闸延时 */
static u64 last_ev_us  = 0;    /* 上一条事件的时间戳，用于冷却窗 */
static u32 ev_total  = 0;      /* PS 确认的事件总数（T 帧 lock/det） */
static u32 seq_x     = 0;      /* X 帧序号 */
static u32 last_pl_ev = 0;     /* 上一帧的 PL 事件计数 */
static u32 x_div     = 0;      /* X 帧分频计数 */

/* 初始化 PL 侧异常检测：写阈值 + 使能，并清一次锁存 */
static void init_event_detect(void)
{
    Xil_Out32(EV_GPIO_CTRL, EV_ENABLE | EV_CLEAR | g_thr);  /* 清锁存 */
    Xil_Out32(EV_GPIO_CTRL, EV_ENABLE | g_thr);             /* clear 拉回 0 */
}

/* ------------------------------------------------------------------
 * 检测器初始化与 PL 门限同步
 *
 * PL 侧的快路径（双判据 + 锁存）保持不变，但门限改由 PS 下发，
 * 避免"PL 一套阈值、PS 一套阈值"互相打架。
 * ------------------------------------------------------------------ */
static void detector_init(void)
{
    det_init(&g_det);
}

static void sync_pl_threshold(void)
{
    u32 thr = det_ratio_thr(&g_det);

    if (thr != g_thr) {
        g_thr = thr;
        Xil_Out32(EV_GPIO_CTRL, EV_ENABLE | g_thr);
    }
}

static void read_event_status(void)
{
    u32 st = Xil_In32(EV_GPIO_STATUS);

    ev_count   = st & 0xFFFFU;
    ev_latched = (st >> 16) & 1U;
    ev_above   = (st >> 17) & 1U;
   ev_kurt    = (st >> 19) & 1U;
   ev_klvl    = (st >> 28) & 0xFU;
}

/* AXI GPIO 自检：两路全输入模式下 TRI 复位值应为 0xFFFFFFFF。
   注意：这个 IP 没有使能中断（C_INTERRUPT=0），0x11C/0x128 这些中断寄存器
   根本没有实现，读回 0 是正常的，不能用它判断从机是否存活。 */
static void axi_gpio_selftest(void)
{
    u32 tri1 = Xil_In32(AUDIO_GPIO_BASE + 0x04U);
    u32 tri2 = Xil_In32(AUDIO_GPIO_BASE + 0x0CU);

    xil_printf("# [DIAG] TRI1=%08x TRI2=%08x (expect FFFFFFFF FFFFFFFF)\r\n",
               tri1, tri2);
}

static int energy_to_db10(u32 energy)
{
    int msb = 0;
    u32 v = energy;
    u32 base;
    u32 frac_q8;

    if (energy == 0U) {
        return -900;
    }

    while (v >>= 1) {
        msb++;
    }

    /* 只用 msb 的话分辨率是 3 dB（一档一跳，看起来"不动"）。
       这里在 [2^msb, 2^(msb+1)) 内做线性插值，分辨率降到约 0.26 dB：
       log2(E) = msb + (E - 2^msb)/2^msb，再乘 30.103 得到 0.1 dB 单位。 */
    base = 1U << msb;
    frac_q8 = (u32)((((u64)(energy - base)) << 8) / base);   /* 0..255 */

    return (int)(((u32)msb * 30103U + (frac_q8 * 30103U) / 256U) / 1000U) - 1000;
}

/* 频带能量比 R = E_high / (E_low + E_high)，千分比 0..1000。
   这就是 T 帧里的 BER 字段（Band Energy Ratio，不是误码率）。
   能量低于噪声门限时比值没有意义（分母接近 0 会乱跳），直接返回 0。 */
/* 与 PL 侧 anomaly_detect.GATE 保持一致：低于该总能量时比值没有意义。
   实测静音底噪（编码器 +18 dB PGA 放大的宽带噪声）den 可达 ~1300，
   所以压到 4096 才稳；真实可听信号 den 在 10^4 量级，不会被挡。 */
#define RATIO_NOISE_GATE 100U

static u32 energy_ratio_permille(void)
{
    u64 den = (u64)energy_low + (u64)energy_high;

    if (den < (u64)RATIO_NOISE_GATE) {
        return 0U;
    }
    return (u32)(((u64)energy_high * 1000ULL) / den);
}

static int init_i2c(void)
{
    XIicPs_Config *cfg;
    int status;

    cfg = XIicPs_LookupConfig(IIC_DEVICE_ID);
    if (cfg == NULL) {
        return XST_FAILURE;
    }

    status = XIicPs_CfgInitialize(&Iic, cfg, cfg->BaseAddress);
    if (status != XST_SUCCESS) {
        return status;
    }

    XIicPs_SetSClk(&Iic, IIC_SCLK_RATE);
    return XST_SUCCESS;
}

/* I2C 等待总线空闲的最大轮询次数，防止总线被拉死时整个系统卡住 */
#define I2C_TIMEOUT_LOOPS 200000U

static int i2c_wait_idle(u32 loops)
{
    u32 g = 0;

    while (XIicPs_BusIsBusy(&Iic)) {
        if (++g > loops) {
            return XST_FAILURE;
        }
    }
    return XST_SUCCESS;
}

/* 总线恢复：重新初始化控制器，释放被拉住的 SCL/SDA */
static void i2c_bus_recover(void)
{
    XIicPs_Config *cfg = XIicPs_LookupConfig(IIC_DEVICE_ID);

    if (cfg != NULL) {
        XIicPs_CfgInitialize(&Iic, cfg, cfg->BaseAddress);
        XIicPs_SetSClk(&Iic, IIC_SCLK_RATE);
    }
}

/* SHT30 的 CRC8：多项式 0x31，初值 0xFF。I2C 偶发干扰时靠它把坏帧挡掉。 */
static u8 sht_crc8(const u8 *d)
{
    u8 crc = 0xFF;
    int i, b;

    for (i = 0; i < 2; i++) {
        crc ^= d[i];
        for (b = 0; b < 8; b++)
            crc = (crc & 0x80) ? (u8)((crc << 1) ^ 0x31) : (u8)(crc << 1);
    }
    return crc;
}

static int read_sht30(int *t100, int *h100)
{
    u8 cmd[2] = {0x2C, 0x06};   /* high repeatability, no clock stretching */
    u8 data[6] = {0};
    int status;
    u16 raw_t;
    u16 raw_h;
    int t_x10;
    int h_x10;

    /* 每一步都先确认总线空闲，超时就复位控制器，绝不无限等待 */
    if (i2c_wait_idle(I2C_TIMEOUT_LOOPS) != XST_SUCCESS) {
        i2c_bus_recover();
        return XST_FAILURE;
    }

    status = XIicPs_MasterSendPolled(&Iic, cmd, 2, SHT30_ADDR);
    if (status != XST_SUCCESS) {
        i2c_bus_recover();
        return XST_FAILURE;
    }

    if (i2c_wait_idle(I2C_TIMEOUT_LOOPS) != XST_SUCCESS) {
        i2c_bus_recover();
        return XST_FAILURE;
    }

    usleep(25000);              /* 高重复性模式约 15 ms，留到 25 ms */

    status = XIicPs_MasterRecvPolled(&Iic, data, 6, SHT30_ADDR);
    if (status != XST_SUCCESS) {
        i2c_bus_recover();
        return XST_FAILURE;
    }

    if (i2c_wait_idle(I2C_TIMEOUT_LOOPS) != XST_SUCCESS) {
        i2c_bus_recover();
        return XST_FAILURE;
    }

    if ((data[2] != sht_crc8(&data[0])) || (data[5] != sht_crc8(&data[3]))) {
        return XST_FAILURE;          /* 数据被干扰，丢弃这一帧 */
    }

    raw_t = (u16)(((u16)data[0] << 8) | (u16)data[1]);
    raw_h = (u16)(((u16)data[3] << 8) | (u16)data[4]);

    /* 量程：T = -45 + 175*raw/65535，H = 100*raw/65535，放大 100 倍存整数 */
    t_x10 = ((1750 * (int)raw_t) / 65535) - 450;
    h_x10 = (1000 * (int)raw_h) / 65535;

    *t100 = t_x10 * 10;
    *h100 = h_x10 * 10;
    return XST_SUCCESS;
}

static u32 sht_fail = 0;      /* 连续失败次数 */

static void update_sht30(void)
{
    int t;
    int h;
    int ok = 0;

    if (read_sht30(&t, &h) == XST_SUCCESS) {
        /* 量程校验：超出 SHT30 物理范围的直接当坏帧 */
        if ((t >= -4000) && (t <= 12500) && (h >= 0) && (h <= 10000)) {
            ok = 1;
        }
    }

    if (ok) {
        temp100 = t;
        hum100 = h;
        sht_ok  = 1;
        sht_fail = 0;
        det_on_temp(&g_det, temp100);   /* 温度融合：只在有新读数时喂 */
    } else {
        /* 偶发坏帧不立刻置无效，连续 3 次才认为传感器掉线 */
        sht_fail++;
        sht_err++;
        if (sht_fail >= 3) {
            temp100 = -32768;
            hum100  = 65535;
            sht_ok  = 0;
        }
    }
}

static u64 now_us(void)
{
    XTime ticks;
    XTime_GetTime(&ticks);
    return (u64)(ticks / (GLOBAL_TIMER_HZ / 1000000ULL));
}

static void u64_to_dec(u64 value, char *out)
{
    char tmp[21];
    int i = 0;
    int j = 0;

    if (value == 0) {
        out[0] = '0';
        out[1] = '\0';
        return;
    }

    while (value > 0 && i < 20) {
        tmp[i++] = (char)('0' + (int)(value % 10));
        value /= 10;
    }
    while (i > 0) {
        out[j++] = tmp[--i];
    }
    out[j] = '\0';
}

static void send_i(void)
{
    xil_printf("I,1,1,ZYNQ7010,0.2.0,48000,512,2,0\r\n");
}

static void send_t(u64 t_us)
{
    char tb[24];
    int db_low = energy_to_db10(energy_low);
    int db_high = energy_to_db10(energy_high);
    u32 state = in_event ? 2U :
                ((g_det.level >= 2U) ? 2U : (g_det.level ? 1U : 0U));
    u64_to_dec(t_us, tb);
    /* 字段: seq,t_us,temp,hum,db_low,db_high,ber,state,lock,det,supp
       ber   = 频带能量比（千分比）
       state = 0 正常 / 1 超阈值 / 2 事件（PS 多维判决 + PL 快路径融合）
       lock  = 事件总数；协议要求 det = lock + supp，supp 恒 0 */
    xil_printf("T,%u,%s,%d,%d,%d,%d,%u,%u,%u,%u,0\r\n",
               seq_t++, tb, temp100, hum100, db_low, db_high,
               g_det.ber_show, state, ev_total, ev_total);
}

static void send_e(u64 t_us)
{
    char tb[24];
    int db_low = energy_to_db10(energy_low);
    int db_high = energy_to_db10(energy_high);
    u32 reason;

    /* reason: 1 = 谱形异常（BER 显著高于基线）
               2 = 冲击（能量突增 / 峭度超限 / PL 锁存） */
    if (g_det.ber_show > g_det.mu[0] + 3 * g_det.sd[0])  reason = 1U;
    else if (g_det.jump || ev_kurt)                     reason = 2U;
    else                                                reason = 1U;

    u64_to_dec(t_us, tb);
    /* 字段: seq,t_us,db_low,db_high,ber,thr,lock,reason */
    xil_printf("E,%u,%s,%d,%d,%u,%u,%u,%u\r\n",
               seq_e++, tb, db_low, db_high, g_det.ber_show, g_thr,
               ev_total, reason);
}

/* X 帧：扩展诊断，10 Hz。v1.0 上位机会忽略未知帧型，
   但协议解析器已放开 X（lib/protocol.py），用于验证多维判决。 */
static void send_x(u64 t_us)
{
    char tb[24];
    u32 flags = 0;
    u32 score100;

    if (g_det.overheat)  flags |= 0x01U;   /* 温度高于基线 3 摄氏度 */
    if (ev_latched)      flags |= 0x02U;   /* PL 锁存 */
    if (ev_above)        flags |= 0x04U;   /* PL 超阈值 */
    if (g_det.n_upd < DET_LEARN_FAST) flags |= 0x08U;  /* 基线学习期 */
    if (g_det.jump)      flags |= 0x10U;   /* 能量突增 */
    if (in_event)        flags |= 0x20U;   /* 正在一次事件中 */
    if (g_det.speech_like) flags |= 0x40U; /* 语音类（能量包络有明显间隙） */

    score100 = (u32)(((u64)g_det.score * 100U) / 65536U);

    u64_to_dec(t_us, tb);
    /* X,seq,t_us,level,score100,ber,ber_res,kurt,thr,mu_ber,sd_ber,
         nf_low,nf_high,flags,ev_pl,ev_ps  —— 共 16 个字段 */
    xil_printf("X,%u,%s,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\r\n",
               seq_x++, tb,
               (u32)g_det.level, score100, g_det.ber_show, g_det.ber_res,
               ev_klvl, g_thr, (u32)g_det.mu[0], (u32)g_det.sd[0],
               g_det.nf_low, g_det.nf_high, flags, ev_count, ev_total);
}

static void send_s(u64 t_us)
{
    char tb[24];
    u32 flags = sht_ok ? 0x0002U : 0x0000U;
    u64_to_dec(t_us, tb);
    xil_printf("S,%u,%s,%u,%u,0\r\n",
               seq_s++, tb, flags, sht_err);
}

int main(void)
{
    u32 tick = 0;
    u64 next;
    int i2c_ok;

    init_platform();

    /* -----------------------------------------------------------------
     * 关掉底板的两颗 PS LED（PS_LED0/PS_LED1 = MIO7 / MIO8）。
     *
     * 这两个 MIO 在 PS7 里没有分配功能，复位默认值是 0x1601：
     * TRI_ENABLE=0（输出使能）、L0_SEL=0、L0=1 —— 也就是「一直在输出高」。
     * 板上 LED 是高电平点亮，所以它们上电就常亮。
     *
     * 把 L0 清 0 即可让引脚输出低、灯灭；用读-改-写，保留 IO_Type 等其它位。
     * 不占功能、不改 BD、不影响 QSPI / UART。
     * ----------------------------------------------------------------- */
    Xil_Out32(0xF8000008U, 0xDF0DU);                  /* SLCR 解锁 */
    Xil_Out32(0xF8000700U + 4U * 7U, Xil_In32(0xF8000700U + 4U * 7U) & ~1U);
    Xil_Out32(0xF8000700U + 4U * 8U, Xil_In32(0xF8000700U + 4U * 8U) & ~1U);
    Xil_Out32(0xF8000004U, 0x767BU);                  /* SLCR 重新上锁 */
    xil_printf("# [PSLED] MIO7=%08x MIO8=%08x\r\n",
               (unsigned)Xil_In32(0xF8000700U + 4U * 7U),
               (unsigned)Xil_In32(0xF8000700U + 4U * 8U));

    xil_printf("# ==================================================\r\n");
    xil_printf("#  Zynq-7010 v1.0 protocol ready\r\n");
    xil_printf("# ==================================================\r\n");

    i2c_ok = init_i2c();
    if (i2c_ok == XST_SUCCESS) {
        xil_printf("# [INFO] SHT30 I2C ready at 0x44\r\n");

        /* 上电先探一次；失败也不影响主循环，后面每 5 秒重试 */
        update_sht30();
        if (sht_ok) {
            xil_printf("# [INFO] SHT30 probe OK: temp100=%d hum100=%d\r\n", temp100, hum100);
        } else {
            xil_printf("# [WARN] SHT30 probe failed (err=%u); retry every 5 s\r\n", sht_err);
        }
    } else {
        xil_printf("# [WARN] SHT30 I2C init failed; using sentinel values\r\n");
        sht_ok = 0;
    }

    xil_printf("# [INFO] T=50 Hz, I/S=1 Hz, UART=115200, v1 frames\r\n");
    xil_printf("# [INFO] AXI GPIO audio energy at 0x%08X\r\n", AUDIO_GPIO_BASE);

    axi_gpio_selftest();

    /* 编码器上电后需要几秒才稳定，期间 ADC 会出毛刺，先别开检测 */
    usleep(2500000);
    detector_init();
    init_event_detect();
    xil_printf("# [INFO] event detect: threshold=%u permille, enabled\r\n", EV_THRESHOLD);
    xil_printf("# [INFO] PS detector v2: 2D chi-square (ber, kurt) + energy jump\r\n");

    read_audio_energy();
    read_event_status();
    xil_printf("# [INFO] first energy low=%u high=%u (hex %08x %08x)\r\n",
               energy_low, energy_high, energy_low, energy_high);
    xil_printf("# [DIAG] ev status: latched=%u above=%u count=%u\r\n",
               ev_latched, ev_above, ev_count);

    last_pl_ev = ev_count;      /* 以上电时的计数为基准 */

    next = now_us();

    while (1) {
        u64 t_us = now_us();

        read_audio_energy();
        read_event_status();

        /* 多维判决：谱形（BER z 分数）+ 时间结构（峭度）+ 能量突增 */
        det_feed(&g_det, energy_low, energy_high, ev_klvl);

        /* 事件状态机：PS 判决到 L2 或 PL 快路径锁存都要能开事件，但只报一次 */
        {
            int pl_new = (ev_count != last_pl_ev) ? 1 : 0;

            if (pl_new) last_pl_ev = ev_count;

            /* 开闸延时：level 必须连续 500 ms 保持 L2 才开事件，
               这样语音类声源在检出"能量间隙"后会被撤销，不会锁存。 */
            if (g_det.level >= 2U) {
                if (lvl2_ticks < 1000U) lvl2_ticks++;
            } else {
                lvl2_ticks = 0U;
            }

            if (!in_event && ((lvl2_ticks >= DET_EVENT_HOLD) || pl_new)) {
                /* 冷却窗：1.5 s 内不重复开事件，避免一个持续异响被拆成十几条 */
                if ((last_ev_us == 0ULL) || ((t_us - last_ev_us) > 1500000ULL)) {
                    in_event = 1U;
                    last_ev_us = t_us;
                    ev_total++;
                    send_e(t_us);
                }
            } else if (in_event && (g_det.level == 0U) && !ev_latched) {
                in_event = 0U;
                lvl2_ticks = 0U;
            }
        }

        /* 上电前 5 秒打印原始 32 位能量 + 频带能量比，便于对照 */
        if ((tick < 100U) && ((tick % 25U) == 0U)) {   /* 只在开机 2 秒内自检 */
            xil_printf("# [RAW] %u %08x %08x R=%u K=%u A=%u KL=%u\r\n",
                       tick, energy_low, energy_high, energy_ratio_permille(), ev_kurt, ev_above, ev_klvl);
        }

        if ((tick % FRAME_HZ) == 0U) {
            send_i();
        }

        /* 每 5 秒读一次温湿度（读失败只置无效值，不挡主循环） */
        if ((tick % (FRAME_HZ * 5U)) == 0U) {
            update_sht30();
        }

        send_t(t_us);

        /* 10 Hz 扩展诊断帧：把多维判决的中间量送出去，便于现场验证 */
        if ((tick % 5U) == 0U) {
            send_x(t_us);
        }

        if ((tick % FRAME_HZ) == 0U) {
            send_s(t_us);
            sync_pl_threshold();   /* 1 Hz 把 PS 基线同步给 PL 快路径 */
            /* update_sht30(); -- disabled until I2C blocking issue is resolved */
        }

        tick++;
        next += FRAME_PERIOD_US;

        {
            u64 now = now_us();
            if (next > now) {
                s64 wait = (s64)(next - now);
                if (wait > 0) {
                    usleep((unsigned long)wait);
                }
            } else if ((now - next) > 200000ULL) {
                next = now;
            }
        }
    }

    cleanup_platform();
    return 0;
}
