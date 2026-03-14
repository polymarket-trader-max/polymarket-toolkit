# ============================================================
# 🔒 POLYMARKET TOOLKIT PRO — Preview Stub
# Full version: https://gumroad.com/l/polymarket-toolkit-pro
# This file shows the API surface. Upgrade to Pro for the
# complete implementation (~212 lines of battle-tested code).
# ============================================================
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
    HIGH_LIQ_THRESHOLD  = 20_000
    MED_LIQ_THRESHOLD   = 5_000
    MIN_TREND_SLOPE     = 0.005
    MIN_R2              = 0.55
    STRONG_ZSCORE       = 1.8
    OVERREACTION_Z      = 2.5
    VOL_ACCEL_THRESHOLD = 0.5
    SNAPSHOT_DAY_STRONG  = 0.06
    SNAPSHOT_WEEK_STRONG = 0.12
    SNAPSHOT_OVERREACT   = 0.20

    def estimate_probability(self, market: dict) -> Optional[tuple]:
        """返回 (估计概率, 置信度, 理由) 或 None（不下注）"""
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )

    def _signal_from_series(self, yes_price: float, liquidity: float, feat) -> Optional[tuple]:
        """基于真实时序特征生成信号。"""
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )

    def _signal_from_snapshot(self, yes_price: float, liquidity: float, market: dict) -> Optional[tuple]:
        """基于 API 快照字段生成信号（无历史数据时降级使用）。"""
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )

    def _load_features(self, market: dict):
        """尝试从本地数据库加载价格特征，失败则返回 None。"""
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )
