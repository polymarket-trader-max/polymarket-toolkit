"""
价格时序分析引擎 - 从本地快照数据库提取真实动量指标

提供函数：
  analyze_series(records) -> PriceFeatures  计算全套特征
  build_market_features(market_id) -> PriceFeatures | None  一键分析
"""

import math
import sys
import os
from dataclasses import dataclass, field
from typing import Optional

# 确保能找到 price_tracker
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class PriceFeatures:
    """一个市场的价格时序特征集合。"""

    # 基础
    n_points: int = 0
    hours_tracked: float = 0.0
    first_price: float = 0.0
    last_price: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    price_range: float = 0.0          # max - min

    # 动量 (velocity)
    velocity_1h: float = 0.0          # 最近1小时价格变化
    velocity_6h: float = 0.0          # 最近6小时价格变化
    velocity_24h: float = 0.0         # 最近24小时价格变化
    velocity_total: float = 0.0       # 全程变化（first→last）

    # 加速度 (是否在加速/减速)
    acceleration: float = 0.0         # (recent_velocity - older_velocity)

    # 波动率
    volatility_hourly: float = 0.0    # 小时级别标准差
    volatility_daily: float = 0.0     # 日级别标准差（年化）

    # Z-score：当前价格偏离均值的程度
    zscore: float = 0.0               # (last - mean) / std

    # 线性趋势
    trend_slope: float = 0.0          # 线性回归斜率（每小时）
    trend_r2: float = 0.0             # 拟合优度（0=噪声，1=完美趋势）

    # 成交量特征
    avg_vol_24h: float = 0.0
    vol_acceleration: float = 0.0     # 近期成交量变化

    # 价格位置
    percentile_position: float = 0.5  # 当前价格在历史区间的位置 (0=最低，1=最高)
    mean_reversion_signal: float = 0.0  # 正=偏高，负=偏低

    # 模式标记
    is_trending: bool = False         # 有明显趋势
    is_volatile: bool = False         # 波动异常大
    is_at_extreme: bool = False       # 价格在极端区域
    data_quality: str = "none"        # "none" | "sparse" | "ok" | "good"

    raw_series: list = field(default_factory=list, repr=False)


