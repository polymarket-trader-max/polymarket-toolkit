# ============================================================
# 🔒 POLYMARKET TOOLKIT PRO — Preview Stub
# Full version: https://gumroad.com/l/polymarket-toolkit-pro
# This file shows the API surface. Upgrade to Pro for the
# complete implementation (~228 lines of battle-tested code).
# ============================================================
"""
规则信号 v2 - 基于价格区间偏差的统计套利

核心理论（学术研究 & 预测市场实证）：

1. 「过度自信偏差」：市场定价 >90% 的事件，实际命中率约 80-85%（高估约5-10%）
2. 「长尾低估」：市场定价 <10% 的极端事件往往被轻微低估
3. 「现状偏差」：涉及「现任者/现有状态继续」的问题，现状往往更可能持续
4. 「极端事件高估」：戏剧性事件往往被高估

可信类别（有信息优势）：
- politics, tech, geopolitics, finance, crypto

排除类别（无优势）：
- sports, weather, entertainment, short-term
"""

from .base import BaseSignalGenerator
from typing import Optional
import re
from datetime import datetime, timezone


# 只对这些类别产生信号
ACTIONABLE_CATEGORIES = {"politics", "tech", "geopolitics", "finance", "crypto"}

# 排除关键词（正则）
EXCLUDE_PATTERNS = [
    r"up or down",
    r"\d+:\d{2}\s*[ap]m",
    r"temperature|rainfall|snow|weather",
    r"\bvs\.?\s+\b",
    r"oscars|grammy|emmy|golden globe|box office",
    r"will .{3,30} score|goals|points|yards",
    r"nfl|nba|mlb|nhl|premier league|champions league",
    r"super bowl|world series|stanley cup|nba finals",
    r"will .{3,40} win .{3,40} (game|match|set|race)",
    r"episode|season finale|tv show",
    r"celebrity|kardashian|jenner|taylor swift",
]


class RulesBasedSignalGenerator(BaseSignalGenerator):
    """
    v2 规则信号：价格区间偏差校准 + 类别过滤
    """

    # 校准表：(category, price_zone) → (estimated_true_rate, confidence)
    CALIBRATION = {
        ("politics",    "very_high"): (0.82, 0.63),
        ("tech",        "very_high"): (0.85, 0.58),
        ("geopolitics", "very_high"): (0.77, 0.66),
        ("finance",     "very_high"): (0.83, 0.60),
        ("crypto",      "very_high"): (0.80, 0.55),
        ("politics",    "high"): (0.77, 0.52),
        ("tech",        "high"): (0.80, 0.50),
        ("geopolitics", "high"): (0.74, 0.55),
        ("finance",     "high"): (0.79, 0.50),
        ("crypto",      "high"): (0.76, 0.52),
        ("politics",    "med_high"): (0.65, 0.46),
        ("tech",        "med_high"): (0.68, 0.44),
        ("geopolitics", "med_high"): (0.62, 0.48),
        ("finance",     "med_high"): (0.67, 0.45),
        ("crypto",      "med_high"): (0.65, 0.44),
        ("politics",    "med_low"):  (0.30, 0.44),
        ("tech",        "med_low"):  (0.28, 0.43),
        ("geopolitics", "med_low"):  (0.28, 0.45),
        ("finance",     "med_low"):  (0.29, 0.44),
        ("crypto",      "med_low"):  (0.31, 0.43),
        ("politics",    "low"):  (0.11, 0.50),
        ("tech",        "low"):  (0.10, 0.48),
        ("geopolitics", "low"):  (0.09, 0.52),
        ("finance",     "low"):  (0.10, 0.50),
        ("crypto",      "low"):  (0.12, 0.50),
        ("politics",    "very_low"): (0.05, 0.45),
        ("tech",        "very_low"): (0.05, 0.43),
        ("geopolitics", "very_low"): (0.04, 0.47),
        ("finance",     "very_low"): (0.05, 0.45),
        ("crypto",      "very_low"): (0.06, 0.45),
    }

    def estimate_probability(self, market: dict) -> Optional[tuple]:
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )

    def _get_price_zone(self, price: float) -> Optional[str]:
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )

    def _days_left(self, end_date_str: str) -> Optional[int]:
        """计算距截止日期剩余天数"""
        raise NotImplementedError(
            "🔒 This is a PRO feature. Get the full version at: https://github.com/polymarket-trader-max/polymarket-toolkit#-pro-version"
        )
