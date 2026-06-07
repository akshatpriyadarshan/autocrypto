"""Scheduler — background thread, sync functions, no asyncio."""
import threading, time
from datetime import datetime, timezone, timedelta
from loguru import logger

_thread: threading.Thread = None
_stop_event = threading.Event()
CANDLE_SECS = {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}


def start_all():
    global _thread, _stop_event
    _stop_event.clear()
    _thread = threading.Thread(target=_main_loop, daemon=True, name="autocrypto_scheduler")
    _thread.start()
    logger.info("Scheduler started (sync thread)")


def stop_all():
    _stop_event.set()
    logger.info("Scheduler stop requested")


def _main_loop():
    last_engine  = 0.0
    last_risk    = 0.0
    last_alert   = 0.0
    last_snap    = 0.0
    last_report  = 0.0

    # Run engine once immediately on start (5s delay for DB init)
    time.sleep(5)

    while not _stop_event.is_set():
        now = time.time()

        # Signal engine — aligned to candle close
        interval = _get_interval()
        period   = CANDLE_SECS.get(interval, 900)
        engine_due = (now - last_engine) >= period or last_engine == 0
        if engine_due and _is_active():
            try:
                from backend.services.signal_engine import run_signal_engine
                run_signal_engine()
                last_engine = now
            except Exception as e:
                logger.error(f"Engine: {e}")

        # Risk checks every 30s
        if (now - last_risk) >= 30 and _is_active():
            try:
                from backend.services.risk_manager import run_risk_checks
                run_risk_checks()
                last_risk = now
            except Exception as e:
                logger.error(f"Risk: {e}")

        # Alert monitor every 60s
        if (now - last_alert) >= 60:
            try:
                from backend.services.alert_system import process_pending_alerts
                process_pending_alerts()
                last_alert = now
            except Exception as e:
                logger.error(f"Alerts: {e}")

        # Fund snapshot every hour
        if (now - last_snap) >= 3600 and _is_active():
            try:
                from backend.services.fund_manager import take_fund_snapshot
                take_fund_snapshot()
                last_snap = now
            except Exception as e:
                logger.error(f"Snapshot: {e}")

        # Daily report at 14:30 UTC (8 PM IST)
        dt = datetime.now(timezone.utc)
        report_due = (dt.hour == 14 and dt.minute == 30 and
                      (now - last_report) >= 3600)
        if report_due:
            try:
                from backend.services.daily_reporter import send_daily_report
                send_daily_report()
                last_report = now
            except Exception as e:
                logger.error(f"Report: {e}")

        time.sleep(10)  # poll every 10s


def _is_active() -> bool:
    try:
        from backend.db.database import get_session
        from backend.config.config_manager import get_config
        with get_session() as db:
            return get_config(db, "bot_active") == "true"
    except Exception: return False


def _get_interval() -> str:
    try:
        from backend.db.database import get_session
        from backend.config.config_manager import get_config
        with get_session() as db:
            return get_config(db, "candle_interval") or "15m"
    except Exception: return "15m"
