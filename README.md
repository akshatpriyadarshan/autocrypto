# AutoCrypto Trader

Single-command automated crypto trading app.
Free signal engine (no TradingView needed) + Delta Exchange execution.

## Quick Start

```bash
# 1. Setup (once)
chmod +x setup_local.sh && ./setup_local.sh

# 2. Run
source venv/bin/activate
streamlit run app.py
# Opens at http://localhost:8501
```

## What it does
- Fetches live charts from Binance (free, no API key)
- Computes EMA9/21 crossover + RSI + Volume signals
- Places orders on Delta Exchange automatically
- Monitors stop-loss every 30 seconds
- Locks 25% profit when fund doubles
- Sends daily email reports + emergency alerts

## Pages
| Page | Purpose |
|------|---------|
| Overview | Live stats, recent signals and trades |
| Setup | Configure exchange, email, trading rules |
| Signal Engine | Live indicator values, manual trigger |
| Trades | Full trade history with P&L |
| Fund | Balance, locked funds, daily reports |
| Alerts | System warnings and critical events |

## Key Rules
- Risk per trade: configurable % of available fund
- Stop-loss: fixed % or ATR-based, checked every 30s
- 25% lock: when fund doubles, 25% locked permanently
- Max drawdown: bot pauses if daily loss > configured %
- Daily report: emailed at 8 PM IST
