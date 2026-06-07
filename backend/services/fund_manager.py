"""Fund manager — sync DB, real balance from exchange, 25% lock rule."""
from decimal import Decimal
from datetime import datetime, timezone
from loguru import logger
from backend.db.database import get_session
from backend.models.db_models import FundSnapshot, Trade, TradeStatus, Alert, AlertLevel
from backend.config.config_manager import get_config
from sqlalchemy import select, func


def take_fund_snapshot() -> dict:
    with get_session() as db:
        starting = float(get_config(db, "starting_capital") or "0")
        lock_thr = float(get_config(db, "profit_lock_threshold") or "100")
        lock_pct = float(get_config(db, "profit_lock_pct") or "25")

        total = _exchange_balance(db)
        if total is None:
            total = _estimate_from_trades(db, starting)

        open_rows = db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN)).scalars().all()
        in_trades = sum(float(t.quantity) * float(t.entry_price or 0) for t in open_rows)

        prev = _latest_snapshot(db)
        locked = float(prev.locked_25pct) if prev else 0.0
        available = max(0.0, total - in_trades - locked)
        pnl_total = total + locked - starting
        today_pnl = _today_pnl(db)

        milestone = False
        if starting > 0:
            profit_pct = ((total + locked - starting) / starting) * 100
            if profit_pct >= lock_thr:
                lvl = int(profit_pct // lock_thr)
                last = _milestone_count(db)
                if lvl > last:
                    lock_amt = total * (lock_pct / 100)
                    locked += lock_amt
                    available = max(0.0, total - in_trades - locked)
                    milestone = True
                    db.add(Alert(level=AlertLevel.INFO, category="milestone",
                        message=f"Milestone! {profit_pct:.1f}% profit. Locked ₹{lock_amt:,.2f}. Trading with ₹{available:,.2f}."))
                    logger.info(f"MILESTONE: locked ₹{lock_amt:,.2f}")

        snap = FundSnapshot(
            total_balance=Decimal(str(round(total,2))),
            available=Decimal(str(round(available,2))),
            locked_25pct=Decimal(str(round(locked,2))),
            in_trades=Decimal(str(round(in_trades,2))),
            starting_fund=Decimal(str(round(starting,2))),
            pnl_today=Decimal(str(round(today_pnl,2))),
            pnl_total=Decimal(str(round(pnl_total,2))),
            milestone_hit=milestone,
        )
        db.add(snap)
        db.commit()
        return {"total":total,"available":available,"locked":locked,"pnl_total":pnl_total,"milestone":milestone}


def get_available_fund() -> float:
    with get_session() as db:
        snap = _latest_snapshot(db)
        if snap: return float(snap.available)
        return float(get_config(db, "starting_capital") or "0")


def _exchange_balance(db) -> float | None:
    try:
        if get_config(db, "setup_complete") != "true": return None
        from backend.services.trade_executor import get_wallet_balance_sync
        return get_wallet_balance_sync()
    except Exception: return None

def _estimate_from_trades(db, starting: float) -> float:
    r = db.execute(select(func.sum(Trade.pnl)).where(
        Trade.status == TradeStatus.CLOSED, Trade.pnl.isnot(None)))
    return starting + float(r.scalar() or 0)

def _today_pnl(db) -> float:
    today = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    r = db.execute(select(func.sum(Trade.pnl)).where(
        Trade.status==TradeStatus.CLOSED, Trade.pnl.isnot(None)))
    return float(r.scalar() or 0)

def _latest_snapshot(db) -> FundSnapshot | None:
    return db.execute(select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1)).scalar_one_or_none()

def _milestone_count(db) -> int:
    return int(db.execute(select(func.count(FundSnapshot.id)).where(FundSnapshot.milestone_hit==True)).scalar() or 0)
