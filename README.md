<![CDATA[<div align="center">

# 🎯 Polymarket Trading Toolkit

**Automated trading tools for the world's largest prediction market.**

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![GitHub Stars](https://img.shields.io/github/stars/polymarket-trader-max/polymarket-toolkit?style=social)](https://github.com/polymarket-trader-max/polymarket-toolkit)
[![41 Modules](https://img.shields.io/badge/modules-41-orange)](https://github.com/polymarket-trader-max/polymarket-toolkit)

Market making · Signal trading · BTC lag arbitrage · Position monitoring · Backtesting

**Built and battle-tested with real capital on Polymarket.**

[Quick Start](#-quick-start) · [Features](#-features) · [Architecture](#-architecture) · [Pro Version](#-pro-version)

</div>

---

## 💡 Why This Toolkit?

Most Polymarket bots try to **predict outcomes** — and lose money doing it. Only [7.6% of Polymarket wallets are profitable](https://polytreasury.com).

This toolkit takes a different approach:

- **Market making** earns from the bid-ask spread, not from being right
- **Lag arbitrage** exploits price delays between exchanges and Polymarket
- **Signal trading** only enters positions with quantified edge (≥5%)
- **0% maker fee** — all strategies use GTC limit orders

> 🧠 *70% of whale profits come from market structure (making + arbitrage), not predicting events.*

---

## 🆓 vs 🔒 Free vs Pro

| | 🆓 Free (15 modules) | 🔒 Pro (41 modules) |
|--|---|---|
| **Scanners** | Edge scanner, live scanner, market classifier | + Multi-signal scorer, cross-arb, time decay, AI scanner |
| **Trading** | — | Market makers, signal trader, BTC lag arb, quick exec |
| **Risk** | Position monitor (auto TP/SL) | + Live game guard, stress testing |
| **Data** | Price tracker, BTC scanner, data fetcher | + News monitor, signal engine (6 modules) |
| **Backtest** | Calibration testing | + Full engine with friction model, Sharpe/drawdown |
| **Source code** | ✅ Complete | ✅ Complete — no obfuscation, modify anything |

---

## ✨ Features

### Core Strategies
| Module | Description | Risk | Tier |
|--------|------------|------|------|
| `spread_maker.py` | **Market Making** — Dual-sided orders to earn bid-ask spread | Low | 🔒 |
| `signal_trader.py` | **Signal Trading** — Oil models, live sports, time decay signals | Medium | 🔒 |
| `btc_lag_trader.py` | **BTC Lag Arbitrage** — Exploit price lag vs Polymarket odds | Medium | 🔒 |
| `maker_bot.py` | **Advanced Market Maker** — Inventory management, auto-rotation | Low | 🔒 |
| `maker_trader.py` | **Maker Trading** — GTC limit order execution | Low | 🔒 |

### Scanners & Analysis
| Module | Description | Tier |
|--------|------------|------|
| `edge_scanner.py` | Quick edge detection with configurable thresholds | 🆓 |
| `live_scanner.py` | Real-time opportunity scanner | 🆓 |
| `market_classifier.py` | Auto-classify markets (crypto, sports, politics) | 🆓 |
| `multi_edge_scanner.py` | Scan 100+ markets with multi-signal scoring | 🔒 |
| `smart_scanner.py` | AI-enhanced scanner with category awareness | 🔒 |
| `cross_arb_scanner.py` | Cross-market arbitrage detection | 🔒 |
| `time_decay_scanner.py` | Time decay opportunities in expiring markets | 🔒 |

### Risk Management
| Module | Description | Tier |
|--------|------------|------|
| `monitor_positions.py` | **Position Monitor** — Auto take-profit & stop-loss | 🆓 |
| `live_game_guard.py` | Live sports monitoring with in-play hedging | 🔒 |
| `trade_now.py` | Quick execution (FOK market orders) | 🔒 |

### Data & Intelligence
| Module | Description | Tier |
|--------|------------|------|
| `data_fetcher.py` | Gamma API data fetcher with caching | 🆓 |
| `price_tracker.py` | Historical price tracking and snapshots | 🆓 |
| `btc_scanner.py` | BTC price monitoring for triggers | 🆓 |
| `scorer.py` | Market opportunity scoring (0-100 scale) | 🆓 |
| `news_monitor.py` | Multi-source news monitoring with trade signals | 🔒 |

### Backtesting
| Module | Description | Tier |
|--------|------------|------|
| `calibration_test.py` | Strategy calibration testing | 🆓 |
| `backtest/engine.py` | Backtesting engine with friction models | 🔒 |
| `backtest/metrics.py` | Performance metrics (Sharpe, drawdown, etc.) | 🔒 |
| `run_backtest.py` | Run backtests against historical data | 🔒 |
| `stress_test.py` | Stress testing under extreme scenarios | 🔒 |

### Signal Engine (`signals/`)
| Module | Description | Tier |
|--------|------------|------|
| `signals/base.py` | Signal base class & interface | 🆓 |
| `signals/momentum.py` | Price momentum signals | 🔒 |
| `signals/liquidity.py` | Liquidity analysis | 🔒 |
| `signals/price_series.py` | Time series analysis | 🔒 |
| `signals/combo.py` | Multi-signal combination | 🔒 |
| `signals/rules.py` | Rule-based signal generation | 🔒 |

### BTC Direction (`btc_direction/`)
| Module | Description | Tier |
|--------|------------|------|
| `btc_direction/signals.py` | BTC direction signal engine | 🔒 |
| `btc_direction/lag_arb.py` | Brownian motion lag arb engine | 🔒 |
| `btc_direction/trader.py` | BTC direction auto-trader | 🔒 |
| `btc_direction/scanner.py` | BTC market scanner | 🔒 |

### Utilities
| Module | Description | Tier |
|--------|------------|------|
| `trade_utils.py` | Common trading utilities | 🆓 |
| `gen_api_keys.py` | Generate CLOB API credentials | 🆓 |

---

## 🚀 Quick Start

### 1. Clone & Install
```bash
git clone https://github.com/polymarket-trader-max/polymarket-toolkit.git
cd polymarket-toolkit
pip install py-clob-client requests python-dotenv
```

### 2. Configure Credentials
```bash
cp .env.example .env
# Edit .env with your Polymarket CLOB API credentials
# See gen_api_keys.py for key generation
```

### 3. Scan for Opportunities
```bash
python edge_scanner.py          # Find mispriced markets
python live_scanner.py          # Real-time scanning
python market_classifier.py     # Classify market types
```

### 4. Monitor Positions
```bash
python monitor_positions.py     # Auto take-profit & stop-loss
python price_tracker.py         # Track price history
```

### 5. Run Market Maker (Pro)
```bash
python spread_maker.py          # Earn bid-ask spread
python maker_bot.py             # Advanced market making with inventory mgmt
```

---

## 📊 Architecture

```
┌─────────────────────────────────────────────────┐
│              🆓 Data Layer                       │
│  btc_scanner │ price_tracker │ data_fetcher      │
│  Gamma API │ CLOB API │ Binance API              │
├─────────────────────────────────────────────────┤
│           🆓/🔒 Signal Layer                     │
│  signals/base 🆓 │ momentum/liquidity/rules 🔒   │
│  btc_direction/* 🔒 │ market_classifier 🆓       │
├─────────────────────────────────────────────────┤
│              🔒 Strategy Layer                    │
│  spread_maker │ signal_trader │ btc_lag_trader    │
│  cross_arb │ time_decay │ multi_edge_scanner      │
├─────────────────────────────────────────────────┤
│           🆓/🔒 Execution Layer                   │
│  trade_utils 🆓 │ trade_now 🔒 │ maker_bot 🔒    │
├─────────────────────────────────────────────────┤
│           🆓/🔒 Risk Management                   │
│  monitor_positions 🆓 │ live_game_guard 🔒        │
│  scorer 🆓 │ stress_test 🔒                       │
└─────────────────────────────────────────────────┘
```

---

## 🔑 Key Concepts

- **CLOB**: Central Limit Order Book — Polymarket's core trading engine
- **Maker vs Taker**: Maker orders (GTC limit) = **0% fee**. Taker orders = 2% fee
- **YES/NO Tokens**: Binary outcome tokens. YES + NO = $1 at settlement
- **Proxy Wallet**: Polymarket uses a proxy contract — `signature_type=1` for email accounts
- **GTC Limit Orders**: Good-Till-Cancelled — the only way to get 0% maker fee

---

## 💰 Pro Version

**All 41 modules. 10,000+ lines. Full source code. No obfuscation.**

| What You Get | |
|---|---|
| 🤖 **Market Making Bots** | `spread_maker` + `maker_bot` + `maker_trader` — earn spread without predicting |
| 📈 **Signal Trading** | Oil price models, live sports scoring, time decay |
| ⚡ **BTC Lag Arbitrage** | Brownian motion pricing model exploits exchange-to-Polymarket delay |
| 🔍 **Advanced Scanners** | Multi-signal scoring, cross-market arb, AI-enhanced |
| 🧪 **Backtesting Suite** | Full engine with realistic friction, Sharpe ratio, max drawdown |
| 📡 **News Monitor** | Geopolitical + crypto news → automated trade signals |
| 🎮 **Live Game Guard** | Real-time sports monitoring with in-play hedging |

### 💵 Pricing: **$39 USDC** ~~$49~~

<details>
<summary><b>How to Buy →</b></summary>

1. **Send $39 USDC** on Polygon to:
   ```
   0x73727711e62fFAe1d5Ec9E0a9aE79CcBbd1e6D47
   ```
2. **Open an [Issue](https://github.com/polymarket-trader-max/polymarket-toolkit/issues/new)** with your tx hash + GitHub username
3. **Get access** within 24h to the private `polymarket-toolkit-pro` repo

> ⚡ USDC on **Polygon** only. Lifetime access + updates.

</details>

---

## ⚙️ Configuration Examples

```python
# spread_maker.py (Pro) — Market making parameters
MIN_VOLUME_24H = 25000    # Min 24h volume ($)
MIN_SPREAD     = 0.015    # Min spread to trade (1.5¢)
MAX_EXPOSURE   = 10.0     # Max exposure per market ($)
ORDER_SIZE     = 8        # Tokens per order

# monitor_positions.py (Free) — Risk management
TAKE_PROFIT_PCT = 0.25    # +25% take profit
STOP_LOSS_PCT   = 0.20    # -20% stop loss
```

---

## ⚠️ Disclaimer

**For educational and research purposes.** Trading prediction markets involves real financial risk. Past performance ≠ future results. Never trade more than you can afford to lose.

---

## 📄 License

MIT License — Free modules are fully open source.

## 🤝 Contributing

PRs welcome! Areas that could use help:
- [ ] WebSocket support for real-time orderbook
- [ ] More signal sources (social media, on-chain data)
- [ ] Web dashboard for position monitoring
- [ ] Multi-account support

---

<div align="center">

**⭐ Star this repo if you find it useful — it helps others discover it!**

</div>
]]>