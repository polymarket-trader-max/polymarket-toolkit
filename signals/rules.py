"""
规则信号 v2 - 基于价格区间偏差的统计套利

核心理论（学术研究 & 预测市场实证）：

1. 「过度自信偏差」：市场定价 >90% 的事件，实际命中率约 80-85%（高估约5-10%）
   → 适合做 NO

2. 「长尾低估」：市场定价 <10% 的极端事件往往被轻微低估
   → 但需精准筛类，不能盲目买

3. 「现状偏差」：涉及「现任者/现有状态继续」的问题（现任总统、现有政策），
   现状往往比市场更可能持续

4. 「极端事件高估」：戏剧性事件（政治人物辞职、战争爆发等）往往被高估

可信类别（有信息优势）：
- politics  : 政治事件，有历史基准率支撑
- tech      : 科技产品发布/里程碑，行业知识优势
- geopolitics: 地缘政治，历史模式
- finance   : 经济数据/政策，量化基础
- crypto    : 加密货币，链上数据 + 宏观

排除类别（无优势）：
- sports        : 纯随机
- weather       : 气象专业
- entertainment : 娱乐八卦
- short-term    : 市场做市商主导
"""

from .base import BaseSignalGenerator
from typing import Optional
import re
from datetime import datetime, timezone


# 只对这些类别产生信号
ACTIONABLE_CATEGORIES = {"politics", "tech", "geopolitics", "finance", "crypto"}

