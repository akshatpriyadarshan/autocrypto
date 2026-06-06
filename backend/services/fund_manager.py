"""Fund manager — real balance from exchange, 25% lock rule."""
from decimal import Decimal
from datetime import datetime, timezone
from loguru import logger
from backend.db.database import AsyncSessionLocal
from backend.models.db_models import FundSnapshot, Trade, TradeStatus, Alert, AlertLevel
from backend.config.config_manager import get_config
from sqlalchemy import select, func


async def take_fund_snapshot() -> dict:
    async with AsyncSessionLocal() as db:
        starting = float(await get_config(db, "starting_capital") or "0")
        lock_thr = float(await get_config(db, "profit_lock_threshold") or "100")
        lock_pct = float(await get_config(db, "profit_lock_pct") or "25")

        # Real balance — None if exchange unreachable
        total = await _exchange_balance()

        # If exchange unreachable, estimate from trades
        if total is None:
            total = await _estimate_from_trades(db, starting)
            logger.warning("Using estimated balance (exchange unreachable)")

        # Open trades value
        r = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
        open_trades = r.scalars().all()
        in_trades = sum(float(t.quantity) * float(t.entry_price or 0) for t in open_trades)

        # Previous locked
        prev = await _latest_snapshot(db)
        locked = float(prev.locked_25pct) if prev else 0.0
        available = max(0.0, total - in_trades - locked)
        pnl_total = total + locked - starting
        today_pnl = await _today_pnl(db)

        # Milestone check
        milestone = False
        if starting > 0:
            profit_pct = ((total + locked - starting) / starting) * 100
            if profit_pct >= lock_thr:
                lvl = int(profit_pct // lock_thr)
                last = await _milestone_count(db)
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
        await db.commit()
        return {"total":total,"available":available,"locked":locked,"pnl_total":pnl_total,"milestone":milestone}


async def get_available_fund() -> float:
    async with AsyncSessionLocal() as db:
        snap = await _latest_snapshot(db)
        if snap: return float(snap.available)
        return float(await get_config(db, "starting_capital") or "0")


async def _exchange_balance() -> float | None:
    try:
        async with AsyncSessionLocal() as db:
            if await get_config(db, "setup_complete") != "true": return None
        from backend.services.trade_executor import get_wallet_balance
        return await get_wallet_balance()
    except Exception:
        return None

async def _estimate_from_trades(db, starting: float) -> float:
    r = await db.execute(select(func.sum(Trade.pnl)).where(
        Trade.status == TradeStatus.CLOSED, Trade.pnl.isnot(None)))
    return starting + float(r.scalar() or 0)

async def _today_pnl(db) -> float:
    today = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    r = await db.execute(select(func.sum(Trade.pnl)).where(
        Trade.status==TradeStatus.CLOSED, Trade.closed_at>=today, Trade.pnl.isnot(None)))
    return float(r.scalar() or 0)

async def _latest_snapshot(db) -> FundSnapshot | None:
    r = await db.execute(select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1))
    return r.scalar_one_or_none()

async def _milestone_count(db) -> int:
    r = await db.execute(select(func.count(FundSnapshot.id)).where(FundSnapshot.milestone_hit==True))
    return int(r.scalar() or 0)
