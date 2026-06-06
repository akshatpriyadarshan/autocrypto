"""Background scheduler — signal engine, risk, alerts, reports."""
import asyncio, time
from datetime import datetime, timezone, timedelta
from loguru import logger

_tasks = []
CANDLE_SECONDS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}


async def start_all():
    logger.info("Starting schedulers…")
    _tasks.clear()
    _tasks.append(asyncio.create_task(_engine_loop(),   name="engine"))
    _tasks.append(asyncio.create_task(_risk_loop(),     name="risk"))
    _tasks.append(asyncio.create_task(_alert_loop(),    name="alerts"))
    _tasks.append(asyncio.create_task(_snap_loop(),     name="snap"))
    _tasks.append(asyncio.create_task(_report_loop(),   name="report"))
    logger.info(f"{len(_tasks)} tasks started")

async def stop_all():
    for t in _tasks: t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    logger.info("Schedulers stopped")

async def _engine_loop():
    # Run once immediately on start (so signals aren't delayed by full interval)
    await asyncio.sleep(5)  # brief startup delay
    while True:
        try:
            if await _active():
                from backend.services.signal_engine import run_signal_engine
                await run_signal_engine()
            iv   = await _interval()
            wait = CANDLE_SECONDS.get(iv, 900) - (time.time() % CANDLE_SECONDS.get(iv, 900))
            logger.info(f"Engine: next in {wait:.0f}s")
            await asyncio.sleep(wait)
        except asyncio.CancelledError: break
        except Exception as e:
            logger.error(f"Engine loop: {e}", exc_info=True)
            await asyncio.sleep(60)

async def _risk_loop():
    while True:
        try:
            if await _active():
                from backend.services.risk_manager import run_risk_checks
                await run_risk_checks()
        except asyncio.CancelledError: break
        except Exception as e: logger.error(f"Risk: {e}")
        await asyncio.sleep(30)

async def _alert_loop():
    while True:
        try:
            from backend.services.alert_system import process_pending_alerts
            await process_pending_alerts()
        except asyncio.CancelledError: break
        except Exception as e: logger.error(f"Alerts: {e}")
        await asyncio.sleep(60)

async def _snap_loop():
    while True:
        try:
            await asyncio.sleep(3600)
            if await _active():
                from backend.services.fund_manager import take_fund_snapshot
                await take_fund_snapshot()
        except asyncio.CancelledError: break
        except Exception as e: logger.error(f"Snap: {e}")

async def _report_loop():
    while True:
        try:
            now = datetime.now(timezone.utc)
            nxt = now.replace(hour=14,minute=30,second=0,microsecond=0)
            if nxt <= now: nxt += timedelta(days=1)
            await asyncio.sleep((nxt-now).total_seconds())
            from backend.services.daily_reporter import send_daily_report
            await send_daily_report()
        except asyncio.CancelledError: break
        except Exception as e:
            logger.error(f"Report: {e}")
            await asyncio.sleep(3600)

async def _active() -> bool:
    try:
        from backend.db.database import AsyncSessionLocal
        from backend.config.config_manager import get_config
        async with AsyncSessionLocal() as db:
            return await get_config(db,"bot_active") == "true"
    except Exception: return False

async def _interval() -> str:
    try:
        from backend.db.database import AsyncSessionLocal
        from backend.config.config_manager import get_config
        async with AsyncSessionLocal() as db:
            return await get_config(db,"candle_interval") or "15m"
    except Exception: return "15m"
