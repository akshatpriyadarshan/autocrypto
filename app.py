"""
AutoCrypto Trader — streamlit run app.py
Pure sync. No asyncio. Works on Python 3.14 + Streamlit Cloud.
"""
import os, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Load Streamlit secrets into env FIRST ─────────────────────────────────────
import streamlit as st
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ[_k] = _v
except Exception:
    pass

# ── Load .env (lower priority than secrets) ───────────────────────────────────
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Ensure valid Fernet key ───────────────────────────────────────────────────
if not os.environ.get("CONFIG_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet
    _key = Fernet.generate_key().decode()
    os.environ["CONFIG_ENCRYPTION_KEY"] = _key

# ── DB, scheduler init (once per session) ─────────────────────────────────────
if "ready" not in st.session_state:
    from backend.db.database import init_db
    init_db()
    from backend.services.scheduler import start_all
    start_all()
    st.session_state.ready = True

# ── Imports ───────────────────────────────────────────────────────────────────
from backend.db.database import get_session
from backend.config.config_manager import (
    get_config, set_config, get_all_plain, reset_fernet, SECRET_KEYS
)
from backend.models.db_models import (
    Signal, Trade, TradeStatus, FundSnapshot,
    Alert, AlertLevel, DailyReport
)
from sqlalchemy import select, desc

# ── Hardcoded Delta credentials (configured for Streamlit Cloud IPs) ──────────
DELTA_API_KEY    = "76wEBRrPbx64EUzphk43LIX1kCWrFb"
DELTA_API_SECRET = "3lJghi3DLRdgeoesLYxfBg5l9jH4Q0HEjLMOkN744dp9dOH4ddiHG6Mv09cH"

# Auto-seed Delta keys into DB on first load if not already set
if "delta_seeded" not in st.session_state:
    with get_session() as db:
        existing = get_config(db, "delta_api_key")
        if not existing:
            set_config(db, "delta_api_key",    DELTA_API_KEY,    is_secret=True)
            set_config(db, "delta_api_secret", DELTA_API_SECRET, is_secret=True)
            set_config(db, "delta_testnet",    "false")  # live keys — not testnet
    st.session_state.delta_seeded = True

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="AutoCrypto Trader", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ── Helpers ───────────────────────────────────────────────────────────────────
def cfg(key):
    with get_session() as db:
        return get_config(db, key)

def set_cfg(key, val, secret=False):
    with get_session() as db:
        set_config(db, key, val, is_secret=secret)

# Delta India API returns balances in USD. Fixed rate: 1 USD = 85 INR.
# All display values multiplied by USD_TO_INR for INR display.
def inr(v, already_inr=False):
    """Format as INR. Assumes input is USD unless already_inr=True."""
    try:
        amount = float(v or 0)
        if not already_inr:
            amount = amount * USD_TO_INR
        return f"₹{amount:,.2f}"
    except: return "₹0.00"

def pct(v):
    try: return f"{float(v or 0):+.2f}%"
    except: return "0.00%"

def ago(dt):
    if not dt: return "—"
    try:
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 60: return f"{s}s ago"
        if s < 3600: return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    except: return "—"

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""<style>
[data-testid="stAppViewContainer"]{background:#0a0e1a}
[data-testid="stSidebar"]{background:#141c2e;border-right:1px solid #1e293b}
.stButton>button{background:linear-gradient(135deg,#1d4ed8,#0f766e)!important;
  color:#fff!important;border:none!important;border-radius:8px!important}
h1,h2,h3{color:#e2e8f0!important}
.stDataFrame{background:#141c2e}
.mc{background:#141c2e;border:1px solid #1e293b;border-radius:10px;padding:16px;margin:4px 0}
.ml{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#64748b;margin-bottom:4px}
.mv{font-size:22px;font-weight:700;font-family:monospace}
.green{color:#22c55e}.red{color:#ef4444}.blue{color:#3b82f6}.amber{color:#f59e0b}.purple{color:#a78bfa}
.ib{background:#0f172a;border:1px solid #1e3a5f;border-radius:8px;padding:12px;margin:8px 0;color:#94a3b8}
.wb{background:#1c1007;border:1px solid #78350f;border-radius:8px;padding:12px;margin:8px 0;color:#fbbf24}
</style>""", unsafe_allow_html=True)

def card(label, val, sub="", color="blue"):
    return f'<div class="mc"><div class="ml">{label}</div><div class="mv {color}">{val}</div>{"<div style=font-size:12px;color:#64748b>"+sub+"</div>" if sub else ""}</div>'

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📈 AutoCrypto Trader")
    st.markdown("---")
    page = st.radio("Navigation", [
        "🏠 Overview", "⚙️ Setup", "📡 Signals",
        "📰 Market Intel", "💹 Trades", "💰 Fund", "🔔 Alerts"
    ], label_visibility="collapsed")
    st.markdown("---")
    bot_active = cfg("bot_active") == "true"
    setup_done = cfg("setup_complete") == "true"
    if bot_active:
        st.success("🟢 Bot Active")
        if st.button("⏹ Stop Bot"):
            set_cfg("bot_active", "false"); st.rerun()
    else:
        st.error("🔴 Bot Inactive")
        if setup_done and st.button("▶ Start Bot"):
            set_cfg("bot_active", "true"); st.rerun()
        elif not setup_done:
            st.caption("Complete Setup first")
    if st.button("🔄 Refresh"): st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🏠 Overview":
    st.title("Overview")

    with get_session() as db:
        _snap   = db.execute(select(FundSnapshot).order_by(desc(FundSnapshot.snapshot_at)).limit(1)).scalar_one_or_none()
        _sigs   = db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(8)).scalars().all()
        _trades = db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(8)).scalars().all()
        _alerts = db.execute(select(Alert).where(
            Alert.resolved==False,
            Alert.level.in_([AlertLevel.CRITICAL,AlertLevel.WARNING])
        ).limit(3)).scalars().all()
        iv      = get_config(db,"candle_interval") or "15m"
        pairs   = get_config(db,"trading_pairs") or "—"
        starting= get_config(db,"starting_capital") or "0"
        # Extract primitives inside session
        snap = {"tb":float(_snap.total_balance),"av":float(_snap.available),
                "lk":float(_snap.locked_25pct),"pd":float(_snap.pnl_today)} if _snap else None
        sigs = [{"dir":s.direction.value,"pair":s.pair,"price":float(s.price),
                 "ok":s.processed,"rej":s.rejected,"why":s.reject_reason or "","at":s.received_at}
                for s in _sigs]
        trades = [{"dir":t.direction.value,"pair":t.pair,"pnl":float(t.pnl) if t.pnl else None,
                   "status":t.status.value,"at":t.opened_at} for t in _trades]
        alerts = [{"level":a.level,"cat":a.category,"msg":a.message} for a in _alerts]

    for a in alerts:
        fn = st.error if a["level"]==AlertLevel.CRITICAL else st.warning
        fn(f"{'🚨' if a['level']==AlertLevel.CRITICAL else '⚠️'} {a['cat']}: {a['msg']}")

    c1,c2,c3,c4 = st.columns(4)
    if snap:
        pc = "green" if snap["pd"]>=0 else "red"
        c1.markdown(card("Total Fund",inr(snap["tb"])), unsafe_allow_html=True)
        c2.markdown(card("Available",inr(snap["av"]),"","green"), unsafe_allow_html=True)
        c3.markdown(card("Today P&L",inr(snap["pd"]),"",pc), unsafe_allow_html=True)
        c4.markdown(card("Locked",inr(snap["lk"]),"","amber"), unsafe_allow_html=True)
    else:
        c1.markdown(card("Starting Capital",inr(starting),"⚠ Not synced"), unsafe_allow_html=True)
        c2.markdown(card("Interval",iv,"","blue"), unsafe_allow_html=True)
        c3.markdown(card("Pairs",str(len([p for p in pairs.split(",") if p.strip()])),"active","purple"), unsafe_allow_html=True)
        c4.markdown(card("Status","Active" if bot_active else "Inactive","","green" if bot_active else "red"), unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### Recent Signals")
        if sigs:
            for s in sigs:
                dc = "#22c55e" if s["dir"]=="buy" else "#ef4444"
                st_txt = "✓" if s["ok"] else (f"✗ {s['why']}" if s["rej"] else "⏳")
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid #1e293b">'
                    f'<span style="color:{dc};font-weight:600">{s["dir"].upper()}</span> '
                    f'<strong style="color:#e2e8f0">{s["pair"]}</strong> {inr(s["price"])} '
                    f'<span style="color:#64748b;font-size:12px">{st_txt} · {ago(s["at"])}</span>'
                    f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="ib">No signals yet. Start the bot.</div>', unsafe_allow_html=True)

    with col2:
        st.markdown("#### Recent Trades")
        if trades:
            for t in trades:
                dc = "#22c55e" if t["dir"]=="buy" else "#ef4444"
                pnl = t["pnl"]; pc = "#22c55e" if (pnl or 0)>=0 else "#ef4444"
                ps = f' <span style="color:{pc}">{inr(pnl)}</span>' if pnl is not None else ""
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid #1e293b">'
                    f'<span style="color:{dc};font-weight:600">{t["dir"].upper()}</span> '
                    f'<strong style="color:#e2e8f0">{t["pair"]}</strong>{ps} '
                    f'<span style="color:#64748b;font-size:12px">{t["status"]} · {ago(t["at"])}</span>'
                    f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="ib">No trades yet.</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# SETUP
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "⚙️ Setup":
    is_edit = cfg("setup_complete") == "true"
    st.title("Edit Setup" if is_edit else "Initial Setup")
    if is_edit:
        st.markdown('<div class="ib">✅ Setup complete. Edit any field and Save to update.</div>', unsafe_allow_html=True)

    existing = {}
    if is_edit:
        with get_session() as db:
            existing = get_all_plain(db)
    def ex(k, d=""): return existing.get(k) or d

    current_bot = cfg("bot_active") or "false"

    from backend.services.signal_engine import RECOMMENDED_PAIRS, FUTURES_PAIRS

    with st.form("setup"):
        st.markdown("#### 🔑 Delta Exchange")
        # Show pre-configured note
        st.info("✅ Delta API credentials pre-configured for Streamlit Cloud IPs. Testnet/Live toggle below.")
        testnet = st.checkbox("Use Testnet (uncheck for Live trading)", value=ex("delta_testnet","false")=="true")

        st.markdown("#### 📬 Email Alerts")
        c1,c2 = st.columns(2)
        email     = c1.text_input("Your Email *", value=ex("email_address"))
        smtp_host = c2.text_input("SMTP Host", value=ex("smtp_host","smtp.gmail.com"))
        c1,c2,c3 = st.columns(3)
        smtp_port = c1.number_input("Port", value=int(ex("smtp_port","587")), min_value=1, max_value=65535)
        smtp_user = c2.text_input("SMTP User", value=ex("smtp_user"))
        smtp_pass = c3.text_input("SMTP Password", value=ex("smtp_password"), type="password")
        smtp_tls  = st.checkbox("Use TLS", value=ex("smtp_use_tls","true")=="true")

        st.markdown("#### 📊 Trading Parameters")
        c1,c2,c3 = st.columns(3)
        capital    = c1.number_input("Starting Capital (INR ₹)", value=float(ex("starting_capital","10000")), min_value=1000.0, step=1000.0)
        risk_pct   = c2.slider("Risk per Trade %", 0.5, 10.0, float(ex("risk_per_trade_pct","2")), 0.5)
        max_trades = c3.number_input("Max Open Trades", value=int(ex("max_open_trades","3")), min_value=1, max_value=10)

        st.markdown("#### 🎯 Pairs & Strategy")
        c1,c2 = st.columns(2)
        spot_sel = c1.multiselect("Spot Pairs",
            ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT"],
            default=[p for p in ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT"]
                     if p in ex("trading_pairs",",".join(RECOMMENDED_PAIRS))])
        fut_sel  = c2.multiselect("Futures Pairs",
            ["DOGE/USDT","LINK/USDT","AVAX/USDT","ADA/USDT"],
            default=[p for p in ["DOGE/USDT","LINK/USDT"]
                     if p in ex("trading_pairs","")])

        c1,c2,c3 = st.columns(3)
        iv_opts  = ["1m","5m","15m","1h","4h"]
        cur_iv   = ex("candle_interval","15m")
        interval = c1.selectbox("Candle Interval", iv_opts,
                                 index=iv_opts.index(cur_iv) if cur_iv in iv_opts else 2)
        sl_type  = c2.selectbox("Stop-Loss Type", ["fixed","atr"],
                                 index=0 if ex("stop_loss_type","fixed")=="fixed" else 1)
        sl_pct   = c3.number_input("Stop-Loss %", value=float(ex("stop_loss_fixed_pct","2")), min_value=0.5, max_value=20.0)

        c1,c2,c3 = st.columns(3)
        max_dd    = c1.number_input("Max Drawdown %", value=float(ex("max_drawdown_pct","15")), min_value=5.0)
        lock_thr  = c2.number_input("Profit Lock At %", value=float(ex("profit_lock_threshold","100")), min_value=10.0)
        lock_pct  = c3.number_input("Lock Amount %", value=float(ex("profit_lock_pct","25")), min_value=5.0, max_value=50.0)

        submitted = st.form_submit_button("💾 Save Setup")

    if submitted:
        errs = []
        if not email or "@" not in email: errs.append("Valid email required")
        if not spot_sel and not fut_sel:  errs.append("Select at least one pair")
        for e in errs: st.error(e)
        if not errs:
            all_pairs = ",".join(spot_sel + fut_sel) or ",".join(RECOMMENDED_PAIRS)
            # Save each key individually — no bulk function, no complexity
            keys = {
                "delta_api_key":          DELTA_API_KEY,
                "delta_api_secret":       DELTA_API_SECRET,
                "delta_testnet":          str(testnet).lower(),
                "tradingview_webhook_secret": "not-used",
                "email_address":          email.strip(),
                "smtp_host":              smtp_host.strip(),
                "smtp_port":              str(int(smtp_port)),
                "smtp_user":              smtp_user.strip(),
                "smtp_password":          smtp_pass,
                "smtp_use_tls":           str(smtp_tls).lower(),
                "starting_capital":       str(capital),
                "risk_per_trade_pct":     str(risk_pct),
                "stop_loss_type":         sl_type,
                "stop_loss_fixed_pct":    str(sl_pct),
                "max_drawdown_pct":       str(max_dd),
                "trading_pairs":          all_pairs,
                "max_open_trades":        str(int(max_trades)),
                "candle_interval":        interval,
                "profit_lock_threshold":  str(lock_thr),
                "profit_lock_pct":        str(lock_pct),
                "setup_complete":         "true",
                "bot_active":             current_bot,
            }
            try:
                with get_session() as db:
                    for k, v in keys.items():
                        set_config(db, k, v, is_secret=(k in SECRET_KEYS))
                    # single commit via context manager exit
                reset_fernet()
                st.success("✅ Setup saved!")
                if not is_edit: st.balloons()
            except Exception as e:
                st.error(f"Save failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNALS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📡 Signals":
    st.title("Signal Engine")
    st.caption("Free analysis via KuCoin/OKX/Bybit — no TradingView needed.")

    with get_session() as db:
        iv     = get_config(db,"candle_interval") or "15m"
        pairs  = get_config(db,"trading_pairs") or "BTC/USDT"
        active = get_config(db,"bot_active") == "true"
        _sigs  = db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(30)).scalars().all()
        sigs   = [{"id":s.id,"pair":s.pair,"dir":s.direction.value.upper(),
                   "price":float(s.price),"atr":float(s.atr) if s.atr else None,
                   "ok":s.processed,"rej":s.rejected,"why":s.reject_reason or "","at":s.received_at}
                  for s in _sigs]

    pairs_list = [p.strip() for p in pairs.split(",") if p.strip()]
    CSECS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}
    nxt   = CSECS.get(iv,900) - (time.time() % CSECS.get(iv,900))

    c1,c2,c3,c4 = st.columns(4)
    sc = "green" if active else "red"
    c1.markdown(card("Status","Running" if active else "Stopped","",sc), unsafe_allow_html=True)
    c2.markdown(card("Interval",iv,"","blue"), unsafe_allow_html=True)
    c3.markdown(card("Next Run",f"{int(nxt//60)}m {int(nxt%60)}s","","amber"), unsafe_allow_html=True)
    c4.markdown(card("Watching",str(len(pairs_list))+" pairs","","purple"), unsafe_allow_html=True)

    if st.button("▶ Run Now", disabled=not active):
        with st.spinner("Analysing…"):
            from backend.services.signal_engine import run_signal_engine
            run_signal_engine()
        st.success("Done!"); st.rerun()

    st.markdown("#### Live Indicators")
    from backend.services.signal_engine import get_indicator_snapshot
    cols = st.columns(min(len(pairs_list), 4))
    for i, pair in enumerate(pairs_list[:4]):
        with cols[i]:
            snap = get_indicator_snapshot(pair, iv)
            if "error" in snap:
                st.error(f"{pair}: {snap['error']}")
            else:
                tc  = "green" if snap["trend"]=="BULL" else "red"
                rc  = "red" if snap["rsi"]>70 else ("green" if snap["rsi"]<30 else "amber")
                vc  = "green" if snap["vol_spike"] else "red"
                ws  = snap.get("would_signal","None")
                wc  = "#22c55e" if ws=="BUY" else ("#ef4444" if ws=="SELL" else "#64748b")
                wl  = f"⚡ SIGNAL: {ws}" if ws != "None" else "No signal this candle"
                # Price: show INR primary, USD secondary
                price_inr = snap["price"] * USD_TO_INR
                st.markdown(f"""<div class="mc"><div class="ml">{pair}</div>
<div class="mv blue">₹{price_inr:,.0f}</div>
<div style="color:#64748b;font-size:11px;margin-bottom:6px">${snap['price']:,.2f} USD</div>
<div style="padding:4px 8px;border-radius:6px;font-size:12px;font-weight:600;color:{wc};background:rgba(99,179,237,0.08);margin-bottom:6px">{wl}</div>
<div style="font-size:12px;line-height:1.8">
Trend: <span class="{tc}"><b>{snap['trend']}</b></span><br>
EMA9: <code style="color:#e2e8f0">{snap['ema_fast']}</code> / EMA21: <code style="color:#e2e8f0">{snap['ema_slow']}</code><br>
RSI: <span class="{rc}"><b>{snap['rsi']}</b></span><br>
Vol Spike: <span class="{vc}"><b>{"Yes ✓" if snap['vol_spike'] else "No"}</b></span><br>
<small style="color:#64748b">{snap['cross']}</small>
</div></div>""", unsafe_allow_html=True)

    st.markdown("#### Strategy: BUY when EMA9 crosses **above** EMA21 + RSI < 70. SELL when EMA9 crosses **below** EMA21 + RSI > 30. Volume spike shown but not required.")

    if sigs:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":s["id"],"Pair":s["pair"],"Dir":s["dir"],"Price (INR)":f"₹{s['price']*USD_TO_INR:,.0f}",
            "ATR":f"{s['atr']:.4f}" if s["atr"] else "—",
            "Status":"✓" if s["ok"] else (f"✗ {s['why']}" if s["rej"] else "⏳"),
            "Time":ago(s["at"]),
        } for s in sigs]))
    else:
        st.info("No signals yet. Start bot — engine runs immediately then every candle close.")

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET INTEL
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📰 Market Intel":
    st.title("Market Intelligence")
    st.caption("Research-driven pair selection — June 2025")

    intel = [
        ("ETH/USDT","🔥 Strong Buy","bull","Pectra upgrade (May 2025). 45% monthly rally. ETF demand. Cup-and-handle above $2,750. Target $3,000–$4,100.","Drop below $2,430 → pullback to $2,060."),
        ("SOL/USDT","🔥 Strong Buy","bull","Memecoin traffic driving on-chain revenue. SOL ETF speculation. Breakout at $150–$160. Pump.fun $22B+ transactions.","Token unlocks create sell pressure. Watch $130."),
        ("XRP/USDT","📈 Buy","bull","US regulatory clarity. Cross-border payment adoption. Support at $2.30–$2.50.","Slow vs ETH/SOL. Binary regulatory risk."),
        ("BTC/USDT","📈 Buy","bull","EMA crossovers intact. MACD bullish. Target $80K–$82K. Bitcoin dominance high.","EMA 200 near $79K is major resistance."),
        ("DOGE/USDT","⚡ Speculative","neutral","X payments integration rumours. High retail interest. Good for short scalps.","Pure sentiment play. No fundamentals."),
        ("LINK/USDT","📈 Buy","bull","DeFi TVL growing → oracle demand rising. Chainlink CCIP adoption increasing.","Lags in early bull moves."),
    ]

    for pair, score, sentiment, why, risk in intel:
        bg = {"bull":"#052e16","bear":"#1c0707","neutral":"#0c0a03"}[sentiment]
        bc = {"bull":"#166534","bear":"#991b1b","neutral":"#78350f"}[sentiment]
        tc = {"bull":"#22c55e","bear":"#ef4444","neutral":"#f59e0b"}[sentiment]
        st.markdown(f"""<div style="background:{bg};border:1px solid {bc};border-radius:10px;padding:14px;margin:8px 0">
<span style="color:{tc};font-weight:700">{score}</span>
<strong style="color:#e2e8f0;font-size:16px"> {pair}</strong>
<div style="color:#94a3b8;font-size:13px;margin-top:6px">{why}</div>
<div style="color:#ef4444;font-size:12px;margin-top:4px">⚠ Risk: {risk}</div>
</div>""", unsafe_allow_html=True)

    st.markdown("#### Avoid for Now")
    for pair, reason in [("SUI/USDT","Below key resistance, weak momentum"),
                          ("AVAX/USDT","Not enough volume for clean signals"),
                          ("ADA/USDT","Chang upgrade improving but momentum weak")]:
        st.markdown(f'<div style="background:#1c0707;border:1px solid #7f1d1d;border-radius:8px;padding:10px;margin:4px 0">'
                    f'<span style="color:#ef4444;font-weight:600">⛔ {pair}</span> — <span style="color:#94a3b8;font-size:13px">{reason}</span>'
                    f'</div>', unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💹 Trades":
    st.title("Trades")

    with get_session() as db:
        _trades = db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(100)).scalars().all()
        trades = [{
            "id":t.id,"pair":t.pair,"dir":t.direction.value.upper(),"status":t.status.value,
            "entry":float(t.entry_price) if t.entry_price else None,
            "exit":float(t.exit_price) if t.exit_price else None,
            "sl":float(t.stop_loss_price) if t.stop_loss_price else None,
            "qty":float(t.quantity),
            "pnl":float(t.pnl) if t.pnl else None,
            "pnl_pct":float(t.pnl_pct) if t.pnl_pct else None,
            "opened":t.opened_at,"closed":t.closed_at,
        } for t in _trades]

    closed  = [t for t in trades if t["status"]=="closed"]
    won     = [t for t in closed if (t["pnl"] or 0)>0]
    total_p = sum(t["pnl"] or 0 for t in closed)
    open_c  = sum(1 for t in trades if t["status"]=="open")

    c1,c2,c3,c4 = st.columns(4)
    pc = "green" if total_p>=0 else "red"
    c1.markdown(card("Open",str(open_c),"","amber"), unsafe_allow_html=True)
    c2.markdown(card("Closed",str(len(closed)),"","blue"), unsafe_allow_html=True)
    c3.markdown(card("Win Rate",f"{round(len(won)/len(closed)*100 if closed else 0,1)}%",
                     f"{len(won)}W/{len(closed)-len(won)}L","green"), unsafe_allow_html=True)
    c4.markdown(card("Total P&L",inr(total_p),"",pc), unsafe_allow_html=True)

    if trades:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":t["id"],"Pair":t["pair"],"Dir":t["dir"],"Status":t["status"],
            "Entry":f"₹{t['entry']*USD_TO_INR:,.0f}" if t["entry"] else "—",
            "Exit":f"₹{t['exit']*USD_TO_INR:,.0f}" if t["exit"] else "—",
            "SL":f"₹{t['sl']*USD_TO_INR:,.0f}" if t["sl"] else "—",
            "Qty":round(t["qty"],6),
            "P&L":f"₹{t['pnl']*USD_TO_INR:,.2f}" if t["pnl"] is not None else "—",
            "P&L%":pct(t["pnl_pct"]) if t["pnl_pct"] is not None else "—",
            "Opened":ago(t["opened"]),"Closed":ago(t["closed"]) if t["closed"] else "—",
        } for t in trades]))
    else:
        st.info("No trades yet.")

# ═══════════════════════════════════════════════════════════════════════════════
# FUND
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💰 Fund":
    st.title("Fund")

    with get_session() as db:
        _snap    = db.execute(select(FundSnapshot).order_by(desc(FundSnapshot.snapshot_at)).limit(1)).scalar_one_or_none()
        _reports = db.execute(select(DailyReport).order_by(desc(DailyReport.report_date)).limit(30)).scalars().all()
        starting = get_config(db,"starting_capital") or "0"
        snap = {"tb":float(_snap.total_balance),"av":float(_snap.available),
                "lk":float(_snap.locked_25pct),"pt":float(_snap.pnl_total),
                "ms":_snap.milestone_hit} if _snap else None
        reports = [{"date":r.report_date.strftime("%d %b %Y") if r.report_date else "—",
                    "start":float(r.starting_fund),"end":float(r.ending_fund),
                    "locked":float(r.locked_fund),"trades":r.trades_count,
                    "won":r.winning_trades,"lost":r.losing_trades,
                    "pnl_day":float(r.pnl_day),"pnl_total":float(r.pnl_total),
                    "email":r.email_sent} for r in _reports]

    if snap:
        pc = "green" if snap["pt"]>=0 else "red"
        c1,c2,c3,c4 = st.columns(4)
        c1.markdown(card("Total Balance",inr(snap["tb"])), unsafe_allow_html=True)
        c2.markdown(card("Available",inr(snap["av"]),"","green"), unsafe_allow_html=True)
        c3.markdown(card("Locked (Protected)",inr(snap["lk"]),"","amber"), unsafe_allow_html=True)
        c4.markdown(card("All-time P&L",inr(snap["pt"]),"",pc), unsafe_allow_html=True)
        if snap["ms"]: st.success("🎉 Profit milestone reached!")
        st.caption(f"Note: Delta India balance in USD × {int(USD_TO_INR)} = INR (fixed rate per Delta Exchange India)")
    else:
        st.markdown(f'<div class="wb">⚠️ No exchange sync yet. Configured: ₹{float(starting):,.2f} INR. Click Sync to fetch real balance from Delta Exchange.</div>', unsafe_allow_html=True)

    if st.button("🔄 Sync Balance from Delta"):
        with st.spinner("Connecting to Delta Exchange…"):
            try:
                from backend.services.fund_manager import take_fund_snapshot
                result = take_fund_snapshot()
                st.success(f"Synced! Balance: {inr(result.get('total',0))}")
                st.rerun()
            except Exception as e:
                st.error(f"Sync failed: {e}")

    if reports:
        import pandas as pd
        st.markdown("#### Daily Reports")
        st.dataframe(pd.DataFrame([{
            "Date":r["date"],"Start":inr(r["start"]),"End":inr(r["end"]),
            "Locked":inr(r["locked"]),"Trades":r["trades"],
            "W/L":f"{r['won']}W/{r['lost']}L",
            "Day P&L":inr(r["pnl_day"]),"Total P&L":inr(r["pnl_total"]),
            "Email":"✓" if r["email"] else "—",
        } for r in reports]))

# ═══════════════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔔 Alerts":
    st.title("Alerts")

    with get_session() as db:
        _alerts = db.execute(select(Alert).order_by(desc(Alert.created_at)).limit(50)).scalars().all()
        alerts = [{"id":a.id,"level":a.level.value.upper(),"cat":a.category,
                   "msg":a.message[:100],"resolved":a.resolved,"at":a.created_at}
                  for a in _alerts]

    unresolved = [a for a in alerts if not a["resolved"]]
    if unresolved: st.warning(f"{len(unresolved)} unresolved alert(s)")
    else: st.success("All clear")

    if alerts:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":a["id"],"Level":a["level"],"Category":a["cat"],
            "Message":a["msg"],"Resolved":"✓" if a["resolved"] else "—","Time":ago(a["at"]),
        } for a in alerts]))
        if unresolved:
            aid = st.number_input("Alert ID to resolve", min_value=1, step=1, value=unresolved[0]["id"])
            if st.button("Mark Resolved"):
                with get_session() as db:
                    a = db.execute(select(Alert).where(Alert.id==int(aid))).scalar_one_or_none()
                    if a:
                        a.resolved = True
                        a.resolved_at = datetime.now(timezone.utc)
                st.success("Resolved"); st.rerun()
    else:
        st.info("No alerts.")
