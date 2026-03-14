"""
btc_direction/trader.py — BTC 方向交易执行器

流程：
1. 计算信号
2. 扫描机会
3. 去重（不重复下注同一市场）
4. 下单 + 记录
"""

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, BalanceAllowanceParams, AssetType,
    MarketOrderArgs, OrderType,
)
from btc_direction.signals import compute_signal
from btc_direction.scanner import evaluate_opportunities

# ── 凭据（与主策略相同） ──────────────────────────────────────────
PRIVATE_KEY  = os.environ["POLYMARKET_PRIVATE_KEY"]
PROXY_WALLET = os.environ["POLYMARKET_PROXY_WALLET"]
creds = ApiCreds(
    api_key        = os.environ["POLYMARKET_API_KEY"],
    api_secret     = os.environ["POLYMARKET_API_SECRET"],
    api_passphrase = os.environ["POLYMARKET_API_PASSPHRASE"],
)

SCRIPT_DIR = Path(__file__).parent
BTC_LOG    = SCRIPT_DIR / "btc_trades.json"

# ── 风控参数 ────────────────────────────────────────────────────────
MAX_BTC_TRADES     = 2          # 同时最多2笔 BTC 方向仓位
MAX_BET_PER_TRADE  = 5.0        # 单笔最大 $5
MIN_BET_PER_TRADE  = 2.0        # 单笔最小 $2
KELLY_FRACTION     = 0.25       # Kelly 分数（保守）
MAX_DAILY_LOSS     = 15.0       # 每日最大亏损 $15，超过停止交易
DRY_RUN            = False      # True = 只模拟不下单


def get_client() -> ClobClient:
    return ClobClient(
        "https://clob.polymarket.com",
        key=PRIVATE_KEY, chain_id=POLYGON,
        creds=creds, signature_type=1, funder=PROXY_WALLET,
    )


def get_balance(client: ClobClient) -> float:
    bal = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    return int(bal["balance"]) / 1e6


def load_btc_log() -> dict:
    if BTC_LOG.exists():
        try:
            return json.loads(BTC_LOG.read_text())
        except Exception:
            pass
    return {"trades": [], "daily_pnl": {}, "total_bet": 0}


def save_btc_log(data: dict):
    BTC_LOG.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def get_open_trades(log: dict) -> list[dict]:
    return [t for t in log["trades"] if t.get("status") == "open"]


def get_today_loss(log: dict) -> float:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return abs(min(0, log.get("daily_pnl", {}).get(today, 0)))


def is_market_already_traded(log: dict, market_id: str) -> bool:
    """是否已经对该市场下注（未平仓）"""
    for t in log["trades"]:
        if t.get("market_id") == market_id and t.get("status") == "open":
            return True
    return False


