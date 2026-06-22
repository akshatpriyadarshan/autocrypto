"""
Fund manager — real balance from Delta Exchange India.
Falls back to direct REST API call if ccxt fails.
"""
from decimal import Decimal
from datetime import datetime, timezone
from loguru import logger
from backend.db.database import get_session
from backend.models.db_models import FundSnapshot, Trade, TradeStatus, Alert, AlertLevel
import os
from backend.config.config_manager import get_config
from sqlalchemy import select, func

# Delta India REST endpoint (direct — no ccxt needed for read-only balance)
DELTA_INDIAN_API = "https://api.india.delta.exchange/v2"
DELTA_API_KEY    = "76wEBRrPbx64EUzphk43LIX1kCWrFb"
DELTA_API_SECRET = "3lJghi3DLRdgeoesLYxfBg5l9jH4Q0HEjLMOkN744dp9dOH4ddiHG6Mv09cH"


def _get_delta_auth() -> tuple[str, str, bool]:
    with get_session() as db:
        api_key = os.getenv("DELTA_API_KEY") or get_config(db, "delta_api_key") or DELTA_API_KEY
        api_secret = os.getenv("DELTA_API_SECRET") or get_config(db, "delta_api_secret") or DELTA_API_SECRET
        if os.getenv("DELTA_TESTNET") is not None:
            testnet = os.getenv("DELTA_TESTNET").lower() in ("1", "true", "yes", "y")
        else:
            testnet = get_config(db, "delta_testnet") == "true"
    return api_key, api_secret, testnet


def _delta_api_url(testnet: bool = False) -> str:
    return "https://cdn-ind.testnet.deltaex.org" if testnet else "https://api.india.delta.exchange"


def take_fund_snapshot() -> dict:
    with get_session() as db:
        _cap_raw = float(get_config(db, "starting_capital") or "0")
        # starting_capital stored in INR by user — convert to USD for internal comparison
        # Delta India fixed rate: 1 USD = 85 INR
        USD_TO_INR = 85.0
        starting = _cap_raw / USD_TO_INR  # USD equivalent
        lock_thr = float(get_config(db, "profit_lock_threshold") or "100")
        lock_pct = float(get_config(db, "profit_lock_pct") or "25")

        total = _get_balance()
        if total is None:
            total = _estimate_from_trades(db, starting)
            logger.warning("Using estimated balance (exchange unreachable)")

        open_rows = db.execute(
            select(Trade).where(Trade.status == TradeStatus.OPEN)
        ).scalars().all()
        in_trades = sum(
            float(t.quantity) * float(t.entry_price or 0) for t in open_rows
        )

        prev   = _latest_snapshot(db)
        locked = float(prev.locked_25pct) if prev else 0.0
        available = max(0.0, total - in_trades - locked)
        pnl_total = total + locked - starting
        today_pnl = _today_pnl(db)

        milestone = False
        if starting > 0:
            profit_pct = ((total + locked - starting) / starting) * 100
            if profit_pct >= lock_thr:
                lvl  = int(profit_pct // lock_thr)
                last = _milestone_count(db)
                if lvl > last:
                    lock_amt  = total * (lock_pct / 100)
                    locked   += lock_amt
                    available = max(0.0, total - in_trades - locked)
                    milestone = True
                    db.add(Alert(
                        level=AlertLevel.INFO, category="milestone",
                        message=(f"Milestone! {profit_pct:.1f}% profit. "
                                 f"Locked ₹{lock_amt:,.2f}. "
                                 f"Trading with ₹{available:,.2f}.")
                    ))
                    logger.info(f"MILESTONE: locked ₹{lock_amt:,.2f}")

        db.add(FundSnapshot(
            total_balance = Decimal(str(round(total, 2))),
            available     = Decimal(str(round(available, 2))),
            locked_25pct  = Decimal(str(round(locked, 2))),
            in_trades     = Decimal(str(round(in_trades, 2))),
            starting_fund = Decimal(str(round(starting, 2))),
            pnl_today     = Decimal(str(round(today_pnl, 2))),
            pnl_total     = Decimal(str(round(pnl_total, 2))),
            milestone_hit = milestone,
        ))
        return {
            "total": total, "available": available,
            "locked": locked, "pnl_total": pnl_total,
            "milestone": milestone,
        }


def get_available_fund() -> float:
    with get_session() as db:
        snap = _latest_snapshot(db)
        if snap:
            return float(snap.available)
        return float(get_config(db, "starting_capital") or "0")


def _get_balance() -> float | None:
    """
    Try ccxt deltaindia first, then direct REST, then return None.
    """
    # Try ccxt
    try:
        from backend.services.trade_executor import get_wallet_balance_sync
        result = get_wallet_balance_sync()
        if result is not None:
            logger.info(f"Balance from ccxt: ₹{result:,.2f} USDT")
            return result
    except Exception as e:
        logger.warning(f"ccxt balance failed: {e}")

    # Try direct REST API (Delta India v2)
    try:
        import hashlib, hmac, time, httpx
        ts     = str(int(time.time()))
        method = "GET"
        path   = "/v2/wallet/balances"
        api_key, api_secret, testnet = _get_delta_auth()
        sig = hmac.new(
            api_secret.encode(),
            (method + ts + path).encode(),
            hashlib.sha256
        ).hexdigest()
        headers = {
            "api-key":    api_key,
            "timestamp":  ts,
            "signature":  sig,
            "User-Agent": "autocrypto-trader/1.0",
            "Accept":     "application/json",
        }
        r = httpx.get(
            f"{_delta_api_url(testnet)}{path}",
            headers=headers,
            timeout=10.0
        )
        r.raise_for_status()
        data = r.json()
        if data.get("success") and data.get("result"):
            for asset in data["result"]:
                if asset.get("asset_symbol") in ("USDT", "USD"):
                    bal = float(asset.get("balance") or asset.get("available_balance") or 0)
                    logger.info(f"Balance from REST: ₹{bal:,.2f} USDT")
                    return bal
    except Exception as e:
        logger.warning(f"Direct REST balance failed: {e}")

    return None


def _estimate_from_trades(db, starting: float) -> float:
    r = db.execute(
        select(func.sum(Trade.pnl)).where(
            Trade.status == TradeStatus.CLOSED,
            Trade.pnl.isnot(None)
        )
    )
    return starting + float(r.scalar() or 0)


def _today_pnl(db) -> float:
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    r = db.execute(
        select(func.sum(Trade.pnl)).where(
            Trade.status == TradeStatus.CLOSED,
            Trade.pnl.isnot(None),
        )
    )
    return float(r.scalar() or 0)


def _latest_snapshot(db) -> FundSnapshot | None:
    return db.execute(
        select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1)
    ).scalar_one_or_none()


def _milestone_count(db) -> int:
    return int(
        db.execute(
            select(func.count(FundSnapshot.id)).where(FundSnapshot.milestone_hit == True)
        ).scalar() or 0
    )
