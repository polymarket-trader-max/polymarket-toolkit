#!/usr/bin/env python3
"""
交易机会量化评分系统
总分 ≥ 65 → 全仓下单
总分 50-64 → 半仓下单
总分 < 50 → 跳过
"""

def score_opportunity(opp: dict) -> dict:
    """
    对一个机会进行多维度评分
    opp 需包含：volume_24h, spread_pct, change_1d, days_left,
               edge, direction, price, liquidity, reason
    """
    score = 0
    breakdown = {}

    # 1. 流动性 (20分)
    vol = opp.get('volume_24h', 0)
    if vol >= 1_000_000:
        liq_score = 20
    elif vol >= 100_000:
        liq_score = 15
    elif vol >= 10_000:
        liq_score = 10
    else:
        liq_score = 0
    score += liq_score
    breakdown['流动性'] = f"{liq_score}/20 (vol=${vol:,.0f})"

    # 2. 价差 (15分)
    spread = opp.get('spread_pct', 1.0)
    if spread < 0.01:
        spread_score = 15
    elif spread < 0.02:
        spread_score = 12
    elif spread < 0.04:
        spread_score = 8
    else:
        spread_score = 0
    score += spread_score
    breakdown['价差'] = f"{spread_score}/15 (spread={spread:.1%})"

    # 3. 信息边际 (25分)
    reason = opp.get('reason', '').lower()
    change = abs(opp.get('change_1d', 0))
    if '超跌' in reason or '超涨' in reason:
        if change > 0.20:
            info_score = 22  # 强过度反应
        else:
            info_score = 18  # 中等过度反应
    elif '동량' in reason or '动量' in reason or '균형' in reason or '均衡' in reason:
        info_score = 12
    elif change > 0.30:
        info_score = 20  # 巨大价格变动 = 信息驱动
    else:
        info_score = 8
    score += info_score
    breakdown['信息边际'] = f"{info_score}/25 ({reason[:30]})"

    # 4. 时间窗口 (15分)
    days = opp.get('days_left', 0)
    if 3 <= days <= 14:        # 最佳：短期有催化剂
        time_score = 15
    elif 14 < days <= 60:      # 次优：中期
        time_score = 12
    elif days > 60:             # 长期：theta 衰减慢
        time_score = 8
    elif days == 0:             # 当天：太紧张
        time_score = 5
    else:
        time_score = 5
    score += time_score
    breakdown['时间窗口'] = f"{time_score}/15 ({days}天)"

    # 5. 基本面一致性 (15分)
    price = opp.get('price', 0.5)
    direction = opp.get('direction', 'YES')
    # 简单规则：
    # 高流动市场 + 方向清晰 = 基本面支撑好
    # 极端价格（<10% or >90%）= 基本面压力大
    if direction == 'NO' and price > 0.80:      # 买高确定性 NO
        fund_score = 15
    elif direction == 'YES' and 0.25 < price < 0.55:  # 被低估的 YES
        fund_score = 13
    elif direction == 'YES' and price < 0.25:   # 尾部事件
        fund_score = 3
    elif direction == 'NO' and price < 0.50:    # 做空高概率
        fund_score = 8
    else:
        fund_score = 10
    score += fund_score
    breakdown['基本面'] = f"{fund_score}/15"

    # 6. 相关市场验证 (10分)
    # 如果是伊朗相关、有多个市场在动 → 高分
    question = opp.get('question', '').lower()
    if any(k in question for k in ['iran', 'khamenei', 'supreme leader']):
        cross_score = 10  # 有大量相关市场互相验证
    elif any(k in question for k in ['election', 'fed', 'rate', 'bitcoin']):
        cross_score = 8
    else:
        cross_score = 5
    score += cross_score
    breakdown['相关市场'] = f"{cross_score}/10"

    # 决策
    if score >= 65:
        decision = "✅ 全仓下单"
        kelly_mult = 1.0
    elif score >= 50:
        decision = "🟡 半仓下单"
        kelly_mult = 0.5
    else:
        decision = "❌ 跳过"
        kelly_mult = 0

    return {
        'score': score,
        'decision': decision,
        'kelly_multiplier': kelly_mult,
        'breakdown': breakdown,
    }


def print_score(opp: dict, score_result: dict):
    q = opp.get('question', '')[:60]
    s = score_result['score']
    d = score_result['decision']
    print(f"  {'─'*55}")
    print(f"  {q}")
    print(f"  方向:{opp.get('direction')} @ {opp.get('price'):.3f} | 总分:{s}/100 → {d}")
    for k, v in score_result['breakdown'].items():
        print(f"    {k}: {v}")
    print()
