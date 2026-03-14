"""
动量信号 v2 - 基于真实价格时序数据

数据来源优先级：
  1. 本地时序数据库（price_tracker 积累）→ 完整特征（斜率/加速度/波动率/Z-score）
  2. API 快照字段（day_change / week_change）→ 降级回退模式

策略矩阵：
  高流动性 + 强趋势 + 高R²      → 跟随趋势（最强信号）
  高流动性 + 正向加速度         → 动量加仓
  低流动性 + 过度反应 + 高Z-score → 均值回归
  任意 + 成交量激增 + 方向一致  → 顺势
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .base import BaseSignalGenerator
from typing import Optional


class MomentumSignalGenerator(BaseSignalGenerator):
    """
    动量信号生成器 v2，支持真实时序数据。
    """

    # ── 阈值配置 ─────────────────────────────────────────────
    HIGH_LIQ_THRESHOLD  = 20_000      # 高流动性: >$20k
    MED_LIQ_THRESHOLD   = 5_000       # 中等流动性
    MIN_TREND_SLOPE     = 0.005       # 每小时变化 > 0.5% 才算有趋势
    MIN_R2              = 0.55        # 趋势拟合优度
    STRONG_ZSCORE       = 1.8         # Z-score 绝对值 > 1.8 认为偏离显著
    OVERREACTION_Z      = 2.5         # 超过 2.5σ 视为过度反应
    VOL_ACCEL_THRESHOLD = 0.5         # 成交量加速 > 50%

    # ── 降级模式（仅快照字段）────────────────────────────────
    SNAPSHOT_DAY_STRONG  = 0.06       # 单日变化 > 6%
    SNAPSHOT_WEEK_STRONG = 0.12       # 单周变化 > 12%
    SNAPSHOT_OVERREACT   = 0.20       # 单日 > 20% 视为过度反应

    def estimate_probability(self, market: dict) -> Optional[tuple[float, float, str]]:
        """返回 (估计概率, 置信度, 理由) 或 None（不下注）"""
        yes_price = market["yes_price"]
        liquidity  = market.get("liquidity", 0)

        # ── 优先用真实时序数据 ────────────────────────────────
        feat = self._load_features(market)

        if feat is not None and feat.data_quality in ("ok", "good"):
            result = self._signal_from_series(yes_price, liquidity, feat)
            if result is not None:
                return result

        # ── 降级：用 API 快照字段 ─────────────────────────────
        return self._signal_from_snapshot(yes_price, liquidity, market)

    # ─────────────────────────────────────────────────────────
    # 真实时序模式
    # ─────────────────────────────────────────────────────────

    def _signal_from_series(self, yes_price: float, liquidity: float, feat) -> Optional[tuple]:
        """基于真实时序特征生成信号。"""
        reasons = []
        est_prob = yes_price

        slope   = feat.trend_slope        # 每小时变化
        r2      = feat.trend_r2
        zscore  = feat.zscore
        vol_acc = feat.vol_acceleration
        accel   = feat.acceleration
        v24h    = feat.velocity_24h       # 24h 价格变化

        # ── 规则1：强趋势跟随（高流动性 + 好R²）──────────────
        if liquidity >= self.HIGH_LIQ_THRESHOLD and r2 >= self.MIN_R2 and abs(slope) >= self.MIN_TREND_SLOPE:
            # 跟随斜率方向，幅度与R²和流动性成比例
            follow_adj = slope * 6 * r2  # 6小时预期变化 × 拟合质量
            follow_adj = max(-0.15, min(0.15, follow_adj))
            est_prob = max(0.01, min(0.99, yes_price + follow_adj))
            confidence = 0.60 + r2 * 0.15  # R²越好越自信

            dir_symbol = "↑" if slope > 0 else "↓"
            reasons.append(
                f"[时序]趋势{dir_symbol} slope={slope*100:+.2f}%/h R²={r2:.2f} liq=${liquidity:,.0f}"
            )

            # 加速度加成
            if (accel > 0 and slope > 0) or (accel < 0 and slope < 0):
                confidence = min(0.80, confidence + 0.08)
                reasons.append(f"加速度一致({accel*100:+.2f}%/h²)")

            # Z-score 加成（顺方向）
            if slope > 0 and zscore > 0.5:
                confidence = min(0.80, confidence + 0.04)
            elif slope < 0 and zscore < -0.5:
                confidence = min(0.80, confidence + 0.04)

            return est_prob, confidence, " | ".join(reasons)

        # ── 规则2：均值回归（Z-score过大）──────────────────────
        if abs(zscore) >= self.STRONG_ZSCORE and feat.n_points >= 4:
            # 偏高→做空 YES（等均值回归），偏低→做多 YES
            revert_target = feat.first_price * 0.3 + (feat.min_price + feat.max_price) / 2 * 0.7
            revert_adj = (revert_target - yes_price) * 0.4
            est_prob = max(0.01, min(0.99, yes_price + revert_adj))
            confidence = 0.48 + min(0.15, (abs(zscore) - self.STRONG_ZSCORE) * 0.06)

            if abs(zscore) >= self.OVERREACTION_Z:
                confidence = min(0.70, confidence + 0.10)
                reasons.append(f"[时序]严重过度反应 Zscore={zscore:+.2f}σ 均值回归")
            else:
                reasons.append(f"[时序]均值回归 Zscore={zscore:+.2f}σ")

            if liquidity < self.MED_LIQ_THRESHOLD:
                confidence = min(0.65, confidence + 0.05)
                reasons.append(f"低流动性强化信号(${liquidity:,.0f})")

            return est_prob, confidence, " | ".join(reasons)

        # ── 规则3：成交量激增 + 价格方向 ────────────────────────
        if vol_acc >= self.VOL_ACCEL_THRESHOLD and abs(v24h) >= 0.04:
            follow_adj = v24h * 0.25
            est_prob = max(0.01, min(0.99, yes_price + follow_adj))
            confidence = 0.50
            reasons.append(
                f"[时序]成交量激增{vol_acc:.0%} + 24h动量{v24h:+.1%}"
            )
            return est_prob, confidence, " | ".join(reasons)

        # ── 规则4：弱趋势（仅有稀疏数据）──────────────────────
        if abs(slope) >= self.MIN_TREND_SLOPE * 0.5 and r2 >= 0.40:
            follow_adj = slope * 4 * r2 * 0.5
            est_prob = max(0.01, min(0.99, yes_price + follow_adj))
            confidence = 0.42
            reasons.append(f"[时序]弱趋势 slope={slope*100:+.2f}%/h R²={r2:.2f}")
            return est_prob, confidence, " | ".join(reasons)

        return None

    # ─────────────────────────────────────────────────────────
    # 降级模式（仅快照）
    # ─────────────────────────────────────────────────────────

    def _signal_from_snapshot(self, yes_price: float, liquidity: float, market: dict) -> Optional[tuple]:
        """基于 API 快照字段生成信号（无历史数据时降级使用）。"""
        day_change  = market.get("day_change", 0)
        week_change = market.get("week_change", 0)
        volume_24h  = market.get("volume_24h", 0)
        spread      = market.get("spread", 0.01)

        if abs(day_change) < 0.05 and abs(week_change) < 0.10:
            return None  # 无明显动量

        reasons = []
        est_prob = yes_price

        # 场景1：高流动性 + 强日动量
        if liquidity >= self.HIGH_LIQ_THRESHOLD and abs(day_change) >= self.SNAPSHOT_DAY_STRONG:
            follow_adj = day_change * 0.35
            est_prob = max(0.01, min(0.99, yes_price + follow_adj))
            confidence = 0.52
            reasons.append(f"[快照]高流动性动量(日{day_change:+.1%} liq=${liquidity:,.0f})")

            if abs(week_change) >= self.SNAPSHOT_WEEK_STRONG and (
                (week_change > 0 and day_change > 0) or (week_change < 0 and day_change < 0)
            ):
                consensus_adj = week_change * 0.15
                est_prob = max(0.01, min(0.99, est_prob + consensus_adj))
                confidence = 0.60
                reasons.append(f"日周一致(周{week_change:+.1%})")

            return est_prob, confidence, " | ".join(reasons)

        # 场景2：低流动性 + 过度反应
        if abs(day_change) >= self.SNAPSHOT_OVERREACT and liquidity < self.HIGH_LIQ_THRESHOLD:
            revert_adj = -day_change * 0.30
            est_prob = max(0.01, min(0.99, yes_price + revert_adj))
            confidence = 0.48
            reasons.append(f"[快照]低流动性过度反应(日{day_change:+.1%}→均值回归)")
            return est_prob, confidence, " | ".join(reasons)

        # 场景3：成交量/流动性比异常
        if liquidity > 0 and volume_24h > liquidity * 0.5 and abs(day_change) >= 0.05:
            vol_signal_adj = day_change * 0.25
            est_prob = max(0.01, min(0.99, yes_price + vol_signal_adj))
            confidence = 0.46
            reasons.append(f"[快照]成交量/流动性比={volume_24h/liquidity:.1f}x 顺势")
            return est_prob, confidence, " | ".join(reasons)

        # 场景4：中等动量
        if abs(day_change) >= self.SNAPSHOT_DAY_STRONG * 1.5:
            follow_adj = day_change * 0.28
            est_prob = max(0.01, min(0.99, yes_price + follow_adj))
            confidence = 0.43
            reasons.append(f"[快照]中等动量(日{day_change:+.1%})")
            return est_prob, confidence, " | ".join(reasons)

        return None

    # ─────────────────────────────────────────────────────────
    # 辅助
    # ─────────────────────────────────────────────────────────

    def _load_features(self, market: dict):
        """尝试从本地数据库加载价格特征，失败则返回 None。"""
        try:
            from signals.price_series import build_market_features
            feat = build_market_features(market["id"], days_back=7)
            return feat
        except Exception:
            return None
