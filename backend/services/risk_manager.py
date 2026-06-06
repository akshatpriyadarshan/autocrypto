"""Risk manager — stop-loss monitoring + drawdown halt."""
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from loguru import logger
from backend.db.database import AsyncSessionLocal
from backend.models.db_models import Trade, TradeStatus, TradeDirection, Alert, AlertLevel, FundSnapshot
from backend.config.config_manager import get_config
from sqlalchemy import select, and_


async def run_risk_checks():
    await _check_open_trades()
    await _check_drawdown()


async def _check_open_trades():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
        for trade in r.scalars().all():
            await _eval_trade(db, trade)
        await db.commit()


async def _eval_trade(db, trade: Trade):
    from backend.services.market_data import fetch_price
    price = await fetch_price(str(trade.pair))
    if not price: return
    entry = float(trade.entry_price or 0); sl = float(trade.stop_loss_price or 0)
    if entry <= 0 or sl <= 0: return
    breached = (trade.direction == TradeDirection.BUY and price <= sl) or \
               (trade.direction == TradeDirection.SELL and price >= sl)
    if breached:
        logger.warning(f"SL breach trade={trade.id} {trade.pair} price={price} sl={sl}")
        qty   = float(trade.quantity)
        pnl   = (price - entry) * qty if trade.direction == TradeDirection.BUY else (entry - price) * qty
        trade.status    = TradeStatus.CLOSED
        trade.exit_price = Decimal(str(round(price,8)))
        trade.pnl       = Decimal(str(round(pnl,2)))
        trade.closed_at = datetime.now(timezone.utc)
        trade.notes     = (trade.notes or "") + f" | SL@{price}"
        try:
            from backend.services.trade_executor import close_trade_market
            await close_trade_market(trade.id, price, reason="stop_loss")
        except Exception as e:
            logger.error(f"SL close failed {trade.id}: {e}")


async def _check_drawdown():
    async with AsyncSessionLocal() as db:
        max_dd = float(await get_config(db, "max_drawdown_pct") or "15")
        today  = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        r1 = await db.execute(select(FundSnapshot).where(FundSnapshot.snapshot_at>=today)
                               .order_by(FundSnapshot.snapshot_at.asc()).limit(1))
        r2 = await db.execute(select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1))
        start = r1.scalar_one_or_none(); latest = r2.scalar_one_or_none()
        if not start or not latest: return
        sf = float(start.total_balance); cf = float(latest.total_balance)
        if sf <= 0: return
        dd = ((sf - cf) / sf) * 100
        if dd >= max_dd:
            logger.critical(f"DRAWDOWN {dd:.1f}% >= {max_dd}% — stopping bot")
            from backend.config.config_manager import set_config
            await set_config(db, "bot_active", "false")
            db.add(Alert(level=AlertLevel.CRITICAL, category="drawdown",
                message=f"Bot stopped: {dd:.1f}% drawdown (max {max_dd}%). Fund: ₹{cf:,.2f}"))
            await db.commit()
