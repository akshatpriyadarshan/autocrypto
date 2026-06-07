"""
AutoCrypto Trader — Streamlit App
streamlit run app.py
Pure sync — no asyncio, no anyio threads = no Python 3.14 compatibility issues.
"""
import os, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path

# ── Paths & env ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Sync SQLite URL — no aiosqlite
_raw = os.getenv("DATABASE_URL", f"sqlite:///{ROOT}/data/autocrypto.db")
os.environ["DATABASE_URL"] = _raw.replace("+aiosqlite", "")

env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Load from Streamlit secrets first (persists on Streamlit Cloud)
try:
    import streamlit as _st_check
    _secrets = _st_check.secrets
    for _k in ["CONFIG_ENCRYPTION_KEY", "DATABASE_URL"]:
        if _k in _secrets and not os.environ.get(_k):
            os.environ[_k] = _secrets[_k]
except Exception:
    pass

if not os.environ.get("CONFIG_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["CONFIG_ENCRYPTION_KEY"] = key
    try:
        with open(env_file, "a") as f:
            f.write(f"\nCONFIG_ENCRYPTION_KEY={key}\n")
    except Exception:
        pass  # Read-only filesystem on Streamlit Cloud — key only lasts session

os.makedirs(ROOT / "data", exist_ok=True)

import streamlit as st
st.set_page_config(page_title="AutoCrypto Trader", page_icon="📈",
                   layout="wide", initial_sidebar_state="expanded")

# ── DB init once ──────────────────────────────────────────────────────────────
if "db_ready" not in st.session_state:
    from backend.db.database import init_db
    init_db()
    st.session_state.db_ready = True

# ── Scheduler thread once ─────────────────────────────────────────────────────
if "scheduler_started" not in st.session_state:
    from backend.services.scheduler import start_all
    start_all()
    st.session_state.scheduler_started = True

# ── Imports ───────────────────────────────────────────────────────────────────
from backend.db.database import get_session
from backend.config.config_manager import (
    get_config, set_config, bulk_set_config,
    get_all_config_plain, SECRET_KEYS
)
from backend.models.db_models import (
    Signal, Trade, TradeStatus, FundSnapshot,
    Alert, AlertLevel, DailyReport
)
from backend.services.signal_engine import (
    RECOMMENDED_PAIRS, FUTURES_PAIRS, get_indicator_snapshot
)
from sqlalchemy import select, desc, func

# ── Helpers ───────────────────────────────────────────────────────────────────
def cfg(key):
    with get_session() as db: return get_config(db, key)

def set_cfg(key, val, secret=False):
    with get_session() as db:
        set_config(db, key, val, is_secret=secret)
        db.commit()

def all_cfg():
    with get_session() as db: return get_all_config_plain(db)

def inr(v):
    try: return f"₹{float(v or 0):,.2f}"
    except: return "₹0.00"

def pct(v):
    try: return f"{float(v or 0):+.2f}%"
    except: return "0.00%"

def ago(dt):
    if not dt: return "—"
    try:
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        s = int((datetime.now(timezone.utc) - dt).total_seconds())
        if s < 0: return "just now"
        if s < 60: return f"{s}s ago"
        if s < 3600: return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    except: return "—"

def mc(label, val, sub="", color="blue"):
    return f'<div class="mc"><div class="ml">{label}</div><div class="mv {color}">{val}</div>{"<div class=ms>"+sub+"</div>" if sub else ""}</div>'

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""<style>
[data-testid="stAppViewContainer"]{background:#0a0e1a}
[data-testid="stSidebar"]{background:#141c2e;border-right:1px solid rgba(255,255,255,0.08)}
[data-testid="stSidebar"] *{color:#e2e8f0 !important}
.stButton>button{background:linear-gradient(135deg,#2b6cb0,#285e61) !important;
  color:#fff !important;border:none !important;border-radius:8px !important}
h1,h2,h3,h4{color:#e2e8f0 !important}
p,.stMarkdown p{color:#a0aec0}
.mc{background:#141c2e;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:16px;margin:4px 0}
.ml{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#718096;margin-bottom:4px}
.mv{font-size:22px;font-weight:700;font-family:monospace}
.ms{font-size:12px;color:#718096;margin-top:2px}
.green{color:#48bb78}.red{color:#fc8181}.blue{color:#63b3ed}.amber{color:#f6ad55}.purple{color:#9f7aea}
.ib{background:rgba(99,179,237,.06);border:1px solid rgba(99,179,237,.2);border-radius:8px;padding:12px 16px;margin:8px 0;color:#a0aec0}
.wb{background:rgba(246,173,85,.06);border:1px solid rgba(246,173,85,.2);border-radius:8px;padding:12px 16px;margin:8px 0;color:#a0aec0}
.news-card{background:#141c2e;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:14px;margin:6px 0}
.news-tag{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;margin-bottom:6px}
.bull{background:rgba(72,187,120,0.15);color:#48bb78}
.bear{background:rgba(252,129,129,0.15);color:#fc8181}
.neutral{background:rgba(113,128,150,0.15);color:#718096}
</style>""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📈 AutoCrypto Trader")
    st.markdown("---")
    page = st.radio("Navigate", [
        "🏠 Overview", "⚙️ Setup", "📡 Signal Engine",
        "📰 Market Intel", "💹 Trades", "💰 Fund", "🔔 Alerts"
    ], label_visibility="collapsed")
    st.markdown("---")
    bot_active = cfg("bot_active") == "true"
    setup_done = cfg("setup_complete") == "true"
    if bot_active:
        st.success("🟢 Bot Active")
        if st.button("⏹ Stop Bot", use_container_width=True):
            set_cfg("bot_active", "false")
            st.rerun()
    else:
        st.error("🔴 Bot Inactive")
        if setup_done and st.button("▶ Start Bot", use_container_width=True):
            set_cfg("bot_active", "true")
            st.rerun()
        elif not setup_done:
            st.caption("Complete Setup first")
    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True): st.rerun()

# ═══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════
if page == "🏠 Overview":
    st.title("Overview")
    # Agent 1 fix: extract ALL primitive values inside session — prevents DetachedInstanceError
    with get_session() as db:
        _snap   = db.execute(select(FundSnapshot).order_by(desc(FundSnapshot.snapshot_at)).limit(1)).scalar_one_or_none()
        _sigs   = db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(8)).scalars().all()
        _trades = db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(8)).scalars().all()
        open_c  = len(db.execute(select(Trade).where(Trade.status==TradeStatus.OPEN)).scalars().all())
        _alerts = db.execute(select(Alert).where(Alert.resolved==False,Alert.level.in_([AlertLevel.CRITICAL,AlertLevel.WARNING])).limit(5)).scalars().all()
        iv      = get_config(db,"candle_interval") or "15m"
        pairs   = get_config(db,"trading_pairs") or ""
        starting= get_config(db,"starting_capital") or "0"
        # Extract primitives while session is open
        snap_data = {
            "tb": float(_snap.total_balance), "av": float(_snap.available),
            "lk": float(_snap.locked_25pct), "pd": float(_snap.pnl_today)
        } if _snap else None
        sigs = [{"dir": str(s.direction.value), "pair": s.pair, "price": float(s.price),
                 "processed": s.processed, "rejected": s.rejected,
                 "reason": s.reject_reason or "", "at": s.received_at} for s in _sigs]
        trades = [{"dir": str(t.direction.value), "pair": t.pair,
                   "pnl": float(t.pnl) if t.pnl else None,
                   "status": t.status.value, "at": t.opened_at} for t in _trades]
        alerts = [{"level": a.level, "cat": a.category, "msg": a.message} for a in _alerts]

    for a in alerts:
        if a["level"]==AlertLevel.CRITICAL: st.error(f"🚨 {a['cat']}: {a['msg']}")
        else: st.warning(f"⚠️ {a['cat']}: {a['msg']}")

    c1,c2,c3,c4 = st.columns(4)
    if snap_data:
        tb=snap_data["tb"]; av=snap_data["av"]; lk=snap_data["lk"]; pd=snap_data["pd"]
        pc="green" if pd>=0 else "red"
        c1.markdown(mc("Total Fund",inr(tb))             , unsafe_allow_html=True)
        c2.markdown(mc("Available",inr(av),"","green")   , unsafe_allow_html=True)
        c3.markdown(mc("Today P&L",inr(pd),"",pc)        , unsafe_allow_html=True)
        c4.markdown(mc("Locked",inr(lk),"","amber")      , unsafe_allow_html=True)
    else:
        c1.markdown(mc("Configured Capital",inr(starting),"⚠ Not synced yet"), unsafe_allow_html=True)
        c2.markdown(mc("Open Trades",str(open_c),"","amber"), unsafe_allow_html=True)
        c3.markdown(mc("Interval",iv,"","blue"), unsafe_allow_html=True)
        c4.markdown(mc("Pairs",str(len([p for p in pairs.split(",") if p.strip()])),"active","purple"), unsafe_allow_html=True)

    st.markdown("")
    col1,col2 = st.columns(2)
    with col1:
        st.markdown("#### 📡 Recent Signals")
        if sigs:
            for s in sigs:
                dc="#48bb78" if s["dir"]=="buy" else "#fc8181"
                st_txt="✓" if s["processed"] else ("✗ "+s["reason"] if s["rejected"] else "⏳")
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05)">'
                    f'<span style="color:{dc};font-weight:600">{s["dir"].upper()}</span> '
                    f'<strong style="color:#e2e8f0">{s["pair"]}</strong> @ {inr(s["price"])} '
                    f'<span style="color:#718096;font-size:12px">{st_txt} · {ago(s["at"])}</span>'
                    f'</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="ib">No signals yet. Start the bot to begin analysis.</div>', unsafe_allow_html=True)

    with col2:
        st.markdown("#### 💹 Recent Trades")
        if trades:
            for t in trades:
                dc="#48bb78" if t["dir"]=="buy" else "#fc8181"
                pnl=t["pnl"]; pc="#48bb78" if (pnl or 0)>=0 else "#fc8181"
                pstr=f' <span style="color:{pc}">{inr(pnl)}</span>' if pnl is not None else ""
                st.markdown(f'<div style="padding:8px;border-bottom:1px solid rgba(255,255,255,0.05)">'
                    f'<span style="color:{dc};font-weight:600">{t["dir"].upper()}</span> '
                    f'<strong style="color:#e2e8f0">{t["pair"]}</strong>{pstr} '
                    f'<span style="color:#718096;font-size:12px">{t["status"]} · {ago(t["at"])}</span>'
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

    existing = all_cfg() if is_edit else {}
    def ex(key, default=""): return existing.get(key) or default
    current_bot = cfg("bot_active") or "false"

    # Default pairs = recommended
    default_pairs = ",".join(RECOMMENDED_PAIRS)

    with st.form("setup_form", clear_on_submit=False):
        st.markdown("#### 🔑 Delta Exchange")
        c1,c2 = st.columns(2)
        api_key    = c1.text_input("API Key *", value=ex("delta_api_key"), type="password")
        api_secret = c2.text_input("API Secret *", value=ex("delta_api_secret"), type="password")
        testnet    = st.checkbox("Use Testnet", value=ex("delta_testnet","true")=="true")

        st.markdown("#### 📬 Email")
        c1,c2 = st.columns(2)
        email      = c1.text_input("Email *", value=ex("email_address"))
        smtp_host  = c2.text_input("SMTP Host", value=ex("smtp_host","smtp.gmail.com"))
        c1,c2,c3  = st.columns(3)
        smtp_port  = c1.number_input("Port", value=int(ex("smtp_port","587")), min_value=1, max_value=65535)
        smtp_user  = c2.text_input("SMTP User", value=ex("smtp_user"))
        smtp_pass  = c3.text_input("SMTP Password", value=ex("smtp_password"), type="password")
        smtp_tls   = st.checkbox("Use TLS", value=ex("smtp_use_tls","true")=="true")

        st.markdown("#### 📊 Trading Parameters")
        c1,c2,c3  = st.columns(3)
        capital    = c1.number_input("Starting Capital (INR ₹) *", value=float(ex("starting_capital","10000")), min_value=100.0, step=1000.0)
        risk_pct   = c2.slider("Risk per Trade %", 0.5, 10.0, float(ex("risk_per_trade_pct","2")), 0.5)
        max_trades = c3.number_input("Max Open Trades", value=int(ex("max_open_trades","3")), min_value=1, max_value=10)

        st.markdown("#### 🎯 Trading Pairs")
        st.caption("Delta Exchange India spot: BTC, ETH, SOL, XRP. Futures: + DOGE, LINK, AVAX, ADA and 50+ more.")
        c1,c2 = st.columns(2)
        spot_pairs = c1.multiselect(
            "Spot Pairs (confirmed on Delta India)",
            ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT"],
            default=[p for p in ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT"] if p in ex("trading_pairs",",".join(RECOMMENDED_PAIRS))],
            help="These 4 are confirmed spot pairs on Delta Exchange India"
        )
        futures_sel = c2.multiselect(
            "Futures Pairs (perpetuals, higher risk)",
            ["DOGE/USDT","LINK/USDT","AVAX/USDT","ADA/USDT","DOT/USDT","MATIC/USDT","SUI/USDT","HYPE/USDT"],
            default=[p for p in ["DOGE/USDT","LINK/USDT"] if p in ex("trading_pairs","")],
            help="Perpetual futures — higher volatility and profit potential"
        )
        all_pairs = ",".join(spot_pairs + futures_sel) or default_pairs

        c1,c2 = st.columns(2)
        iv_opts = ["1m","5m","15m","1h","4h"]
        cur_iv  = ex("candle_interval","15m")
        interval = c1.selectbox("Candle Interval", iv_opts, index=iv_opts.index(cur_iv) if cur_iv in iv_opts else 2)
        sl_opts  = ["fixed","atr"]
        cur_sl   = ex("stop_loss_type","fixed")
        sl_type  = c2.selectbox("Stop-Loss Type", sl_opts, index=sl_opts.index(cur_sl) if cur_sl in sl_opts else 0)

        c1,c2,c3 = st.columns(3)
        sl_pct     = c1.number_input("Fixed SL %", value=float(ex("stop_loss_fixed_pct","2")), min_value=0.5, max_value=20.0, step=0.5)
        max_dd     = c2.number_input("Max Drawdown %", value=float(ex("max_drawdown_pct","15")), min_value=5.0, max_value=50.0)
        lock_thr   = c3.number_input("Profit Lock Threshold %", value=float(ex("profit_lock_threshold","100")), min_value=10.0)
        lock_pct_v = st.number_input("Lock % on Milestone", value=float(ex("profit_lock_pct","25")), min_value=5.0, max_value=50.0)

        submitted = st.form_submit_button("💾 Save Setup", use_container_width=True)

    if submitted:
        errs = []
        if not api_key.strip():    errs.append("Delta API Key required")
        if not api_secret.strip(): errs.append("Delta API Secret required")
        if not email.strip() or "@" not in email: errs.append("Valid email required")
        if not spot_pairs and not futures_sel: errs.append("Select at least one trading pair")
        for e in errs: st.error(e)
        if not errs:
            data = {
                "delta_api_key":api_key.strip(),"delta_api_secret":api_secret.strip(),
                "delta_testnet":str(testnet).lower(),"tradingview_webhook_secret":"not-used",
                "email_address":email.strip(),"smtp_host":smtp_host.strip(),
                "smtp_port":str(int(smtp_port)),"smtp_user":smtp_user.strip(),
                "smtp_password":smtp_pass,"smtp_use_tls":str(smtp_tls).lower(),
                "starting_capital":str(capital),"risk_per_trade_pct":str(risk_pct),
                "stop_loss_type":sl_type,"stop_loss_fixed_pct":str(sl_pct),
                "max_drawdown_pct":str(max_dd),"trading_pairs":all_pairs,
                "max_open_trades":str(int(max_trades)),"candle_interval":interval,
                "profit_lock_threshold":str(lock_thr),"profit_lock_pct":str(lock_pct_v),
                "setup_complete":"true","bot_active":current_bot,
            }
            with get_session() as db:
                bulk_set_config(db, data, secret_keys=SECRET_KEYS)
            st.success("✅ Setup saved! Start Bot from the sidebar.")
            if not is_edit: st.balloons()

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📡 Signal Engine":
    st.title("Signal Engine")
    st.caption("Free chart analysis — Binance public data, no subscription needed.")

    with get_session() as db:
        iv     = get_config(db,"candle_interval") or "15m"
        pairs  = get_config(db,"trading_pairs") or ",".join(RECOMMENDED_PAIRS)
        active = get_config(db,"bot_active") == "true"
        _sigs  = db.execute(select(Signal).order_by(desc(Signal.received_at)).limit(20)).scalars().all()
        sigs   = [{"id":s.id,"pair":s.pair,"dir":s.direction.value.upper(),
                   "price":float(s.price),"atr":float(s.atr) if s.atr else None,
                   "processed":s.processed,"rejected":s.rejected,
                   "reason":s.reject_reason or "","at":s.received_at} for s in _sigs]

    pairs_list = [p.strip() for p in pairs.split(",") if p.strip()]
    CSECS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}
    nxt   = CSECS.get(iv,900) - (time.time() % CSECS.get(iv,900))

    c1,c2,c3,c4 = st.columns(4)
    sc = "green" if active else "red"
    c1.markdown(mc("Status","Running" if active else "Stopped","",sc), unsafe_allow_html=True)
    c2.markdown(mc("Interval",iv,"","blue"), unsafe_allow_html=True)
    c3.markdown(mc("Next Run",f"{int(nxt//60)}m {int(nxt%60)}s","","amber"), unsafe_allow_html=True)
    c4.markdown(mc("Pairs Watching",str(len(pairs_list)),"","purple"), unsafe_allow_html=True)

    col1,_ = st.columns([1,3])
    with col1:
        if st.button("▶ Run Now", use_container_width=True, disabled=not active):
            with st.spinner("Analysing…"):
                from backend.services.signal_engine import run_signal_engine
                run_signal_engine()
            st.success("Done! Check signals below."); st.rerun()

    st.markdown("#### 📊 Live Indicator Snapshot")
    if pairs_list:
        cols = st.columns(min(len(pairs_list), 4))
        for i, pair in enumerate(pairs_list[:4]):
            with cols[i]:
                with st.spinner(f"{pair}…"):
                    snap = get_indicator_snapshot(pair, iv)
                if "error" in snap:
                    st.error(f"{pair}: {snap['error']}")
                else:
                    rsi=snap["rsi"]; rc="red" if rsi>65 else ("green" if rsi<35 else "amber")
                    tc="green" if snap["trend"]=="BULL" else "red"
                    vc="green" if snap["vol_spike"] else "red"
                    st.markdown(f"""<div class="mc">
<div class="ml">{pair}</div>
<div class="mv blue">{inr(snap['price'])}</div>
<div style="margin-top:8px;font-size:13px;line-height:1.9">
  Trend: <span class="{tc}"><b>{snap['trend']}</b></span><br>
  EMA9: <code style="color:#e2e8f0">{snap['ema_fast']}</code><br>
  EMA21: <code style="color:#e2e8f0">{snap['ema_slow']}</code><br>
  RSI: <span class="{rc}"><b>{rsi}</b></span><br>
  ATR: <code style="color:#e2e8f0">{snap['atr']}</code><br>
  Vol Spike: <span class="{vc}"><b>{"✓ Yes" if snap['vol_spike'] else "No"}</b></span><br>
  <div style="margin-top:6px;padding:4px 8px;background:rgba(99,179,237,0.1);border-radius:4px;font-size:12px">{snap['cross']}</div>
</div></div>""", unsafe_allow_html=True)

        if len(pairs_list) > 4:
            st.markdown("#### Additional Pairs")
            cols2 = st.columns(min(len(pairs_list)-4, 4))
            for i, pair in enumerate(pairs_list[4:8]):
                with cols2[i]:
                    snap = get_indicator_snapshot(pair, iv)
                    if "error" not in snap:
                        tc="green" if snap["trend"]=="BULL" else "red"
                        st.markdown(f'<div class="mc"><div class="ml">{pair}</div>'
                            f'<div class="mv blue">{inr(snap["price"])}</div>'
                            f'<div>Trend: <span class="{tc}"><b>{snap["trend"]}</b></span></div>'
                            f'<div>RSI: {snap["rsi"]}</div></div>', unsafe_allow_html=True)

    st.markdown("#### Strategy")
    c1,c2 = st.columns(2)
    c1.markdown("**BUY:** EMA9 crosses ↑ EMA21 · RSI < 65 · Volume spike > 1.5×")
    c2.markdown("**SELL:** EMA9 crosses ↓ EMA21 · RSI > 35 · Volume spike > 1.5×")

    st.markdown("#### Recent Signals")
    if sigs:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":s["id"],"Pair":s["pair"],"Dir":s["dir"],
            "Price":inr(s["price"]),"ATR":f"{s['atr']:.4f}" if s["atr"] else "—",
            "Status":"✓ Processed" if s["processed"] else ("✗ "+s["reason"] if s["rejected"] else "⏳ Pending"),
            "Time":ago(s["at"]),
        } for s in sigs]), use_container_width=True, hide_index=True)
    else:
        st.info("No signals yet. Start bot — engine runs immediately then every candle close.")

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET INTEL  — news-driven pair analysis
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "📰 Market Intel":
    st.title("Market Intelligence")
    st.caption("Research-driven pair selection based on fundamentals + technicals. Updated manually.")

    st.markdown("#### 🎯 Recommended Pairs — June 2025 Analysis")

    intel = [
        {
            "pair":"ETH/USDT","sentiment":"bull","score":"🔥 Strong Buy",
            "why":"Pectra upgrade (May 2025) boosted scalability. Cup-and-handle breakout forming above $2,750. ETF demand growing — open interest >$20B. 45% monthly rally in May. Target: $3,000–$4,100.",
            "risk":"If drops below $2,430 support, pullback to $2,060 possible.",
            "delta":"✅ Spot + Futures available on Delta India",
        },
        {
            "pair":"SOL/USDT","sentiment":"bull","score":"🔥 Strong Buy",
            "why":"Memecoin traffic driving on-chain revenue. SOL ETF speculation building. Breakout zone at $150–$160 — cleared path to $200+ if held. Pump.fun processed $22.3B+ in transactions.",
            "risk":"Token unlock events can create sell pressure. Watch $130 support.",
            "delta":"✅ Spot + Futures available on Delta India",
        },
        {
            "pair":"XRP/USDT","sentiment":"bull","score":"📈 Buy",
            "why":"US regulatory clarity (Ripple vs SEC resolution). Cross-border payment adoption accelerating. Key resistance at $2.30–$2.50. Institutional support remains strong.",
            "risk":"Regulatory news remains binary risk. Slow momentum vs ETH/SOL.",
            "delta":"✅ Spot + Futures available on Delta India",
        },
        {
            "pair":"BTC/USDT","sentiment":"bull","score":"📈 Buy",
            "why":"Recovering above key EMAs (20/50/100). MACD bullish crossover. Higher-high structure forming. Target $80K–$82K zone. Bitcoin Season index still high — capital not rotating to alts yet.",
            "risk":"EMA 200 near $79K is major resistance. Altcoin season not confirmed yet.",
            "delta":"✅ Spot + Futures available on Delta India",
        },
        {
            "pair":"DOGE/USDT","sentiment":"neutral","score":"⚡ Speculative",
            "why":"X/Twitter payments integration rumours persistent. High retail interest. Musk social media drives short spikes. Best for short scalp trades on spikes.",
            "risk":"No strong fundamental driver. Pure sentiment play. High volatility.",
            "delta":"✅ Futures on Delta India",
        },
        {
            "pair":"LINK/USDT","sentiment":"bull","score":"📈 Buy",
            "why":"DeFi TVL growing — oracle demand increasing. Chainlink CCIP cross-chain protocol adoption. Often outperforms in DeFi rallies.",
            "risk":"DeFi summer not confirmed yet. Lags BTC/ETH in early bull moves.",
            "delta":"✅ Futures on Delta India",
        },
    ]

    for item in intel:
        sc_class = "bull" if item["sentiment"]=="bull" else ("bear" if item["sentiment"]=="bear" else "neutral")
        st.markdown(f"""<div class="news-card">
<span class="news-tag {sc_class}">{item['score']}</span>
<strong style="color:#e2e8f0;font-size:16px"> {item['pair']}</strong>
<span style="color:#718096;font-size:12px;float:right">{item['delta']}</span>
<div style="color:#a0aec0;font-size:13px;margin-top:6px;line-height:1.7">{item['why']}</div>
<div style="color:#fc8181;font-size:12px;margin-top:6px">⚠ Risk: {item['risk']}</div>
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### ❌ Pairs to Avoid Right Now")
    avoid = [
        ("SUI/USDT","Bearish — trading below $1 resistance, struggled to reclaim levels since 2025 decline."),
        ("AVAX/USDT","Subnet activity picking up but not enough volume yet for clean EMA signals."),
        ("ADA/USDT","Chang upgrade improving sentiment but momentum weak vs ETH/SOL."),
    ]
    for pair, reason in avoid:
        st.markdown(f'<div class="news-card"><span class="news-tag bear">⛔ Avoid</span> <strong style="color:#e2e8f0"> {pair}</strong><div style="color:#a0aec0;font-size:13px;margin-top:4px">{reason}</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    st.info("💡 This intel is updated manually. For live technical signals, go to Signal Engine page.")

# ═══════════════════════════════════════════════════════════════════════════════
# TRADES
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "💹 Trades":
    st.title("Trades")
    with get_session() as db:
        _trades = db.execute(select(Trade).order_by(desc(Trade.opened_at)).limit(100)).scalars().all()
        # Extract ALL primitives inside session
        trades = [{
            "id":t.id, "pair":t.pair, "dir":t.direction.value.upper(),
            "status":t.status.value,
            "entry":float(t.entry_price) if t.entry_price else None,
            "exit":float(t.exit_price) if t.exit_price else None,
            "sl":float(t.stop_loss_price) if t.stop_loss_price else None,
            "qty":float(t.quantity),
            "pnl":float(t.pnl) if t.pnl else None,
            "pnl_pct":float(t.pnl_pct) if t.pnl_pct else None,
            "opened_at":t.opened_at, "closed_at":t.closed_at,
            "is_open": t.status.value == "open",
            "is_closed": t.status.value == "closed",
        } for t in _trades]
    open_c   = sum(1 for t in trades if t["is_open"])
    closed_t = [t for t in trades if t["is_closed"]]
    won      = [t for t in closed_t if (t["pnl"] or 0) > 0]
    total_pnl= sum(t["pnl"] or 0 for t in closed_t)
    c1,c2,c3,c4=st.columns(4)
    pc="green" if total_pnl>=0 else "red"
    wr=round(len(won)/len(closed_t)*100 if closed_t else 0,1)
    c1.markdown(mc("Open",str(open_c),"","amber"), unsafe_allow_html=True)
    c2.markdown(mc("Closed",str(len(closed_t)),"","blue"), unsafe_allow_html=True)
    c3.markdown(mc("Win Rate",f"{wr}%",f"{len(won)}W / {len(closed_t)-len(won)}L","green"), unsafe_allow_html=True)
    c4.markdown(mc("Total P&L",inr(total_pnl),"",pc), unsafe_allow_html=True)
    if trades:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":t["id"], "Pair":t["pair"], "Dir":t["dir"], "Status":t["status"],
            "Entry":inr(t["entry"]) if t["entry"] else "—",
            "Exit":inr(t["exit"]) if t["exit"] else "—",
            "SL":inr(t["sl"]) if t["sl"] else "—",
            "Qty":round(t["qty"],6),
            "P&L":inr(t["pnl"]) if t["pnl"] is not None else "—",
            "P&L%":pct(t["pnl_pct"]) if t["pnl_pct"] is not None else "—",
            "Opened":ago(t["opened_at"]),
            "Closed":ago(t["closed_at"]) if t["closed_at"] else "—",
        } for t in trades]), use_container_width=True, hide_index=True)
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
        # Extract primitives inside session
        snap_d = {
            "tb": float(_snap.total_balance), "av": float(_snap.available),
            "lk": float(_snap.locked_25pct), "pt": float(_snap.pnl_total),
            "milestone": bool(_snap.milestone_hit),
        } if _snap else None
        reports = [{
            "date": r.report_date.strftime("%d %b %Y") if r.report_date else "—",
            "start": float(r.starting_fund), "end": float(r.ending_fund),
            "locked": float(r.locked_fund), "trades": r.trades_count,
            "won": r.winning_trades, "lost": r.losing_trades,
            "pnl_day": float(r.pnl_day), "pnl_total": float(r.pnl_total),
            "email": r.email_sent,
        } for r in _reports]

    if snap_d:
        tb=snap_d["tb"]; av=snap_d["av"]; lk=snap_d["lk"]; pt=snap_d["pt"]
        pc="green" if pt>=0 else "red"
        c1,c2,c3,c4=st.columns(4)
        c1.markdown(mc("Total Balance",inr(tb)), unsafe_allow_html=True)
        c2.markdown(mc("Available",inr(av),"","green"), unsafe_allow_html=True)
        c3.markdown(mc("Locked (Protected)",inr(lk),"","amber"), unsafe_allow_html=True)
        c4.markdown(mc("All-time P&L",inr(pt),"",pc), unsafe_allow_html=True)
        if snap_d["milestone"]: st.success("🎉 Profit milestone! Funds locked for protection.")
    else:
        st.markdown(f'<div class="wb">⚠️ No exchange sync yet. Configured: <strong>₹{float(starting):,.2f}</strong> — NOT real exchange balance. Click Sync to fetch.</div>', unsafe_allow_html=True)

    col1,_=st.columns([1,3])
    with col1:
        if st.button("🔄 Sync Balance Now", use_container_width=True):
            with st.spinner("Fetching from Delta Exchange…"):
                from backend.services.fund_manager import take_fund_snapshot
                try:
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
        } for r in reports]), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "🔔 Alerts":
    st.title("Alerts")
    with get_session() as db:
        _alerts = db.execute(select(Alert).order_by(desc(Alert.created_at)).limit(50)).scalars().all()
        alerts = [{
            "id":a.id, "level":a.level.value.upper(), "cat":a.category,
            "msg":a.message[:100], "resolved":a.resolved, "at":a.created_at,
        } for a in _alerts]
    unresolved = [a for a in alerts if not a["resolved"]]
    if unresolved: st.warning(f"{len(unresolved)} unresolved alert(s)")
    else: st.success("All clear — no active alerts")
    if alerts:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "ID":a["id"],"Level":a["level"],"Category":a["cat"],
            "Message":a["msg"],"Resolved":"✓" if a["resolved"] else "—","Time":ago(a["at"]),
        } for a in alerts]), use_container_width=True, hide_index=True)
        if unresolved:
            first_id = unresolved[0]["id"]
            aid=st.number_input("Alert ID to resolve", min_value=1, step=1, value=first_id)
            if st.button("Mark Resolved"):
                with get_session() as db:
                    a=db.execute(select(Alert).where(Alert.id==int(aid))).scalar_one_or_none()
                    if a:
                        a.resolved=True; a.resolved_at=datetime.now(timezone.utc); db.commit()
                        st.success("Resolved"); st.rerun()
    else:
        st.info("No alerts yet.")
