"""Scheduler — background thread, sync, no asyncio."""
import threading, time
from datetime import datetime, timezone
from loguru import logger

_thread: threading.Thread = None
_stop_event = threading.Event()
CANDLE_SECS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}


def start_all():
    global _thread, _stop_event
    _stop_event.clear()
    _thread = threading.Thread(target=_main_loop, daemon=True, name="autocrypto")
    _thread.start()
    logger.info("Scheduler started")


def stop_all():
    _stop_event.set()


def _main_loop():
    # Agent 3 fix: initialise last_engine to now so engine doesn't fire
    # immediately AND doesn't fire again every loop because last_engine==0
    now = time.time()
    last_engine = now   # will fire after one full period
    last_risk   = now
    last_alert  = now
    last_snap   = now
    last_report = 0.0

    # Brief startup delay for DB init
    time.sleep(8)

    # Run engine ONCE immediately on start so user sees signals quickly
    if _is_active():
        try:
            from backend.services.signal_engine import run_signal_engine
            run_signal_engine()
        except Exception as e:
            logger.error(f"Engine startup: {e}")
    last_engine = time.time()

    while not _stop_event.is_set():
        now      = time.time()
        interval = _get_interval()
        period   = CANDLE_SECS.get(interval, 900)

        # Signal engine — every full candle period
        if (now - last_engine) >= period:
            if _is_active():
                try:
                    from backend.services.signal_engine import run_signal_engine
                    run_signal_engine()
                except Exception as e:
                    logger.error(f"Engine: {e}")
            last_engine = now  # always update to prevent tight loop

        # Risk checks every 30s
        if (now - last_risk) >= 30:
            if _is_active():
                try:
                    from backend.services.risk_manager import run_risk_checks
                    run_risk_checks()
                except Exception as e:
                    logger.error(f"Risk: {e}")
            last_risk = now

        # Alert monitor every 60s
        if (now - last_alert) >= 60:
            try:
                from backend.services.alert_system import process_pending_alerts
                process_pending_alerts()
            except Exception as e:
                logger.error(f"Alerts: {e}")
            last_alert = now

        # Fund snapshot every hour
        if (now - last_snap) >= 3600:
            if _is_active():
                try:
                    from backend.services.fund_manager import take_fund_snapshot
                    take_fund_snapshot()
                except Exception as e:
                    logger.error(f"Snapshot: {e}")
            last_snap = now

        # Daily report at 14:30 UTC
        dt = datetime.now(timezone.utc)
        if dt.hour == 14 and dt.minute == 30 and (now - last_report) >= 3600:
            try:
                from backend.services.daily_reporter import send_daily_report
                send_daily_report()
            except Exception as e:
                logger.error(f"Report: {e}")
            last_report = now

        time.sleep(10)


def _is_active() -> bool:
    try:
        from backend.db.database import get_session
        from backend.config.config_manager import get_config
        with get_session() as db:
            return get_config(db, "bot_active") == "true"
    except Exception:
        return False


def _get_interval() -> str:
    try:
        from backend.db.database import get_session
        from backend.config.config_manager import get_config
        with get_session() as db:
            return get_config(db, "candle_interval") or "15m"
    except Exception:
        return "15m"
