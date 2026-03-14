# 🎯 Polymarket Trading Toolkit

A comprehensive suite of automated trading tools for [Polymarket](https://polymarket.com) — the world's largest prediction market.

Built and battle-tested with real capital. Includes market making, signal-based trading, position monitoring, news-driven alerts, backtesting, and more.

> **10,000+ lines of Python** • **41 modules** • **Battle-tested with real money**

---

## 🆓 vs 🔒 What's Included?

This repo contains the **full API surface** of every module. Free modules ship with complete source code. Pro modules include signatures and docstrings so you can evaluate the architecture before upgrading.

| Tier | Modules | What You Get |
|------|---------|-------------|
| 🆓 **Free** | 15 modules | Full source code — scanners, monitoring, classification, utilities |
| 🔒 **Pro** | 26 modules | Market making bots, signal trading, BTC arbitrage, backtesting engine, advanced scanners |

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

### 1. Install Dependencies
```bash
pip install py-clob-client requests python-dotenv
```

### 2. Configure Credentials
```bash
cp .env.example .env
# Edit .env with your Polymarket CLOB API credentials
```

### 3. Scan for Opportunities (Free)
```bash
python edge_scanner.py          # Find mispriced markets
python live_scanner.py          # Real-time scanning
python market_classifier.py     # Classify market types
```

### 4. Monitor Your Positions (Free)
```bash
python monitor_positions.py     # Auto take-profit & stop-loss
python price_tracker.py         # Track price history
```

### 5. Run Market Maker (Pro)
```bash
python spread_maker.py          # Earn bid-ask spread
python maker_bot.py             # Advanced market making
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

## 🚀 Pro Version

**Get the complete toolkit — all 41 modules, 10,000+ lines of battle-tested code.**

### What's in Pro?

- 🤖 **Market Making Bots** — `spread_maker`, `maker_bot`, `maker_trader` — earn spread without predicting outcomes
- 📈 **Signal Trading** — Oil price models, live sports scoring, time decay exploitation
- ⚡ **BTC Lag Arbitrage** — Brownian motion pricing model vs Polymarket odds
- 🔍 **Advanced Scanners** — Multi-signal scoring, cross-market arb, AI-enhanced scanning
- 🧪 **Backtesting Suite** — Full engine with realistic friction, Sharpe/drawdown metrics
- 📡 **News Monitor** — Multi-source geopolitical/crypto news with automated trade signals
- 🎮 **Live Game Guard** — Real-time sports monitoring with in-play hedging

### Pricing

| Plan | Price | What You Get |
|------|-------|-------------|
| **Pro** | **$39 USDC** ~~$49~~ | All 41 modules, full source, no obfuscation |
| | | Lifetime updates via private GitHub repo |
| | | Priority support via GitHub Issues |

### How to Buy

1. **Send $39 USDC** (Polygon network) to:
   ```
   0x73727711e62fFAe1d5Ec9E0a9aE79CcBbd1e6D47
   ```
2. **Open a [GitHub Issue](https://github.com/polymarket-trader-max/polymarket-toolkit/issues/new)** with:
   - Your tx hash
   - Your GitHub username
3. **Get access** — You'll be added to the private `polymarket-toolkit-pro` repo within 24h

> ⚡ Only USDC on **Polygon** accepted. Don't send tokens on other chains.

### Why Pro?

- **Real money, real code** — Every module was built and tested with actual capital on Polymarket
- **No black boxes** — Full Python source code, read every line, modify anything
- **Structural edge** — Market making earns from spread structure, not from predicting events
- **0% maker fee** — All strategies use GTC limit orders (Polymarket charges 0% for makers)

---

## ⚙️ Configuration

Key parameters in `spread_maker.py` (Pro):
```python
MIN_VOLUME_24H = 25000    # Minimum 24h volume ($)
MIN_SPREAD     = 0.015    # Minimum spread to trade (1.5¢)
MAX_EXPOSURE   = 10.0     # Max exposure per market ($)
MAX_MARKETS    = 10       # Max concurrent markets
ORDER_SIZE     = 8        # Tokens per order
```

Key parameters in `monitor_positions.py` (Free):
```python
TAKE_PROFIT_PCT = 0.25    # Take profit at +25%
STOP_LOSS_PCT   = 0.20    # Stop loss at -20%
```

---

## 🏗️ Polymarket Concepts

- **CLOB**: Central Limit Order Book — where all trading happens
- **Maker vs Taker**: Maker orders (GTC limit) = 0% fee. Taker orders = 2% fee
- **YES/NO Tokens**: Binary outcome tokens. YES + NO always = $1 at settlement
- **Negative Risk Markets**: Multi-outcome markets (e.g., elections)
- **Proxy Wallet**: Polymarket uses a proxy contract for trading
- **signature_type=1**: Required for email-registered accounts

---

## ⚠️ Risk Disclaimer

**This software is for educational and research purposes.** Trading on prediction markets involves real financial risk.

- Past performance does not guarantee future results
- Market making can result in one-sided exposure
- API rate limits may prevent timely order management
- Smart contract risks exist on Polygon
- **Never trade more than you can afford to lose**

---

## 📈 Real-World Performance Notes

1. **Market making is the safest strategy** — earn spread without predicting outcomes
2. **Maker fee = 0%** is a structural advantage — always use GTC limit orders
3. **News-driven trades** need to be fast — information advantage decays in minutes
4. **Diversification matters** — never concentrate >20% in one theme
5. **Neg-risk orderbooks look empty** but the matching engine handles cross-outcome arbitrage

---

## 📄 License

MIT License — Free modules are fully open source. Pro modules show the API surface; full implementation available via [Pro license](https://gumroad.com/l/polymarket-toolkit-pro).

## 🤝 Contributing

PRs welcome for free modules! Areas that could use improvement:
- [ ] WebSocket support for real-time orderbook
- [ ] More signal sources (social media, on-chain)
- [ ] Web dashboard for monitoring
- [ ] Multi-account support
