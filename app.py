"""
AutoCrypto Trader — Streamlit App
Single command: streamlit run app.py
"""
import asyncio, os, sys, time, threading
from datetime import datetime, timezone
from pathlib import Path

# ── Path & env setup ──────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{ROOT}/data/autocrypto.db")

env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

if not os.environ.get("CONFIG_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["CONFIG_ENCRYPTION_KEY"] = key
    with open(env_file, "a") as f:
        f.write(f"\nCONFIG_ENCRYPTION_KEY={key}\n")

os.makedirs(ROOT / "data", exist_ok=True)

# ── Fix event loop BEFORE streamlit import ────────────────────────────────────
# nest_asyncio patches the running loop so run_until_complete can be nested.
# This is the correct fix for Streamlit + asyncio.
import nest_asyncio
nest_asyncio.apply()

import streamlit as st

st.set_page_config(page_title="AutoCrypto Trader", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ── Async runner ──────────────────────────────────────────────────────────────
def run(coro):
    """
    Safe async runner for Streamlit.
    nest_asyncio allows nested run_until_complete calls.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

# ── DB init (once per session) ────────────────────────────────────────────────
if "db_ready" not in st.session_state:
    from backend.db.database import init_db
    run(init_db())
    st.session_state.db_ready = True

# ── Scheduler thread (once per session) ──────────────────────────────────────
if "scheduler_started" not in st.session_state:
    def _scheduler_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        nest_asyncio.apply(loop)
        from backend.services.scheduler import start_all
        loop.run_until_complete(start_all())
        loop.run_forever()
    t = threading.Thread(target=_scheduler_thread, daemon=True, name="scheduler")
    t.start()
    st.session_state.scheduler_started = True

# ── Imports ───────────────────────────────────────────────────────────────────
from backend.db.database import AsyncSessionLocal
from backend.config.config_manager import (
    get_config, set_config, bulk_set_config,
    get_all_config_plain, SECRET_KEYS
)
from backend.models.db_models import (
    Signal, Trade, TradeStatus, FundSnapshot,
    Alert, AlertLevel, DailyReport
)
from sqlalchemy import select, desc, func

# ── Helpers ───────────────────────────────────────────────────────────────────
def inr(v):
    try: return f"₹{float(v or 0):,.2f}"
    except: return "₹0.00"

def pct(v):
    try: return f"{float(v or 0):+.2f}%"
    except: return "0.00%"

def ago(dt):
    if not dt: return "—"
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 0: return "just now"
        if s < 60: return f"{s}s ago"
        if s < 3600: return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    except: return "—"

async def _cfg(key):
    async with AsyncSessionLocal() as db:
        return await get_config(db, key)

async def _set_cfg(key, val, secret=False):
    async with AsyncSessionLocal() as db:
        await set_config(db, key, val, is_secret=secret)
        await db.commit()

async def _all_cfg_plain():
    async with AsyncSessionLocal() as db:
        return await get_all_config_plain(db)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""<style>
[data-testid="stAppViewContainer"]{background:#0a0e1a}
[data-testid="stSidebar"]{background:#141c2e;border-right:1px solid rgba(255,255,255,0.08)}
[data-testid="stSidebar"] *{color:#e2e8f0 !important}
.stButton>button{background:linear-gradient(135deg,#2b6cb0,#285e61) !important;
  color:#fff !important;border:none !important;border-radius:8px !important;font-weight:500 !important}
h1,h2,h3,h4{color:#e2e8f0 !important}
p,.stMarkdown p{color:#a0aec0}
.stDataFrame{background:#141c2e}
div[data-testid="stForm"]{background:#141c2e;border:1px solid rgba(255,255,255,0.08);
  border-radius:10px;padding:16px}
.mc{background:#141c2e;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:16px;margin:4px 0}
.ml{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#718096;margin-bottom:4px}
.mv{font-size:22px;font-weight:700;font-family:monospace}
.ms{font-size:12px;color:#718096;margin-top:2px}
.green{color:#48bb78}.red{color:#fc8181}.blue{color:#63b3ed}.amber{color:#f6ad55}
.info-box{background:rgba(99,179,237,.06);border:1px solid rgba(99,179,237,.2);
  border-radius:8px;padding:12px 16px;margin:8px 0;color:#a0aec0}
.warn-box{background:rgba(246,173,85,.06);border:1px solid rgba(246,173,85,.2);
  border-radius:8px;padding:12px 16px;margin:8px 0;color:#a0aec0}
</style>""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📈 AutoCrypto Trader")
    st.markdown("---")
    page = st.radio("Navigate", [
        "🏠 Overview", "⚙️ Setup", "📡 Signal Engine",
        "💹 Trades", "💰 Fund", "🔔 Alerts"
    ], label_visibility="collapsed")
    st.markdown("---")

    bot_active  = run(_cfg("bot_active")) == "true"
    setup_done  = run(_cfg("setup_complete")) == "true"

    if bot_active:
        st.success("🟢 Bot Active")
        if st.button("⏹ Stop Bot", use_container_width=True):
            run(_set_cfg("bot_active", "false"))
            st.rerun()
    else:
        st.error("🔴 Bot Inactive")
        if setup_done and st.button("▶ Start Bot", use_container_width=True):
            run(_set_cfg("bot_active", "true"))
            st.rerun()
        elif not setup_done:
            st.caption("Complete Setup first")

    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True):
        st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🏠 Overview":
    st.title("Overview")

    async def _overview():
        async with AsyncSessionLocal() as db:
            snap_r  = await db.execute(select(FundSnapshot).order_by(desc(FundSnapshot.snapshot_at)).limit(1))
            sig_r   = await db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(8))
            tr_r    = await db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(8))
            open_r  = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
            al_r    = await db.execute(select(Alert).where(
                Alert.resolved == False,
                Alert.level.in_([AlertLevel.CRITICAL, AlertLevel.WARNING])
            ).limit(5))
            iv    = await get_config(db, "candle_interval") or "15m"
            pairs = await get_config(db, "trading_pairs") or "BTC/USDT"
            start = await get_config(db, "starting_capital") or "0"
            return (snap_r.scalar_one_or_none(), sig_r.scalars().all(),
                    tr_r.scalars().all(), len(open_r.scalars().all()),
                    al_r.scalars().all(), iv, pairs, start)

    snap, sigs, trades, open_c, alerts, iv, pairs, starting = run(_overview())

    for a in alerts:
        if a.level == AlertLevel.CRITICAL:
            st.error(f"🚨 CRITICAL — {a.category}: {a.message}")
        else:
            st.warning(f"⚠️ {a.category}: {a.message}")

    c1, c2, c3, c4 = st.columns(4)
    if snap:
        tb = float(snap.total_balance); av = float(snap.available)
        lk = float(snap.locked_25pct); pd = float(snap.pnl_today)
        pc = "green" if pd >= 0 else "red"
        c1.markdown(f'<div class="mc"><div class="ml">Total Fund</div><div class="mv blue">{inr(tb)}</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="mc"><div class="ml">Available</div><div class="mv green">{inr(av)}</div></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="mc"><div class="ml">Today P&L</div><div class="mv {pc}">{inr(pd)}</div></div>', unsafe_allow_html=True)
        c4.markdown(f'<div class="mc"><div class="ml">Locked (Safe)</div><div class="mv amber">{inr(lk)}</div></div>', unsafe_allow_html=True)
    else:
        c1.markdown(f'<div class="mc"><div class="ml">Configured Capital</div><div class="mv blue">{inr(starting)}</div><div class="ms" style="color:#f6ad55">⚠ Not synced with exchange yet</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="mc"><div class="ml">Open Trades</div><div class="mv amber">{open_c}</div></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="mc"><div class="ml">Candle Interval</div><div class="mv blue">{iv}</div></div>', unsafe_allow_html=True)
        c4.markdown(f'<div class="mc"><div class="ml">Pairs</div><div class="mv" style="font-size:14px">{pairs}</div></div>', unsafe_allow_html=True)

    st.markdown("")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 📡 Recent Signals")
        if sigs:
            for s in sigs:
                dc = "#48bb78" if str(s.direction.value) == "buy" else "#fc8181"
                sig_status = "✓" if s.processed else ("✗ " + (s.reject_reason or "") if s.rejected else "⏳")
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05)">'
                            f'<span style="color:{dc};font-weight:600">{s.direction.value.upper()}</span> '
                            f'<strong style="color:#e2e8f0">{s.pair}</strong> @ {inr(s.price)} '
                            f'<span style="color:#718096;font-size:12px">{ago(s.received_at)} {sig_status}</span>'
                            f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="info-box">No signals yet. Start the bot to begin.</div>', unsafe_allow_html=True)

    with col2:
        st.markdown("#### 💹 Recent Trades")
        if trades:
            for t in trades:
                dc   = "#48bb78" if str(t.direction.value) == "buy" else "#fc8181"
                pnl  = float(t.pnl or 0)
                pc   = "#48bb78" if pnl >= 0 else "#fc8181"
                pstr = f' &nbsp;<span style="color:{pc}">{inr(pnl)}</span>' if t.pnl else ""
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05)">'
                            f'<span style="color:{dc};font-weight:600">{t.direction.value.upper()}</span> '
                            f'<strong style="color:#e2e8f0">{t.pair}</strong>{pstr} '
                            f'<span style="color:#718096;font-size:12px">{t.status.value} · {ago(t.opened_at)}</span>'
                            f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="info-box">No trades yet.</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# SETUP — loads existing config, edit mode
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Setup":
    is_edit = run(_cfg("setup_complete")) == "true"
    st.title("⚙️ " + ("Edit Setup" if is_edit else "Initial Setup"))

    if is_edit:
        st.markdown('<div class="info-box">✅ Setup complete. Edit any field below and Save to update.</div>', unsafe_allow_html=True)

    # Load ALL existing values upfront (sync, before any form/async nesting)
    existing = run(_all_cfg_plain()) if is_edit else {}
    def ex(key, default=""):
        return existing.get(key) or default

    # Read bot_active now (sync, outside form) to avoid nested run() later
    current_bot_active = run(_cfg("bot_active")) or "false"

    with st.form("setup_form", clear_on_submit=False):
        st.markdown("#### 🔑 Delta Exchange API")
        c1, c2 = st.columns(2)
        api_key    = c1.text_input("API Key *", value=ex("delta_api_key"), type="password",
                                    help="From delta.exchange → Account → API")
        api_secret = c2.text_input("API Secret *", value=ex("delta_api_secret"), type="password")
        testnet    = st.checkbox("Use Testnet (recommended for testing first)",
                                  value=ex("delta_testnet", "true") == "true")

        st.markdown("#### 📬 Email Alerts & Reports")
        c1, c2 = st.columns(2)
        email      = c1.text_input("Your Email *", value=ex("email_address"),
                                    placeholder="you@gmail.com")
        smtp_host  = c2.text_input("SMTP Host", value=ex("smtp_host", "smtp.gmail.com"))
        c1, c2, c3 = st.columns(3)
        smtp_port  = c1.number_input("SMTP Port", value=int(ex("smtp_port", "587")),
                                      min_value=1, max_value=65535)
        smtp_user  = c2.text_input("SMTP Username", value=ex("smtp_user"))
        smtp_pass  = c3.text_input("SMTP Password", value=ex("smtp_password"), type="password")
        smtp_tls   = st.checkbox("Use TLS/STARTTLS", value=ex("smtp_use_tls", "true") == "true")

        st.markdown("#### 📊 Trading Parameters")
        c1, c2, c3 = st.columns(3)
        capital    = c1.number_input("Starting Capital (INR ₹) *",
                                      value=float(ex("starting_capital", "10000")),
                                      min_value=100.0, step=1000.0)
        risk_pct   = c2.slider("Risk per Trade %", 0.5, 10.0,
                                float(ex("risk_per_trade_pct", "2")), 0.5)
        max_trades = c3.number_input("Max Open Trades",
                                      value=int(ex("max_open_trades", "3")),
                                      min_value=1, max_value=10)

        c1, c2 = st.columns(2)
        pairs      = c1.text_input("Trading Pairs (comma-separated)",
                                    value=ex("trading_pairs", "BTC/USDT,ETH/USDT"),
                                    help="e.g. BTC/USDT,ETH/USDT,SOL/USDT")
        iv_opts    = ["1m", "5m", "15m", "1h", "4h"]
        cur_iv     = ex("candle_interval", "15m")
        iv_idx     = iv_opts.index(cur_iv) if cur_iv in iv_opts else 2
        interval   = c2.selectbox("Candle Interval", iv_opts, index=iv_idx,
                                   help="How often the engine analyses charts")

        c1, c2 = st.columns(2)
        sl_opts  = ["fixed", "atr"]
        cur_sl   = ex("stop_loss_type", "fixed")
        sl_idx   = sl_opts.index(cur_sl) if cur_sl in sl_opts else 0
        sl_type  = c1.selectbox("Stop-Loss Type", sl_opts, index=sl_idx)
        sl_pct   = c2.number_input("Fixed Stop-Loss %",
                                    value=float(ex("stop_loss_fixed_pct", "2")),
                                    min_value=0.5, max_value=20.0, step=0.5)

        c1, c2, c3 = st.columns(3)
        max_dd     = c1.number_input("Max Daily Drawdown %",
                                      value=float(ex("max_drawdown_pct", "15")),
                                      min_value=5.0, max_value=50.0)
        lock_thr   = c2.number_input("Profit Lock Threshold %",
                                      value=float(ex("profit_lock_threshold", "100")),
                                      min_value=10.0)
        lock_pct_v = c3.number_input("Lock % on Milestone",
                                      value=float(ex("profit_lock_pct", "25")),
                                      min_value=5.0, max_value=50.0)

        submitted = st.form_submit_button("💾 Save Setup", use_container_width=True)

    if submitted:
        errs = []
        if not api_key.strip():    errs.append("Delta API Key is required")
        if not api_secret.strip(): errs.append("Delta API Secret is required")
        if not email.strip() or "@" not in email: errs.append("Valid email address required")
        if not pairs.strip():      errs.append("At least one trading pair required")

        if errs:
            for e in errs:
                st.error(e)
        else:
            # Build save dict — use pre-read current_bot_active, no nested run()
            async def _save(data: dict):
                async with AsyncSessionLocal() as db:
                    await bulk_set_config(db, data, secret_keys=SECRET_KEYS)

            save_data = {
                "delta_api_key":              api_key.strip(),
                "delta_api_secret":           api_secret.strip(),
                "delta_testnet":              str(testnet).lower(),
                "tradingview_webhook_secret": "not-used",
                "email_address":              email.strip(),
                "smtp_host":                  smtp_host.strip(),
                "smtp_port":                  str(int(smtp_port)),
                "smtp_user":                  smtp_user.strip(),
                "smtp_password":              smtp_pass,
                "smtp_use_tls":               str(smtp_tls).lower(),
                "starting_capital":           str(capital),
                "risk_per_trade_pct":         str(risk_pct),
                "stop_loss_type":             sl_type,
                "stop_loss_fixed_pct":        str(sl_pct),
                "max_drawdown_pct":           str(max_dd),
                "trading_pairs":              pairs.strip(),
                "max_open_trades":            str(int(max_trades)),
                "candle_interval":            interval,
                "profit_lock_threshold":      str(lock_thr),
                "profit_lock_pct":            str(lock_pct_v),
                "setup_complete":             "true",
                "bot_active":                 current_bot_active,
            }
            with st.spinner("Saving…"):
                run(_save(save_data))
            st.success("✅ Setup saved! Use the sidebar to Start Bot.")
            if not is_edit:
                st.balloons()

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📡 Signal Engine":
    st.title("Signal Engine")
    st.caption("Built-in free chart analyser — Binance data, no TradingView subscription needed.")

    async def _engine_meta():
        async with AsyncSessionLocal() as db:
            iv     = await get_config(db, "candle_interval") or "15m"
            pairs  = await get_config(db, "trading_pairs") or "BTC/USDT"
            active = await get_config(db, "bot_active") == "true"
            r      = await db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(20))
            sigs   = r.scalars().all()
        return iv, pairs, active, sigs

    iv, pairs_str, active, sigs = run(_engine_meta())
    pairs_list = [p.strip() for p in pairs_str.split(",") if p.strip()]
    CSECS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}
    nxt   = CSECS.get(iv, 900) - (time.time() % CSECS.get(iv, 900))

    c1, c2, c3, c4 = st.columns(4)
    sc = "green" if active else "red"
    c1.markdown(f'<div class="mc"><div class="ml">Status</div><div class="mv {sc}">{"Running" if active else "Stopped"}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="mc"><div class="ml">Interval</div><div class="mv blue">{iv}</div></div>', unsafe_allow_html=True)
    c3.markdown(f'<div class="mc"><div class="ml">Next Run</div><div class="mv amber">{int(nxt//60)}m {int(nxt%60)}s</div></div>', unsafe_allow_html=True)
    c4.markdown(f'<div class="mc"><div class="ml">Pairs</div><div class="mv" style="font-size:16px">{len(pairs_list)}</div></div>', unsafe_allow_html=True)

    st.markdown("")

    col1, _ = st.columns([1, 3])
    with col1:
        if st.button("▶ Run Engine Now", use_container_width=True,
                     disabled=not active,
                     help="Manually trigger signal analysis"):
            with st.spinner("Analysing charts…"):
                from backend.services.signal_engine import run_signal_engine
                run(run_signal_engine())
            st.success("Done! Check signals below.")
            st.rerun()

    st.markdown("#### 📊 Live Indicator Snapshot")
    st.caption("Current values fetched from Binance now — not historical.")

    from backend.services.signal_engine import get_indicator_snapshot
    cols = st.columns(max(len(pairs_list), 1))
    for i, pair in enumerate(pairs_list):
        with cols[i]:
            with st.spinner(f"{pair}…"):
                snap = run(get_indicator_snapshot(pair, iv))
            if "error" in snap:
                st.error(f"{pair}: {snap['error']}")
            else:
                rsi = snap["rsi"]
                rc  = "red" if rsi > 65 else ("green" if rsi < 35 else "amber")
                tc  = "green" if snap["trend"] == "BULL" else "red"
                vc  = "green" if snap["vol_spike"] else "red"
                st.markdown(f"""<div class="mc">
<div class="ml">{pair}</div>
<div class="mv blue">{inr(snap['price'])}</div>
<div style="margin-top:8px;font-size:13px;line-height:1.8">
  <div>Trend: <span class="{tc}"><b>{snap['trend']}</b></span></div>
  <div>EMA {9}: <code style="color:#e2e8f0">{snap['ema_fast']}</code></div>
  <div>EMA {21}: <code style="color:#e2e8f0">{snap['ema_slow']}</code></div>
  <div>RSI: <span class="{rc}"><b>{rsi}</b></span></div>
  <div>ATR: <code style="color:#e2e8f0">{snap['atr']}</code></div>
  <div>Vol Spike: <span class="{vc}"><b>{"Yes ✓" if snap['vol_spike'] else "No"}</b></span></div>
  <div style="margin-top:6px;padding:4px 8px;background:rgba(99,179,237,0.1);border-radius:4px;font-size:12px">{snap['cross']}</div>
</div></div>""", unsafe_allow_html=True)

    st.markdown("#### Strategy Logic")
    c1, c2 = st.columns(2)
    c1.markdown("""**BUY signal requires:**
- EMA 9 crosses **above** EMA 21
- RSI(14) **below 65** (not overbought)  
- Volume **>1.5×** 20-bar average""")
    c2.markdown("""**SELL signal requires:**
- EMA 9 crosses **below** EMA 21  
- RSI(14) **above 35** (not oversold)
- Volume **>1.5×** 20-bar average""")

    st.markdown("#### Recent Signals")
    if sigs:
        import pandas as pd
        rows = []
        for s in sigs:
            rows.append({
                "ID": s.id, "Pair": s.pair,
                "Direction": s.direction.value.upper(),
                "Price": inr(s.price),
                "ATR": f"{float(s.atr):.4f}" if s.atr else "—",
                "Status": ("✓ Processed" if s.processed
                           else ("✗ " + (s.reject_reason or "Rejected") if s.rejected
                           else "⏳ Pending")),
                "Time": ago(s.received_at),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No signals yet. Start the bot — engine runs immediately, then every candle close.")

# ═══════════════════════════════════════════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💹 Trades":
    st.title("Trades")

    async def _trades():
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(100))
            return r.scalars().all()

    trades    = run(_trades())
    open_t    = [t for t in trades if t.status == TradeStatus.OPEN]
    closed_t  = [t for t in trades if t.status == TradeStatus.CLOSED]
    won       = [t for t in closed_t if t.pnl and float(t.pnl) > 0]
    total_pnl = sum(float(t.pnl or 0) for t in closed_t)

    c1, c2, c3, c4 = st.columns(4)
    pc = "green" if total_pnl >= 0 else "red"
    c1.markdown(f'<div class="mc"><div class="ml">Open</div><div class="mv amber">{len(open_t)}</div></div>', unsafe_allow_html=True)
    c2.markdown(f'<div class="mc"><div class="ml">Closed</div><div class="mv blue">{len(closed_t)}</div></div>', unsafe_allow_html=True)
    wr = round(len(won)/len(closed_t)*100 if closed_t else 0, 1)
    c3.markdown(f'<div class="mc"><div class="ml">Win Rate</div><div class="mv green">{wr}%</div><div class="ms">{len(won)}W / {len(closed_t)-len(won)}L</div></div>', unsafe_allow_html=True)
    c4.markdown(f'<div class="mc"><div class="ml">Total P&L</div><div class="mv {pc}">{inr(total_pnl)}</div></div>', unsafe_allow_html=True)

    if trades:
        import pandas as pd
        rows = [{
            "ID": t.id, "Pair": t.pair, "Dir": t.direction.value.upper(),
            "Status": t.status.value,
            "Entry": inr(t.entry_price) if t.entry_price else "—",
            "Exit": inr(t.exit_price) if t.exit_price else "—",
            "SL": inr(t.stop_loss_price) if t.stop_loss_price else "—",
            "Qty": round(float(t.quantity), 6),
            "P&L": inr(t.pnl) if t.pnl else "—",
            "P&L %": pct(t.pnl_pct) if t.pnl_pct else "—",
            "Opened": ago(t.opened_at),
            "Closed": ago(t.closed_at) if t.closed_at else "—",
        } for t in trades]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet. Signals generate trades automatically when bot is active.")

# ═══════════════════════════════════════════════════════════════════════════════
# FUND
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💰 Fund":
    st.title("Fund")

    async def _fund():
        async with AsyncSessionLocal() as db:
            s  = await db.execute(select(FundSnapshot).order_by(desc(FundSnapshot.snapshot_at)).limit(1))
            rr = await db.execute(select(DailyReport).order_by(desc(DailyReport.report_date)).limit(30))
            start = await get_config(db, "starting_capital") or "0"
            return s.scalar_one_or_none(), rr.scalars().all(), start

    snap, reports, starting = run(_fund())

    if snap:
        tb = float(snap.total_balance); av = float(snap.available)
        lk = float(snap.locked_25pct); pt = float(snap.pnl_total)
        pc = "green" if pt >= 0 else "red"
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f'<div class="mc"><div class="ml">Total Balance</div><div class="mv blue">{inr(tb)}</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="mc"><div class="ml">Available</div><div class="mv green">{inr(av)}</div></div>', unsafe_allow_html=True)
        c3.markdown(f'<div class="mc"><div class="ml">Locked (Protected)</div><div class="mv amber">{inr(lk)}</div></div>', unsafe_allow_html=True)
        c4.markdown(f'<div class="mc"><div class="ml">All-time P&L</div><div class="mv {pc}">{inr(pt)}</div></div>', unsafe_allow_html=True)
        if snap.milestone_hit:
            st.success("🎉 Profit milestone reached! Funds locked for protection.")
    else:
        st.markdown(f'<div class="warn-box">⚠️ No exchange sync yet. Configured capital: <strong>₹{float(starting):,.2f}</strong><br>'
                    f'This is your setup value — NOT your real exchange balance. Click Sync to fetch real balance.</div>',
                    unsafe_allow_html=True)

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("🔄 Sync Balance Now", use_container_width=True):
            with st.spinner("Fetching balance from Delta Exchange…"):
                from backend.services.fund_manager import take_fund_snapshot
                try:
                    result = run(take_fund_snapshot())
                    st.success(f"Synced! Balance: {inr(result.get('total', 0))}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Sync failed: {e}")

    if reports:
        import pandas as pd
        st.markdown("#### Daily Reports")
        rows = [{
            "Date": r.report_date.strftime("%d %b %Y") if r.report_date else "—",
            "Start": inr(r.starting_fund), "End": inr(r.ending_fund),
            "Locked": inr(r.locked_fund), "Trades": r.trades_count,
            "W/L": f"{r.winning_trades}W/{r.losing_trades}L",
            "Day P&L": inr(r.pnl_day), "Total P&L": inr(r.pnl_total),
            "Email": "✓" if r.email_sent else "—",
        } for r in reports]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Daily reports will appear here after the first full trading day.")

# ═══════════════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔔 Alerts":
    st.title("Alerts")

    async def _alerts():
        async with AsyncSessionLocal() as db:
            r = await db.execute(select(Alert).order_by(desc(Alert.created_at)).limit(50))
            return r.scalars().all()

    alerts = run(_alerts())
    unresolved = [a for a in alerts if not a.resolved]
    if unresolved:
        st.warning(f"{len(unresolved)} unresolved alert(s) need attention")
    else:
        st.success("All clear — no active alerts")

    if alerts:
        import pandas as pd
        rows = [{
            "ID": a.id,
            "Level": a.level.value.upper(),
            "Category": a.category,
            "Message": a.message[:100],
            "Resolved": "✓" if a.resolved else "—",
            "Time": ago(a.created_at),
        } for a in alerts]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        if unresolved:
            st.markdown("#### Resolve Alert")
            aid = st.number_input("Alert ID", min_value=1, step=1,
                                   value=unresolved[0].id)
            if st.button("Mark as Resolved"):
                async def _resolve(alert_id: int):
                    async with AsyncSessionLocal() as db:
                        r = await db.execute(select(Alert).where(Alert.id == alert_id))
                        a = r.scalar_one_or_none()
                        if a:
                            a.resolved = True
                            a.resolved_at = datetime.now(timezone.utc)
                            await db.commit()
                            return True
                        return False
                if run(_resolve(int(aid))):
                    st.success("Alert resolved")
                    st.rerun()
                else:
                    st.error("Alert not found")
    else:
        st.info("No alerts yet.")
