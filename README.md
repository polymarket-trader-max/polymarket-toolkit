# 🎯 Polymarket Trading Toolkit

A comprehensive suite of automated trading tools for [Polymarket](https://polymarket.com) — the world's largest prediction market.

Built and battle-tested with real capital. Includes market making, signal-based trading, position monitoring, news-driven alerts, backtesting, and more.

## ✨ Features

### Core Strategies
| Module | Description | Risk Level |
|--------|------------|------------|
| `spread_maker.py` | **Market Making** — Place dual-sided orders (YES+NO) to earn the bid-ask spread. Zero prediction needed. | Low |
| `signal_trader.py` | **Signal Trading** — Trade based on quantitative signals: oil price models, live sports scores, time decay | Medium |
| `btc_lag_trader.py` | **BTC Lag Arbitrage** — Exploit price lag between crypto exchanges and Polymarket odds | Medium |
| `maker_bot.py` | **Advanced Market Maker** — Full-featured market making with inventory management | Low |

### Scanners & Analysis
| Module | Description |
|--------|------------|
| `multi_edge_scanner.py` | Scan 100+ markets for mispriced opportunities using multi-signal scoring |
| `edge_scanner.py` | Quick edge detection with configurable thresholds |
| `smart_scanner.py` | AI-enhanced market scanner with category awareness |
| `cross_arb_scanner.py` | Find cross-market arbitrage (correlated events priced inconsistently) |
| `time_decay_scanner.py` | Identify time decay opportunities in expiring markets |
| `market_classifier.py` | Auto-classify markets by type (crypto, sports, politics, etc.) |
| `live_scanner.py` | Real-time opportunity scanner |

### Risk Management
| Module | Description |
|--------|------------|
| `monitor_positions.py` | **Position Monitor** — Auto take-profit & stop-loss execution |
| `live_game_guard.py` | Live sports game monitoring with in-play hedging |
| `trade_now.py` | Quick execution module for urgent trades (FOK market orders) |

### Data & Intelligence
| Module | Description |
|--------|------------|
| `news_monitor.py` | Multi-source news monitoring (Iran, oil, geopolitics, crypto) with trade signals |
| `price_tracker.py` | Historical price tracking and snapshots |
| `btc_scanner.py` | BTC price monitoring for crypto market triggers |
| `data_fetcher.py` | Gamma API data fetcher with caching |

### Backtesting
| Module | Description |
|--------|------------|
| `backtest/engine.py` | Backtesting engine with realistic friction models |
| `backtest/metrics.py` | Performance metrics (Sharpe, max drawdown, win rate, etc.) |
| `run_backtest.py` | Run backtests against historical Polymarket data |
| `calibration_test.py` | Strategy calibration testing |
| `stress_test.py` | Stress testing under extreme scenarios |

### Signal Engine (`signals/`)
Modular signal framework for building custom strategies:
- `base.py` — Signal base class
- `momentum.py` — Price momentum signals
- `liquidity.py` — Liquidity analysis
- `price_series.py` — Time series analysis
- `combo.py` — Multi-signal combination
- `rules.py` — Rule-based signal generation

### Utilities
| Module | Description |
|--------|------------|
| `scorer.py` | Market opportunity scoring (0-100 scale) |
| `trade_utils.py` | Common trading utilities |
| `gen_api_keys.py` | Generate CLOB API credentials |

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install py-clob-client requests python-dotenv
```

### 2. Configure Credentials
```bash
cp .env.example .env
# Edit .env with your Polymarket CLOB API credentials
# Get these from: https://clob.polymarket.com
```

### 3. Run Market Maker (Safest Strategy)
```bash
python spread_maker.py
```

### 4. Run Position Monitor
```bash
python monitor_positions.py
```

### 5. Scan for Opportunities
```bash
python multi_edge_scanner.py
```

## 📊 Architecture

```
┌─────────────────────────────────────────────────┐
│                  Data Layer                       │
│  btc_scanner │ news_monitor │ price_tracker       │
│  data_fetcher │ Gamma API │ CLOB API              │
├─────────────────────────────────────────────────┤
│                Signal Layer                       │
│  signals/* │ btc_direction/* │ market_classifier   │
├─────────────────────────────────────────────────┤
│               Strategy Layer                      │
│  spread_maker │ signal_trader │ btc_lag_trader     │
│  cross_arb │ time_decay │ edge_scanner            │
├─────────────────────────────────────────────────┤
│              Execution Layer                      │
│  maker_bot (GTC) │ trade_now (FOK) │ trade_utils   │
├─────────────────────────────────────────────────┤
│             Risk Management                       │
│  monitor_positions │ live_game_guard │ scorer      │
└─────────────────────────────────────────────────┘
```

## ⚙️ Configuration

Key parameters in `spread_maker.py`:
```python
MIN_VOLUME_24H = 25000    # Minimum 24h volume ($)
MIN_SPREAD     = 0.015    # Minimum spread to trade (1.5¢)
MAX_EXPOSURE   = 10.0     # Max exposure per market ($)
MAX_MARKETS    = 10       # Max concurrent markets
ORDER_SIZE     = 8        # Tokens per order
```

Key parameters in `monitor_positions.py`:
```python
TAKE_PROFIT_PCT = 0.25    # Take profit at +25%
STOP_LOSS_PCT   = 0.20    # Stop loss at -20%
```

## 🏗️ Polymarket Concepts

- **CLOB**: Central Limit Order Book — where all trading happens
- **Maker vs Taker**: Maker orders (GTC limit) = 0% fee. Taker orders = 2% fee
- **YES/NO Tokens**: Binary outcome tokens. YES + NO always = $1 at settlement
- **Negative Risk Markets**: Multi-outcome markets (e.g., elections with 10+ candidates)
- **Proxy Wallet**: Polymarket uses a proxy contract for trading (important for API setup)
- **signature_type=1**: Required for email-registered accounts

## ⚠️ Risk Disclaimer

**This software is for educational and research purposes.** Trading on prediction markets involves real financial risk.

- Past performance does not guarantee future results
- Market making can result in one-sided exposure
- API rate limits may prevent timely order management
- Smart contract risks exist on Polygon
- **Never trade more than you can afford to lose**

## 📈 Real-World Performance Notes

This toolkit was built and refined through live trading with real capital. Key lessons learned:

1. **Market making is the safest strategy** — earn spread without predicting outcomes
2. **Maker fee = 0%** is a structural advantage — always use GTC limit orders
3. **News-driven trades** need to be fast — information advantage decays in minutes
4. **Diversification matters** — never concentrate >20% in one theme
5. **The orderbook in neg-risk markets looks empty** but the matching engine handles cross-outcome arbitrage — limit orders at fair prices will fill

## 📄 License

MIT License — use freely, but at your own risk.

## 🤝 Contributing

PRs welcome! Areas that could use improvement:
- [ ] WebSocket support for real-time orderbook updates
- [ ] More signal sources (social media, on-chain data)
- [ ] Web dashboard for monitoring
- [ ] Multi-account support
- [ ] Better backtesting with real historical orderbook data