def analyze_series(records: list[dict]) -> PriceFeatures:
    """
    对时序记录列表做完整分析。
    
    records: [{"ts": unix, "yes": float, "vol24h": float, "liq": float}, ...]
    """
    feat = PriceFeatures()

    if not records:
        return feat

    # 去重 + 排序
    seen_ts = set()
    clean = []
    for r in records:
        ts = r.get("ts", 0)
        if ts and ts not in seen_ts:
            seen_ts.add(ts)
            clean.append(r)
    clean.sort(key=lambda x: x["ts"])

    feat.n_points = len(clean)
    feat.raw_series = clean

    # 数据质量
    if feat.n_points < 2:
        feat.data_quality = "none"
        return feat
    elif feat.n_points < 4:
        feat.data_quality = "sparse"
    elif feat.n_points < 12:
        feat.data_quality = "ok"
    else:
        feat.data_quality = "good"

    prices = [r["yes"] for r in clean]
    timestamps = [r["ts"] for r in clean]
    hours = [(t - timestamps[0]) / 3600.0 for t in timestamps]

    feat.first_price = prices[0]
    feat.last_price = prices[-1]
    feat.min_price = min(prices)
    feat.max_price = max(prices)
    feat.price_range = feat.max_price - feat.min_price
    feat.hours_tracked = hours[-1] if hours else 0.0

    now_ts = timestamps[-1]
    feat.velocity_total = feat.last_price - feat.first_price

    # ── 速度（各时间窗口）────────────────────────────────────
    def price_n_hours_ago(hours_back: float) -> Optional[float]:
        target_ts = now_ts - hours_back * 3600
        # 找最近的记录
        best = None
        best_diff = float("inf")
        for r in clean:
            diff = abs(r["ts"] - target_ts)
            if diff < best_diff:
                best_diff = diff
                best = r
        if best and best_diff < hours_back * 3600 * 0.5:  # 允许50%时间误差
            return best["yes"]
        return None

    p1h = price_n_hours_ago(1)
    p6h = price_n_hours_ago(6)
    p24h = price_n_hours_ago(24)

    if p1h is not None:
        feat.velocity_1h = feat.last_price - p1h
    if p6h is not None:
        feat.velocity_6h = feat.last_price - p6h
    if p24h is not None:
        feat.velocity_24h = feat.last_price - p24h

    # ── 加速度 ────────────────────────────────────────────────
    # 比较近半和远半的速度
    mid = len(clean) // 2
    if mid >= 1 and len(clean) > 2:
        recent_vel = prices[-1] - prices[mid]
        older_vel = prices[mid] - prices[0]
        # 单位化到同等时间长度
        t_recent = (timestamps[-1] - timestamps[mid]) / 3600.0 or 1
        t_older = (timestamps[mid] - timestamps[0]) / 3600.0 or 1
        feat.acceleration = (recent_vel / t_recent) - (older_vel / t_older)

    # ── 波动率 ────────────────────────────────────────────────
    if len(prices) >= 3:
        # 逐点收益率
        returns = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        if returns:
            mean_ret = sum(returns) / len(returns)
            var = sum((r - mean_ret)**2 for r in returns) / len(returns)
            std = math.sqrt(var)
            feat.volatility_hourly = std

            # 折算为日波动（假设每15分钟采一次，一天96点）
            intervals_per_day = 24.0 / max(0.01, feat.hours_tracked / max(1, feat.n_points - 1))
            feat.volatility_daily = std * math.sqrt(intervals_per_day)

    # ── Z-score ───────────────────────────────────────────────
    if len(prices) >= 4:
        mean_p = sum(prices) / len(prices)
        var_p = sum((p - mean_p)**2 for p in prices) / len(prices)
        std_p = math.sqrt(var_p) if var_p > 0 else 1e-6
        feat.zscore = (feat.last_price - mean_p) / std_p
        feat.mean_reversion_signal = (mean_p - feat.last_price) / std_p

    # ── 线性趋势（最小二乘）──────────────────────────────────
    if len(clean) >= 4:
        n = len(clean)
        x = hours
        y = prices
        sx = sum(x)
        sy = sum(y)
        sxy = sum(xi * yi for xi, yi in zip(x, y))
        sxx = sum(xi**2 for xi in x)
        denom = n * sxx - sx**2
        if abs(denom) > 1e-10:
            slope = (n * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / n
            feat.trend_slope = slope  # 每小时变化
            # R²
            y_pred = [slope * xi + intercept for xi in x]
            ss_res = sum((yi - yp)**2 for yi, yp in zip(y, y_pred))
            ss_tot = sum((yi - sy/n)**2 for yi in y)
            feat.trend_r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # ── 成交量特征 ────────────────────────────────────────────
    vols = [r.get("vol24h", 0) for r in clean]
    if vols:
        feat.avg_vol_24h = sum(vols) / len(vols)
        # 成交量加速度：近半均值 vs 远半均值
        mid = len(vols) // 2
        if mid >= 1:
            recent_vol_avg = sum(vols[mid:]) / max(1, len(vols) - mid)
            older_vol_avg = sum(vols[:mid]) / max(1, mid)
            feat.vol_acceleration = (recent_vol_avg - older_vol_avg) / max(1, older_vol_avg)

    # ── 价格位置 ──────────────────────────────────────────────
    if feat.price_range > 1e-6:
        feat.percentile_position = (feat.last_price - feat.min_price) / feat.price_range
    else:
        feat.percentile_position = 0.5

    # ── 模式标记 ──────────────────────────────────────────────
    feat.is_trending = feat.trend_r2 > 0.6 and abs(feat.trend_slope) > 0.005  # >0.5%/h 且拟合好
    feat.is_volatile = feat.volatility_daily > 0.15                            # 日波动>15%
    feat.is_at_extreme = feat.last_price < 0.08 or feat.last_price > 0.92

    return feat


def build_market_features(market_id: str, days_back: int = 7) -> Optional[PriceFeatures]:
    """从本地数据库加载并分析一个市场的价格特征。"""
    from price_tracker import load_price_series
    records = load_price_series(market_id, days_back=days_back)
    if not records:
        return None
    return analyze_series(records)


def features_summary(feat: PriceFeatures) -> str:
    """人类可读的特征摘要。"""
    if feat.data_quality == "none":
        return "无历史数据"
    parts = [
        f"数据:{feat.n_points}点/{feat.hours_tracked:.1f}h ({feat.data_quality})",
        f"价格:{feat.first_price:.3f}→{feat.last_price:.3f}({feat.velocity_total:+.3f})",
        f"趋势:{feat.trend_slope*100:+.2f}%/h R²={feat.trend_r2:.2f}",
        f"波动:{feat.volatility_daily:.1%}/日",
        f"Zscore:{feat.zscore:+.2f}",
    ]
    flags = []
    if feat.is_trending:
        flags.append("↗趋势")
    if feat.is_volatile:
        flags.append("⚡高波动")
    if feat.is_at_extreme:
        flags.append("⚠极端位")
    if flags:
        parts.append(" ".join(flags))
    return " | ".join(parts)
