"""
组合信号 - 对多个子信号做加权集成（Ensemble）

逻辑：
- 多个信号同向 → 提高置信度
- 信号冲突 → 降低置信度或跳过
- 最终输出加权平均概率和集成置信度
"""

from .base import BaseSignalGenerator, Signal
from .rules import RulesBasedSignalGenerator
from .momentum import MomentumSignalGenerator
from .liquidity import LiquiditySignalGenerator
from typing import Optional


class ComboSignalGenerator:
    """
    集成信号生成器，组合多个子信号。
    """

    # 各信号权重
    WEIGHTS = {
        "RulesBasedSignalGenerator": 0.40,
        "MomentumSignalGenerator": 0.35,
        "LiquiditySignalGenerator": 0.25,
    }

    def __init__(self):
        self.generators = [
            RulesBasedSignalGenerator(),
            MomentumSignalGenerator(),
            LiquiditySignalGenerator(),
        ]

    def generate(self, market: dict) -> Optional[Signal]:
        """生成集成信号"""
        signals = []
        for gen in self.generators:
            sig = gen.generate(market)
            if sig is not None and sig.direction != "SKIP":
                signals.append(sig)

        if not signals:
            return None

        # 只有一个信号
        if len(signals) == 1:
            return signals[0]

        # 多信号集成
        yes_price = market["yes_price"]

        # 检查方向一致性
        directions = [s.direction for s in signals]
        yes_count = directions.count("YES")
        no_count = directions.count("NO")

        if yes_count > 0 and no_count > 0:
            # 冲突信号 - 降低置信度
            # 取多数方向
            dominant = "YES" if yes_count > no_count else "NO"
            dominant_signals = [s for s in signals if s.direction == dominant]
        else:
            dominant = "YES" if yes_count > no_count else "NO"
            dominant_signals = signals

        # 加权平均概率
        total_weight = 0.0
        weighted_prob = 0.0
        weighted_conf = 0.0
        for sig in dominant_signals:
            w = self.WEIGHTS.get(sig.source, 0.33)
            weighted_prob += sig.estimated_prob * w * sig.confidence
            weighted_conf += sig.confidence * w
            total_weight += w

        if total_weight == 0:
            return dominant_signals[0]

        est_prob = weighted_prob / total_weight
        confidence = min(0.85, weighted_conf / total_weight)

        # 多信号同向提升置信度
        if len(dominant_signals) >= 2:
            confidence = min(0.85, confidence * 1.15)

        # 用集成参数生成最终信号
        combo_market = dict(market)
        combo_market["yes_price"] = yes_price

        # 临时注入估计，用第一个信号的生成器框架
        base_gen = dominant_signals[0]
        ev_yes = est_prob - yes_price

        # 仓位
        bet_price = yes_price if dominant == "YES" else (1 - yes_price)
        bet_prob = est_prob if dominant == "YES" else (1 - est_prob)
        if bet_price > 0:
            b = (1 / bet_price) - 1
            kelly_raw = (b * bet_prob - (1 - bet_prob)) / b
            kelly_frac = max(0, min(kelly_raw, 1)) * 0.25
        else:
            kelly_frac = 0

        edge = abs(est_prob - yes_price)
        if edge < 0.05 or confidence < 0.40:
            return None

        reasonings = [f"[{s.source.replace('SignalGenerator','')}] {s.reasoning}" for s in dominant_signals]

        return Signal(
            market_id=market["id"],
            question=market["question"],
            category=market["category"],
            market_yes_price=yes_price,
            estimated_prob=est_prob,
            confidence=confidence,
            ev=ev_yes,
            direction=dominant,
            edge=edge,
            kelly_fraction=kelly_frac,
            reasoning=" || ".join(reasonings),
            source="ComboSignal",
        )
