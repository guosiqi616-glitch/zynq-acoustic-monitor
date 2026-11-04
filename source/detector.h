#ifndef DETECTOR_H
#define DETECTOR_H

// ---------------------------------------------------------------------------
// detector.h -- PS 侧多维异常检测核心（纯 C / 整数运算 / 无 Xilinx 依赖）
//
// v1.0 的判据是"BER > 固定阈值"，本质是单维、单门限、开机学一次就冻结。
// 这里换成三个正交维度的联合判决：
//
//   维度 1（谱形）  BER（高频能量占比）相对自身基线的 z 分数
//   维度 2（时间结构）峭度的 z 分数（单侧：只关心"变得更冲击"）
//   维度 3（能量）  总能量相对慢速参考的突增，作为独立的瞬态判据
//
// 维度 1 和 2 合成卡方分数 S = z0^2 + z1^2（2 自由度），
// 阈值取 chi2(2, 0.01) = 9.210 与 chi2(2, 0.001) = 13.816（Q16 定点）。
// 维度 3 不参与评分，直接抬升到 L2 —— 瞬态事件的统计形态本来就偏离高斯。
//
// 基线不再"学 10 秒然后冻结"，而是：
//   - 前 100 帧（2 s）快速收敛；
//   - 之后门控 EWMA 持续跟踪（2.5 sigma 内才更新，异常样本进不了基线）；
//   - 门外慢速跟踪（tau 约 2 min），工况缓慢漂移能被吸收；
//   - 连续 L2 超过 30 s 视为工况切换，自动重建基线，避免永久卡死在告警态。
//
// 另外每个子带维护一条"噪声底"（快降慢升），用 (E - N) 的残差算谱形，
// 相当于一个极廉价的谱减；告警门限也由噪声底推导（2 倍底噪），
// 不再依赖写死的常数。
// ---------------------------------------------------------------------------

#include <stdint.h>

typedef int32_t  det_s32;
typedef uint32_t det_u32;
typedef int64_t  det_s64;
typedef uint64_t det_u64;
typedef uint8_t  det_u8;

#define DET_NFEAT        2

#define DET_CHI2_L1      603600   /* chi2(2, 0.01)  = 9.210  -> Q16 */
#define DET_CHI2_L2      905436   /* chi2(2, 0.001) = 13.816 -> Q16 */

#define DET_LEARN_FAST   100U     /* 100 个有效帧快速收敛 */
#define DET_LEARN_TIME   500U     /* 开机后 10 s 内视为"正常工况"，允许学习 */
#define DET_N_FAST       24U      /* 快速收敛的等效窗长 */
#define DET_N_SLOW       1500U    /* 30 s，门控内正常跟踪 */
#define DET_MIN_DEV   250
#define DET_JUMP_MIN  60000 /* 麦克风模式：关闭跳变判据，只保留卡方判据 */
#define DET_CONFIRM_L1   3U       /* 60 ms */
#define DET_CONFIRM_L2   6U       /* 120 ms */
#define DET_RELEASE      40U      /* 800 ms 无异常才降级 */
#define DET_REGRESS      1500U    /* 30 s 持续 L2 -> 重建基线 */
#define DET_WARMUP       50U      /* 1 s 后才允许突增判据 */
#define DET_ENV_WIN      50U      /* 能量包络窗：50 帧 @50 Hz = 1 s */
#define DET_GAP_LEVEL    300U     /* 低于峰值 30% 视为“间隙” */
#define DET_GAP_RATIO    180U     /* 间隙占比 >18% 判为语音类（千分比） */
#define DET_EVENT_HOLD   25U      /* level>=2 连续 500 ms 才开事件 */
#define DET_ARM_TICKS    100U     /* 2 s 后才允许报警，给基线收敛时间 */
#define DET_BER_DEFAULT  753      /* 白噪声理论值 8/(2.9+8)=0.734 -> 实测 753 */
#define DET_BER_SD       30       /* 默认标准差，约 4% */

typedef struct {
    /* ---- 配置 ---- */
    det_u32 gate_min;          /* 绝对能量下限（默认 3000） */
    det_u32 kurt_min;          /* den 高于此值才信峭度（默认 40000） */

    /* ---- 特征与基线 ---- */
    det_s32 f[DET_NFEAT];
    det_s32 mu[DET_NFEAT];
    det_s32 var[DET_NFEAT];
    det_s32 sd[DET_NFEAT];
    det_s64 acc_mu[DET_NFEAT];
    det_s64 acc_var[DET_NFEAT];
    det_u32 n_upd;

    /* ---- 噪声底 ---- */
    det_u32 nf_low;
    det_u32 nf_high;
    det_u8  nf_valid;

    /* ---- 能量突增 ---- */
    det_u32 slow_den;
    det_u32 ticks;
    det_u8  warm;

    /* ---- 能量包络间隙（区分语音类不连续声源与稳态机器声）---- */
    det_u32 env[DET_ENV_WIN];
    det_u8  env_i;
    det_u32 low_ticks;         /* BER 连续低于基线的帧数 */
    det_u8  speech_like;

    /* ---- 判决 ---- */
    det_s32 score;             /* Q16 */
    det_s32 score_peak;
    det_u8  level;             /* 0..3 */
    det_u32 c1;
    det_u32 c2;
    det_u32 c_rel;
    det_u32 c_level;
    det_u32 relearn;           /* 重建基线的剩余窗口（tick） */

    /* ---- 输出 ---- */
    det_u32 den;
    det_u32 ber;
    det_u32 ber_show;          /* 最近一次有效 BER，用于显示（避免跳 0） */
    det_u32 hold;              /* 无效帧计数，超过 400 ms 回到默认值 */
    det_u32 ber_res;
    det_u32 gate;
    det_u8  valid;
    det_u8  jump;

    /* ---- 温度融合 ---- */
    det_s32 t_base;
    det_u32 t_n;
    det_u8  overheat;
} det_t;

// ---------------------------------------------------------------------------
static det_u32 det_isqrt(det_u64 v)
{
    det_u64 x;
    det_u64 y;

    if (v == 0U) return 0U;
    x = v;
    y = (x + 1U) >> 1;
    while (y < x) {
        x = y;
        y = (x + v / x) >> 1;
    }
    return (det_u32)x;
}

static det_s32 det_abs32(det_s32 v)
{
    return (v < 0) ? -v : v;
}

static void det_init(det_t *d)
{
    int i;

    d->gate_min = 300U;      /* 麦克风模式：环境噪声约 226 */
    d->kurt_min = 40000U;

    for (i = 0; i < DET_NFEAT; i++) {
        d->f[i]       = 0;
        d->mu[i]      = 0;
        d->var[i]     = 0;
        d->sd[i]      = 1;
        d->acc_mu[i]  = 0;
        d->acc_var[i] = 0;
    }

    /* 冷启动基线：白噪声下 BER 由滤波器带宽决定，理论值 0.734，
       实测底噪 0.753。先用它兜底，学到真实环境后再覆盖。
       这样"开机即静音、永远学不到有效样本"时系统仍然可用。 */
    d->mu[0]  = DET_BER_DEFAULT;
    d->var[0] = DET_BER_SD * DET_BER_SD;
    d->sd[0]  = DET_BER_SD;
    d->n_upd  = 0;

    d->nf_low   = 0;
    d->nf_high  = 0;
    d->nf_valid = 0;

    d->slow_den = 0;
    d->ticks    = 0;
    d->warm     = 0;

    d->score      = 0;
    d->score_peak = 0;
    d->level      = 0;
    d->c1 = 0; d->c2 = 0; d->c_rel = 0; d->c_level = 0;
    d->relearn    = 0;

    d->den = 0; d->ber = 0; d->ber_res = 0; d->gate = 0;
    d->ber_show = DET_BER_DEFAULT;
    d->hold     = 0;
    d->valid = 0; d->jump = 0;

    d->t_base   = -32768;
    d->t_n      = 0;
    d->overheat = 0;

    for (i = 0; i < (int)DET_ENV_WIN; i++) d->env[i] = 0;
    d->env_i       = 0U;
    d->low_ticks   = 0U;
    d->speech_like = 0U;
}