def run(
    dry_run: bool = DRY_RUN,
    verbose: bool = True,
) -> dict:
    """
    主入口：计算信号 → 扫描 → 下单
    Returns: {"placed": N, "signal": ..., "opportunities": [...]}
    """
    print(f"\n{'='*55}")
    print(f"  ⚡ BTC 方向交易  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if dry_run:
        print(f"  🔶 模拟模式（DRY RUN）")
    print(f"{'='*55}\n")

    log = load_btc_log()
    today_loss = get_today_loss(log)
    open_count = len(get_open_trades(log))

    # ── 风控检查 ──────────────────────────────────────────────
    if today_loss >= MAX_DAILY_LOSS:
        print(f"  🛑 今日亏损 ${today_loss:.2f} 达到上限 ${MAX_DAILY_LOSS}，停止交易")
        return {"placed": 0, "reason": "daily_loss_limit"}

    if open_count >= MAX_BTC_TRADES:
        print(f"  ⚠️ 已有 {open_count} 笔BTC仓位，达到上限 {MAX_BTC_TRADES}")
        return {"placed": 0, "reason": "max_positions"}

    client = get_client()
    balance = get_balance(client)
    print(f"  💰 余额: ${balance:.2f} USDC")
    print(f"  📋 BTC开仓: {open_count}/{MAX_BTC_TRADES}  今日亏损: ${today_loss:.2f}")

    # ── 信号计算 ──────────────────────────────────────────────
    signal = compute_signal(verbose=verbose)

    if signal.direction == "NEUTRAL":
        print(f"\n  ➡️  信号中性 (score={signal.score:+.3f})，本轮不下单\n")
        return {"placed": 0, "signal": signal, "reason": "neutral_signal"}

    print(f"\n  📈 信号方向: {signal.direction}  得分: {signal.score:+.3f}  置信: {signal.confidence:.0%}")

    # ── 扫描市场 ──────────────────────────────────────────────
    slots = MAX_BTC_TRADES - open_count
    opps = evaluate_opportunities(
        signal=signal,
        balance=balance,
        max_bet=MAX_BET_PER_TRADE,
        min_bet=MIN_BET_PER_TRADE,
        kelly_frac=KELLY_FRACTION,
        verbose=verbose,
    )

    # 去重：过滤已开仓的市场
    opps = [o for o in opps if not is_market_already_traded(log, o.market["market_id"])]
    opps = opps[:slots]

    if not opps:
        print(f"\n  📭 无符合条件的机会\n")
        return {"placed": 0, "signal": signal, "reason": "no_opportunities"}

    # ── 执行下单 ──────────────────────────────────────────────
    placed = 0
    print(f"\n  🎯 准备下 {len(opps)} 笔单：\n")

    for opp in opps:
        mkt = opp.market
        print(f"  #{placed+1} {mkt['question']}")
        print(f"     方向: {opp.bet_direction} | token: {opp.token_id[:20]}...")
        print(f"     入场价: {opp.entry_price:.3f} | 净边际: {opp.edge:.1%} | 下注: ${opp.bet_usdc}")

        trade_record = {
            "market_id": mkt["market_id"],
            "question": mkt["question"],
            "direction": opp.bet_direction,
            "token_id": opp.token_id,
            "entry_price": opp.entry_price,
            "bet_usdc": opp.bet_usdc,
            "edge": opp.edge,
            "signal_score": signal.score,
            "event_start": str(mkt["event_start"]),
            "end_time": str(mkt["end_time"]),
            "minutes_to_start": mkt["minutes_to_start"],
            "time": datetime.now(timezone.utc).isoformat(),
            "status": "open",
        }

        if dry_run:
            print(f"     🔶 [模拟] 未实际下单")
            trade_record["status"] = "dry_run"
            trade_record["order_id"] = "DRY_RUN"
            log["trades"].append(trade_record)
            placed += 1
            continue

        try:
            order = client.create_market_order(MarketOrderArgs(
                token_id=opp.token_id,
                amount=opp.bet_usdc,
                side="BUY",
                price=opp.entry_price,
            ))
            resp = client.post_order(order, OrderType.FOK)

            if resp.get("success") or resp.get("orderID"):
                oid = resp.get("orderID", "?")
                taking = resp.get("takingAmount", "?")
                print(f"     ✅ 成交！订单: {oid[:20]} 获得: {taking} tokens")
                trade_record["order_id"] = oid
                trade_record["tokens_received"] = taking
                trade_record["tx"] = resp.get("transactionsHashes", [""])[0] if resp.get("transactionsHashes") else ""
                log["trades"].append(trade_record)
                log["total_bet"] = log.get("total_bet", 0) + opp.bet_usdc
                placed += 1
                balance -= opp.bet_usdc
            else:
                err = resp.get("errorMsg") or str(resp)[:100]
                print(f"     ❌ 失败: {err}")
                trade_record["status"] = "failed"
                trade_record["error"] = err
                log["trades"].append(trade_record)

        except Exception as e:
            print(f"     ❌ 异常: {e}")
            trade_record["status"] = "error"
            trade_record["error"] = str(e)[:200]
            log["trades"].append(trade_record)

        print()

    save_btc_log(log)

    bal_after = get_balance(client)
    print(f"{'='*55}")
    print(f"  ✅ 本轮下单: {placed} 笔")
    print(f"  💰 余额: ${bal_after:.2f} USDC")
    print(f"{'='*55}\n")

    return {"placed": placed, "signal": signal, "opportunities": opps}


def show_positions() -> None:
    """显示当前 BTC 方向持仓"""
    log = load_btc_log()
    open_trades = get_open_trades(log)

    print(f"\n{'='*55}")
    print(f"  📊 BTC 方向持仓  ({len(open_trades)} 笔开仓)")
    print(f"{'='*55}")

    now = datetime.now(timezone.utc)
    for t in open_trades:
        try:
            end = datetime.fromisoformat(t["end_time"].replace("Z", "+00:00"))
            mins_left = int((end - now).total_seconds() / 60)
        except Exception:
            mins_left = "?"

        print(f"  {t['direction']} | {t['question'][:50]}")
        print(f"    入场: {t['entry_price']:.3f} | 下注: ${t['bet_usdc']} | 剩: {mins_left}min")

    print(f"{'='*55}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--positions", action="store_true")
    args = parser.parse_args()

    if args.positions:
        show_positions()
    else:
        run(dry_run=args.dry_run)
