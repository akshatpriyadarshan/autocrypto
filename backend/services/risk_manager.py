"""Risk manager — sync stop-loss monitoring + drawdown halt."""
from decimal import Decimal
from datetime import datetime, timezone
from loguru import logger
from backend.db.database import get_session
from backend.models.db_models import Trade, TradeStatus, TradeDirection, Alert, AlertLevel, FundSnapshot
from backend.config.config_manager import get_config, set_config
from sqlalchemy import select


def run_risk_checks():
    _check_open_trades()
    _check_drawdown()


def _check_open_trades():
    with get_session() as db:
        trades = db.execute(select(Trade).where(Trade.status==TradeStatus.OPEN)).scalars().all()
        for trade in trades:
            _eval_trade(db, trade)
        # committed by get_session() on exit


def _eval_trade(db, trade: Trade):
    from backend.services.trade_executor import get_market_price_sync
    price = get_market_price_sync(str(trade.pair))
    if not price: return
    entry=float(trade.entry_price or 0); sl=float(trade.stop_loss_price or 0)
    if entry<=0 or sl<=0: return
    breached = (trade.direction==TradeDirection.BUY and price<=sl) or \
               (trade.direction==TradeDirection.SELL and price>=sl)
    if breached:
        logger.warning(f"SL breach trade={trade.id} {trade.pair} price={price} sl={sl}")
        try:
            from backend.services.trade_executor import close_trade_market
            close_trade_market(trade.id, price, reason="stop_loss")
        except Exception as e:
            logger.error(f"SL close {trade.id}: {e}")


def _check_drawdown():
    with get_session() as db:
        max_dd = float(get_config(db,"max_drawdown_pct") or "15")
        today  = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        start_snap = db.execute(
            select(FundSnapshot).where(FundSnapshot.snapshot_at>=today)
            .order_by(FundSnapshot.snapshot_at.asc()).limit(1)
        ).scalar_one_or_none()
        latest = db.execute(
            select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1)
        ).scalar_one_or_none()
        if not start_snap or not latest: return
        sf=float(start_snap.total_balance); cf=float(latest.total_balance)
        if sf<=0: return
        dd = ((sf-cf)/sf)*100
        if dd >= max_dd:
            logger.critical(f"DRAWDOWN {dd:.1f}% >= {max_dd}% — stopping bot")
            set_config(db, "bot_active", "false")
            db.add(Alert(level=AlertLevel.CRITICAL, category="drawdown",
                message=f"Bot stopped: {dd:.1f}% drawdown (max {max_dd}%). Fund: ₹{cf:,.2f}"))
            # committed by get_session() on exit