// ---------------------------------------------------------------------------
// 基线更新：用累加器做整数 EWMA，避免 diff/n 在 n 很大时被截断成 0。
//   acc += diff; step = acc / n; mu += step; acc -= step * n;
// ---------------------------------------------------------------------------
static void det_update(det_t *d, det_u32 n)
{
    int i;

    for (i = 0; i < DET_NFEAT; i++) {
        det_s64 diff = (det_s64)d->f[i] - (det_s64)d->mu[i];
        det_s64 step;

        d->acc_mu[i] += diff;
        step = d->acc_mu[i] / (det_s64)n;
        d->mu[i] += (det_s32)step;
        d->acc_mu[i] -= step * (det_s64)n;

        d->acc_var[i] += diff * diff - (det_s64)d->var[i];
        step = d->acc_var[i] / (det_s64)n;
        d->var[i] += (det_s32)step;
        d->acc_var[i] -= step * (det_s64)n;
        if (d->var[i] < 0) d->var[i] = 0;

        {
            det_s32 s     = (det_s32)det_isqrt((det_u64)d->var[i]);
            det_s32 floor_v = det_abs32(d->mu[i]) / 50 + 1;

            if (s < floor_v) s = floor_v;    /* sigma 下限，防除零与爆分 */
            d->sd[i] = s;
        }
    }
    d->n_upd++;
}

// ---------------------------------------------------------------------------
// 温度融合：只在 SHT30 出新读数时调用（每 5 s 一次）
// 温度是慢变量，几乎不会被噪声误触发，用来给声学判据做置信度加成。
// ---------------------------------------------------------------------------
static void det_on_temp(det_t *d, det_s32 t100)
{
    if (t100 == -32768) return;          /* 无效值 */

    d->t_n++;
    if (d->t_n == 1U) {
        d->t_base = t100;
        return;
    }
    /* 120 个读数 x 5 s = 10 min 慢速基线 */
    d->t_base += (t100 - d->t_base) / 120;

    if (d->t_n > 12U)                    /* 预热 1 min */
        d->overheat = ((t100 - d->t_base) > 300) ? 1U : 0U;   /* +3.0 摄氏度 */
}

