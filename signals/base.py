"""
信号基类 - 所有信号生成器的接口定义
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    """单个市场的信号输出"""
    market_id: str
    question: str
    category: str
    market_yes_price: float          # 市场当前 Yes 价格
    estimated_prob: float            # 模型估计的真实概率
    confidence: float                # 模型置信度 0~1
    ev: float                        # 期望值 = estimated_prob - market_yes_price
    direction: str                   # "YES" | "NO" | "SKIP"
    edge: float                      # 绝对优势，始终为正
    kelly_fraction: float            # 推荐仓位（占总资金比）
    reasoning: str                   # 信号理由
    source: str                      # 信号来源标签

    @property
    def is_actionable(self) -> bool:
        return self.direction != "SKIP" and self.edge >= 0.05 and self.confidence >= 0.4


class BaseSignalGenerator:
    """
    信号生成器基类。
    子类实现 estimate_probability() 方法。
    """
    MIN_EDGE = 0.05          # 最小 EV 阈值
    MIN_CONFIDENCE = 0.40    # 最小置信度
    KELLY_FRACTION = 0.25    # 使用 1/4 Kelly

    def generate(self, market: dict) -> Optional[Signal]:
        """
        主入口：接受市场字典，返回 Signal 或 None。
        """
        try:
            result = self.estimate_probability(market)
            if result is None:
                return None

            est_prob, confidence, reasoning = result
            yes_price = market["yes_price"]

            # 计算 EV 和方向
            ev_yes = est_prob - yes_price
            ev_no = (1 - est_prob) - (1 - yes_price)

            if abs(ev_yes) >= abs(ev_no):
                direction = "YES" if ev_yes > 0 else "NO"
                edge = abs(ev_yes)
                bet_prob = est_prob if direction == "YES" else (1 - est_prob)
                bet_price = yes_price if direction == "YES" else (1 - yes_price)
            else:
                direction = "NO" if ev_no > 0 else "YES"
                edge = abs(ev_no)
                bet_prob = (1 - est_prob) if direction == "NO" else est_prob
                bet_price = (1 - yes_price) if direction == "NO" else yes_price

            if edge < self.MIN_EDGE or confidence < self.MIN_CONFIDENCE:
                direction = "SKIP"
                edge = 0.0

            # 凯利公式
            if direction != "SKIP" and bet_price > 0:
                b = (1 / bet_price) - 1   # 赔率
                kelly = (b * bet_prob - (1 - bet_prob)) / b
                kelly = max(0, min(kelly, 1))
                kelly_frac = kelly * self.KELLY_FRACTION
            else:
                kelly_frac = 0.0

            return Signal(
                market_id=market["id"],
                question=market["question"],
                category=market["category"],
                market_yes_price=yes_price,
                estimated_prob=est_prob,
                confidence=confidence,
                ev=ev_yes,
                direction=direction,
                edge=edge,
                kelly_fraction=kelly_frac,
                reasoning=reasoning,
                source=self.__class__.__name__,
            )
        except Exception as e:
            return None

    def estimate_probability(self, market: dict) -> Optional[tuple[float, float, str]]:
        """
        返回 (estimated_probability, confidence, reasoning_str)
        子类必须实现。
        """
        raise NotImplementedError
