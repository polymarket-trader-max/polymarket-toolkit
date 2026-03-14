"""
流动性定价信号 - 低流动性市场往往被错误定价

核心理论：
- 预测市场的定价效率与流动性成正比
- 流动性低的市场更容易出现定价偏差
- 但低流动性也意味着：进出成本高、操纵风险高
- 策略：在流动性适中的市场寻找价格偏差

具体模式：
1. 接近1的低流动性市场 → 检验是否 99%+ 真的应该那么确定
2. 接近0.5的高流动性市场 → 检验是否真的50/50
3. 新市场（成交量低）→ 价格锚定效应，往往从0.5开始随机游走
"""

from .base import BaseSignalGenerator
from typing import Optional
from datetime import datetime, timezone


class LiquiditySignalGenerator(BaseSignalGenerator):
    """
    流动性相关的定价信号。
    """

    def estimate_probability(self, market: dict) -> Optional[tuple[float, float, str]]:
        yes_price = market["yes_price"]
        liquidity = market.get("liquidity", 0)
        volume = market.get("volume", 0)
        volume_24h = market.get("volume_24h", 0)
        spread = market.get("spread", 0.01)
        end_date_str = market.get("end_date", "")
        competitive = market.get("competitive", 0)

        reasons = []
        est_prob = yes_price

        days_left = self._days_left(end_date_str)

        # ── 模式1：超低流动性 + 极端价格 ──────────────────────────
        # 流动性 < $5k 的市场，极端价格（>95% 或 <5%）可能是初始偏差
        if liquidity < 5_000 and (yes_price >= 0.95 or yes_price <= 0.05):
            if volume < 10_000:  # 成交量也低
                mid_pull = (0.5 - yes_price) * 0.10  # 向0.5轻微拉回
                est_prob = max(0.01, min(0.99, yes_price + mid_pull))
                confidence = 0.42
                reasons.append(f"极低流动性极端价格(流动性${liquidity:,.0f}，成交${volume:,.0f})")
                return est_prob, confidence, " | ".join(reasons)

        # ── 模式2：高竞争性 + 高流动性 = 尊重市场 ─────────────────
        # competitive score 接近 1 意味着市场定价非常有效
        # 这种市场我们不做信号（边际太低）
        if competitive > 0.95 and liquidity > 100_000:
            return None  # 跳过，市场太有效

        # ── 模式3：临近截止 + 价格仍在中间 ─────────────────────────
        # 距截止 <7天，价格仍在 35%-65% 区间 → 高度不确定性
        # 这类市场风险太高，不做信号
        if days_left is not None and days_left <= 7 and 0.35 <= yes_price <= 0.65:
            return None  # 太不确定，跳过

        # ── 模式4：成交量突然暴增（无历史动量）────────────────────
        # 如果24h成交量占总成交量的 >30%，说明有新信息/事件
        if volume > 0 and volume_24h > 0:
            recent_pct = volume_24h / volume
            if recent_pct > 0.30 and liquidity > 10_000:
                # 新信息注入，动量有效性高
                reasons.append(f"近期成交量占比高({recent_pct:.0%})，信息注入信号")
                confidence = 0.50
                # 顺势，市场在消化信息
                return est_prob, confidence, " | ".join(reasons)

        return None  # 无触发条件

    def _days_left(self, end_date_str: str) -> Optional[int]:
        if not end_date_str:
            return None
        try:
            end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return max(0, (end - now).days)
        except Exception:
            return None