// ---------------------------------------------------------------------------
// 主入口：每个 20 ms 帧调用一次
// ---------------------------------------------------------------------------
static void det_feed(det_t *d, det_u32 e_low, det_u32 e_high,
                     det_u32 kurt_lvl)
{
    det_u32 den = e_low + e_high;
    det_u32 gate;
    det_s32 z;
    det_s32 s;
    det_s32 target;
    det_s32 ber_dev;
    int i;
    int inside;
    int valid;
    int above1;
    int above2;
    det_u32 n;

    if (d->ticks < 0xFFFFFFFFU) d->ticks++;

    /* ---- 1. 噪声底：快降慢升 ---- */
    if (!d->nf_valid) {
        d->nf_low   = e_low;
        d->nf_high  = e_high;
        d->nf_valid = 1U;
    }
    if (e_low < d->nf_low) {
        det_u32 st = (d->nf_low - e_low) >> 3;
        d->nf_low -= (st ? st : 1U);
    } else if (d->level == 0U) {
        d->nf_low += (e_low - d->nf_low) >> 12;
    }
    if (e_high < d->nf_high) {
        det_u32 st = (d->nf_high - e_high) >> 3;
        d->nf_high -= (st ? st : 1U);
    } else if (d->level == 0U) {
        d->nf_high += (e_high - d->nf_high) >> 12;
    }

    /* ---- 2. 门限：只用绝对下限 ----
       早期版本用 "2 倍底噪" 作为门限，在持续背景噪声下会把噪声帧本身判为
       无效（den 恒小于 2*nf），低信噪比异响永远进不了判决。门限的职责只是
       排除"没有信号"的帧，检测本身交给统计判决，所以这里改成绝对下限。 */
    gate = d->gate_min;
    d->gate  = gate;
    d->den   = den;
    valid    = (den >= gate) ? 1 : 0;
    d->valid = valid ? 1U : 0U;

    /* ---- 2.5 语音类判定：BER 持续低于基线 ----
       语音能量集中在 4 kHz 以下，BER 会长时间贴地（0~几十）；
       机器啸叫的 BER 只会更高，冲击类只持续一两百毫秒。
       因此"BER 低于基线 3 sigma 且连续 1 s 以上"才判为语音类。 */
    /* 注意：不能用 valid 作为条件——语音的停顿会让 valid 掉 0，
       计数器被反复清零，判据永远不成立。静音本身也不该算异常，所以照常累计。 */
    if ((det_s32)d->ber < (d->mu[0] - 90)) {
        if (d->low_ticks < 1000U) d->low_ticks++;
    } else {
        d->low_ticks = 0U;
    }
    d->speech_like = (d->low_ticks > 22U) ? 1U : 0U;   /* 440 ms，早于事件开闸 */

    /* ---- 3. 特征 ---- */
    d->ber     = 0;
    d->ber_res = 0;
    if (den > 0U) {
        det_u32 rl   = (e_low  > d->nf_low ) ? (e_low  - d->nf_low ) : 0U;
        det_u32 rh   = (e_high > d->nf_high) ? (e_high - d->nf_high) : 0U;
        det_u32 rden = rl + rh;

        d->ber = (det_u32)(((det_u64)e_high * 1000U) / (det_u64)den);
        if (rden > (gate >> 1))
            d->ber_res = (det_u32)(((det_u64)rh * 1000U) / (det_u64)rden);
    }
    /* 门限以下（静音/无信号）：特征置中性，既不参与打分也不进基线。
       早期版本在这里直接塞 0，导致基线方差被 0 和 750 的混合撑到 147，
       门控失效、z 分数失灵——这是必须避免的污染。 */
    d->f[0] = valid ? (det_s32)d->ber : d->mu[0];
    d->f[1] = (valid && den >= d->kurt_min) ? (det_s32)kurt_lvl : d->mu[1];
    if (valid) {
        d->ber_show = d->ber;      /* 显示用：保持最近一次有效值 */
        d->hold     = 0U;
    } else if (++d->hold > 20U) {
        d->ber_show = DET_BER_DEFAULT;   /* 400 ms 无有效帧 -> 回到底噪值 */
    }

    /* ---- 4. 慢速能量参考（突增判据）---- */
    {
        det_s32 delta = (det_s32)den - (det_s32)d->slow_den;
        d->slow_den = (det_u32)((det_s32)d->slow_den + (delta >> 7));
    }
    if (d->ticks > DET_WARMUP) d->warm = 1U;

    d->jump = 0;
    if (d->warm && valid && (d->ticks > DET_ARM_TICKS) &&
        (d->slow_den > 0U) && (den > (2U * d->slow_den)) &&
        (den > DET_JUMP_MIN))
        d->jump = 1U;

    /* ---- 5. 卡方分数 ---- */
    s = 0;
    for (i = 0; i < DET_NFEAT; i++) {
        det_s32 diff = d->f[i] - d->mu[i];

        if (i == 1 && diff < 0) diff = 0;      /* 峭度单侧 */
        z = (diff * 256) / d->sd[i];
        if (z >  16384) z =  16384;
        if (z < -16384) z = -16384;
        s += z * z;
    }
    d->score = s;
    if (s > d->score_peak) d->score_peak = s;

    /* ---- 6. 分级 ---- */
    /* 低信噪比时 BER 抖动会让 z 分数虚高，加一条绝对偏差下限兜底 */
    ber_dev = d->ber - d->mu[0];
    if (ber_dev < 0) ber_dev = -ber_dev;

    above1 = (s > DET_CHI2_L1) && valid && (d->ticks > DET_ARM_TICKS) && (ber_dev >= DET_MIN_DEV);
    above2 = (s > DET_CHI2_L2) && valid && (d->ticks > DET_ARM_TICKS) && (ber_dev >= DET_MIN_DEV);

    if (above1) {
        d->c1++;
        if (above2) d->c2++; else d->c2 = 0U;
    } else {
        d->c1 = 0U;
        d->c2 = 0U;
    }

    target = 0;
    if (d->c1 >= DET_CONFIRM_L1) target = 1;
    if (d->c2 >= DET_CONFIRM_L2) target = 2;
    if ((d->c2 >= DET_CONFIRM_L2) &&
        ((kurt_lvl >= 13U) || (d->overheat != 0U) || (d->ber >= 900U)))
        target = 3;
    if (d->jump && target < 2) target = 2;     /* 瞬态直接抬到 L2 */

    /* 语音类声源（能量包络有明显间隙）：直接撤销，不进入事件锁存。
       机器啸叫/摩擦/啮合是连续声源，不会出现 18% 以上的能量间隙。 */
    if (d->speech_like != 0U) {
        target   = 0;
        d->level = 0U;
        d->c1 = 0U; d->c2 = 0U; d->c_rel = 0U;
        d->score_peak = 0;
    }

    if (target > (det_s32)d->level) {
        d->level = (det_u8)target;
        d->c_rel = 0U;
    } else if (target == 0) {
        if (d->level != 0U) {
            if (++d->c_rel >= DET_RELEASE) {
                d->level      = 0U;
                d->c_rel      = 0U;
                d->score_peak = 0;
            }
        }
    } else {
        d->c_rel = 0U;
    }

    if (d->relearn != 0U) d->relearn--;

    /* ---- 7. 工况切换：持续 L2 超过 30 s 未恢复，重建基线 ---- */
    if (d->level >= 2U) {
        d->c_level++;
        if (d->c_level > DET_REGRESS) {
            d->n_upd      = 0U;
            d->acc_mu[0]  = 0; d->acc_mu[1]  = 0;
            d->acc_var[0] = 0; d->acc_var[1] = 0;
            d->level      = 0U;
            d->c1 = 0U; d->c2 = 0U; d->c_rel = 0U; d->c_level = 0U;
            d->score_peak = 0;
            d->relearn    = DET_LEARN_TIME;   /* 重新开一个 10 s 学习窗 */
        }
    } else {
        d->c_level = 0U;
    }

    /* ---- 8. 基线：正常态才更新 ---- */
    inside = 1;
    for (i = 0; i < DET_NFEAT; i++) {
        det_s32 diff = d->f[i] - d->mu[i];
        if (diff < 0) diff = -diff;
        if (diff * 2 > d->sd[i] * 5) inside = 0;    /* 2.5 sigma 门控 */
    }

    /* 学习门控：
       - 开机后 DET_LEARN_TIME（10 s）内，假定当前就是正常工况，允许学习；
       - 重建基线后的 10 s 窗口同样允许；
       - 其余时间只在 level == 0 且落在 2.5 sigma 门控内才学。
       门外一律不学：静音里偶发的 ber=1000 毛刺就是这样被挡在基线之外的
       （早期版本用"门外慢速跟踪"，结果把 sd 从 30 抬到 74，判据变钝）。 */
    if (valid &&
        ((d->ticks < DET_LEARN_TIME) || (d->relearn != 0U) ||
         ((d->level == 0U) && inside))) {
        n = (d->n_upd < DET_LEARN_FAST) ? DET_N_FAST : DET_N_SLOW;
        det_update(d, n);
    }
}

// ---------------------------------------------------------------------------
// 给 PL 用的比值阈值（千分比）：mu + 2 sigma
// ---------------------------------------------------------------------------
static det_u32 det_ratio_thr(const det_t *d)
{
    det_s32 thr = d->mu[0] + 2 * d->sd[0];

    if (thr < 200) thr = 200;
    if (thr > 950) thr = 950;
    return (det_u32)thr;
}

#endif /* DETECTOR_H */