# 排除关键词（正则）——识别垃圾/无优势市场
EXCLUDE_PATTERNS = [
    r"up or down",
    r"\d+:\d{2}\s*[ap]m",         # 时间窗口短线（如 9:15PM-9:30PM）
    r"temperature|rainfall|snow|weather",
    r"\bvs\.?\s+\b",              # 体育对战（队伍名 vs 队伍名）
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
    # 基于学术文献 + Polymarket 历史数据估算
    CALIBRATION = {
        # 极高价格区间 [0.92, 0.99]：过度自信偏差最明显
        ("politics",    "very_high"): (0.82, 0.63),
        ("tech",        "very_high"): (0.85, 0.58),
        ("geopolitics", "very_high"): (0.77, 0.66),
        ("finance",     "very_high"): (0.83, 0.60),
        ("crypto",      "very_high"): (0.80, 0.55),

        # 高价格区间 [0.82, 0.92)：轻微高估
        ("politics",    "high"): (0.77, 0.52),
        ("tech",        "high"): (0.80, 0.50),
        ("geopolitics", "high"): (0.74, 0.55),
        ("finance",     "high"): (0.79, 0.50),
        ("crypto",      "high"): (0.76, 0.52),

        # 中高价格区间 [0.67, 0.82)：轻微高估（过度乐观偏差）
        # 研究表明：预测市场 65-80% YES 事件实际发生率约低 5-8%
        ("politics",    "med_high"): (0.65, 0.46),
        ("tech",        "med_high"): (0.68, 0.44),
        ("geopolitics", "med_high"): (0.62, 0.48),
        ("finance",     "med_high"): (0.67, 0.45),
        ("crypto",      "med_high"): (0.65, 0.44),

        # 中低价格区间 (0.17, 0.33]：轻微低估（long-shot buy 机会）
        # 实际发生率略高于市场定价
        ("politics",    "med_low"):  (0.30, 0.44),
        ("tech",        "med_low"):  (0.28, 0.43),
        ("geopolitics", "med_low"):  (0.28, 0.45),
        ("finance",     "med_low"):  (0.29, 0.44),
        ("crypto",      "med_low"):  (0.31, 0.43),

        # 低价格区间 (0.07, 0.17]：轻微低估（long-shot bias 反向）
        ("politics",    "low"):  (0.11, 0.50),
        ("tech",        "low"):  (0.10, 0.48),
        ("geopolitics", "low"):  (0.09, 0.52),
        ("finance",     "low"):  (0.10, 0.50),
        ("crypto",      "low"):  (0.12, 0.50),

        # 极低价格区间 [0.01, 0.07]：极端事件，更难预测
        ("politics",    "very_low"): (0.05, 0.45),
        ("tech",        "very_low"): (0.05, 0.43),
        ("geopolitics", "very_low"): (0.04, 0.47),
        ("finance",     "very_low"): (0.05, 0.45),
        ("crypto",      "very_low"): (0.06, 0.45),
    }

    def estimate_probability(self, market: dict) -> Optional[tuple[float, float, str]]:
        yes_price = market["yes_price"]
        category  = market.get("category", "other")
        question  = market.get("question", "").lower()
        desc      = market.get("description", "").lower()

        # ── 过滤1：类别白名单 ────────────────────────────────────
        if category not in ACTIONABLE_CATEGORIES:
            return None

        # ── 过滤2：排除垃圾市场 ──────────────────────────────────
        combined_text = question + " " + desc
        for pattern in EXCLUDE_PATTERNS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                return None

        # ── 过滤3：排除「结果已确定」的短期价格市场 ──────────────
        # 如 "BTC > $64k on March 6" 当 BTC 已在 $72k，结果显而易见
        # 识别方式：截止日期 < 7 天 + crypto 类别 + 价格 >95% → 跳过
        days_left = self._days_left(market.get("end_date", ""))
        if (days_left is not None and days_left <= 7
                and category == "crypto"
                and yes_price >= 0.92):
            return None   # 短期加密价格目标，结果接近确定，无优势

        # ── 过滤4：截止日期 <3 天的任何高置信度市场 ─────────────
        if days_left is not None and days_left <= 3 and yes_price >= 0.90:
            return None   # 太近，市场定价基本已反映现实

        # ── 过滤5：价格范围 ──────────────────────────────────────
        price_zone = self._get_price_zone(yes_price)
        if price_zone is None:
            return None  # 核心中间区间 [0.33, 0.67]，无稳健规律

        # 中等区间额外要求：截止日期 > 14 天（短期中间区间全是噪音）
        if price_zone in ("med_high", "med_low"):
            if days_left is not None and days_left < 14:
                return None

        # ── 查校准表 ─────────────────────────────────────────────
        key = (category, price_zone)
        if key not in self.CALIBRATION:
            # 用通用默认值
            if price_zone in ("very_high", "high"):
                true_rate, conf = (0.80, 0.50)
            elif price_zone in ("low", "very_low"):
                true_rate, conf = (0.08, 0.46)
            else:
                return None
        else:
            true_rate, conf = self.CALIBRATION[key]

        # ── 额外关键词修正 ────────────────────────────────────────
        modifiers = []

        # 现任者/现状偏差：现状比市场更可能持续 → YES++
        incumbent_kw = ["remain", "stay", "continue", "re-elect", "incumbent",
                        "retain", "keep", "maintain", "still"]
        if any(kw in question for kw in incumbent_kw):
            true_rate = min(0.99, true_rate + 0.04)
            modifiers.append("现状持续偏差+4%")

        # 极端事件关键词：resign, impeach, war, collapse → YES--（高估修正）
        drama_kw = ["resign", "impeach", "collapse", "war", "invade", "coup",
                    "bankrupt", "default", "arrest", "indict"]
        if any(kw in question for kw in drama_kw):
            true_rate = max(0.01, true_rate - 0.05)
            modifiers.append("极端事件高估修正-5%")

        # 「first time」「unprecedented」→ 往往被高估（新奇事件溢价）
        novel_kw = ["first time", "unprecedented", "first ever", "historic"]
        if any(kw in question for kw in novel_kw):
            true_rate = max(0.01, true_rate - 0.04)
            modifiers.append("新奇事件溢价修正-4%")

        # ── 最终判断 ──────────────────────────────────────────────
        est_prob = true_rate
        edge = abs(est_prob - yes_price)

        if edge < self.MIN_EDGE:
            return None

        reason_parts = [
            f"价格区间={price_zone}({yes_price:.2f})",
            f"类别={category}",
            f"校准概率={true_rate:.2f}",
        ]
        if modifiers:
            reason_parts.extend(modifiers)

        reasoning = " | ".join(reason_parts)
        return est_prob, conf, reasoning

    def _get_price_zone(self, price: float) -> Optional[str]:
        if 0.92 <= price <= 0.99:
            return "very_high"
        elif 0.82 <= price < 0.92:
            return "high"
        elif 0.67 <= price < 0.82:
            return "med_high"     # 新增：中高区间，信号较弱但覆盖更广
        elif 0.17 < price <= 0.33:
            return "med_low"      # 新增：中低区间
        elif 0.07 < price <= 0.17:
            return "low"
        elif 0.01 <= price <= 0.07:
            return "very_low"
        else:
            return None  # 中间区间 [0.33, 0.67]，信息不足不操作

    def _days_left(self, end_date_str: str) -> Optional[int]:
        """计算距截止日期剩余天数"""
        if not end_date_str:
            return None
        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return max(0, (end - now).days)
        except Exception:
            return None
