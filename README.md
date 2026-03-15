# 🎯 Polymarket Trading Toolkit

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![GitHub Stars](https://img.shields.io/github/stars/polymarket-trader-max/polymarket-toolkit?style=social)](https://github.com/polymarket-trader-max/polymarket-toolkit)

**Automated trading tools for [Polymarket](https://polymarket.com) — the world's largest prediction market.**

Market making · Signal trading · BTC lag arbitrage · Position monitoring · Backtesting

> Built and battle-tested with real capital. 41 modules, 10,000+ lines of Python.

---

## 💡 Why This Toolkit?

Most Polymarket bots try to predict outcomes — and lose money. **Only 7.6% of wallets are profitable.**

This toolkit takes a different approach:

- 🏦 **Market making** earns from bid-ask spread, not from being right
- ⚡ **Lag arbitrage** exploits price delays between Binance and Polymarket
- 📊 **Signal trading** only enters with quantified edge ≥ 5%
- 💸 **0% maker fee** — all strategies use GTC limit orders

> *70% of whale profits come from market structure (making + arb), not predicting events.*

---

## 🆓 Free vs Pro

**15 free modules** with full source code. **26 Pro modules** for serious traders.

### Free (included in this repo)

- `edge_scanner.py` — Find mispriced markets with configurable thresholds
- `live_scanner.py` — Real-time opportunity scanner
- `market_classifier.py` — Auto-classify markets (crypto, sports, politics…)
- `monitor_positions.py` — **Auto take-profit & stop-loss** for open positions
- `price_tracker.py` — Historical price tracking and snapshots
- `data_fetcher.py` — Gamma API data fetcher with caching
- `btc_scanner.py` — BTC price monitoring for triggers
- `scorer.py` — Market opportunity scoring (0-100 scale)
- `calibration_test.py` — Strategy calibration testing
- `trade_utils.py` — Common trading utilities
- `gen_api_keys.py` — Generate CLOB API credentials
- `signals/base.py` — Signal base class & interface

### Pro ($39 USDC)

- 🤖 **Market Making** — `spread_maker` · `maker_bot` · `maker_trader` — earn spread without predicting
- 📈 **Signal Trading** — Oil price models, live sports, time decay exploitation
- ⚡ **BTC Lag Arbitrage** — Brownian motion model exploits exchange-to-Polymarket delay
- 🔍 **Advanced Scanners** — `multi_edge_scanner` · `cross_arb_scanner` · `smart_scanner` · `time_decay_scanner`
- 🧪 **Backtesting Suite** — Full engine with realistic friction, Sharpe ratio, max drawdown
- 📡 **News Monitor** — Geopolitical + crypto news → automated trade signals
- 🎮 **Live Game Guard** — Real-time sports monitoring with in-play hedging
- 🧠 **Signal Engine** — 5 additional signal modules (momentum, liquidity, time series, combo, rules)
- 📊 **BTC Direction** — Full signal engine + lag arb engine + auto-trader + scanner

> All 41 modules. Full source code. No obfuscation. Modify anything.

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/polymarket-trader-max/polymarket-toolkit.git
cd polymarket-toolkit
pip install py-clob-client requests python-dotenv
```

### 2. Configure

```bash
cp .env.example .env
# Add your Polymarket CLOB API credentials
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
┌───────────────────────────────────────────────┐
│  Data Layer                                   │
│  btc_scanner · price_tracker · data_fetcher   │
│  Gamma API · CLOB API · Binance API           │
├───────────────────────────────────────────────┤
│  Signal Layer                                 │
│  signals/base · momentum · liquidity · rules  │
│  btc_direction · market_classifier            │
├───────────────────────────────────────────────┤
│  Strategy Layer                               │
│  spread_maker · signal_trader · btc_lag_arb   │
│  cross_arb · time_decay · multi_edge_scanner  │
├───────────────────────────────────────────────┤
│  Execution Layer                              │
│  trade_utils · trade_now · maker_bot          │
├───────────────────────────────────────────────┤
│  Risk Management                              │
│  monitor_positions · live_game_guard          │
│  scorer · stress_test                         │
└───────────────────────────────────────────────┘
```

---

## 🔑 Key Concepts

| Concept | Description |
|---------|-------------|
| **CLOB** | Central Limit Order Book — Polymarket's core trading engine |
| **Maker vs Taker** | Maker (GTC limit) = **0% fee**. Taker = 2% fee |
| **YES/NO Tokens** | Binary outcome tokens. YES + NO = $1 at settlement |
| **Proxy Wallet** | Polymarket uses a proxy contract — `signature_type=1` for email accounts |
| **GTC Limit Orders** | Good-Till-Cancelled — the only way to get 0% maker fee |

---

## 💰 Pro Version

**$39 USDC** ~~$49~~ — All 41 modules, 10,000+ lines, lifetime access.

### How to Buy

1. **Send $39 USDC** on Polygon to:
   ```
   0x73727711e62fFAe1d5Ec9E0a9aE79CcBbd1e6D47
   ```
2. **Open an [Issue](https://github.com/polymarket-trader-max/polymarket-toolkit/issues/new)** with your tx hash + GitHub username
3. **Get access** within 24h to the private `polymarket-toolkit-pro` repo

> ⚡ USDC on **Polygon** only. Full source code. Lifetime updates.

---

## ⚙️ Configuration

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

For educational and research purposes. Trading prediction markets involves real financial risk. Past performance ≠ future results. Never trade more than you can afford to lose.

---

## 📄 License

MIT — Free modules are fully open source.

## 🤝 Contributing

PRs welcome! Areas that could use help:

- [ ] WebSocket support for real-time orderbook
- [ ] More signal sources (social media, on-chain data)
- [ ] Web dashboard for position monitoring
- [ ] Multi-account support

---

**⭐ Star this repo if you find it useful — it helps others discover it!**
