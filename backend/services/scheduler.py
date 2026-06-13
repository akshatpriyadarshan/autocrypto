"""
Scheduler — background thread, pure sync.
Engine respects candle period. last_engine only updates when engine actually ran.
"""
import threading, time
from datetime import datetime, timezone
from loguru import logger

_thread     = None
_stop_event = threading.Event()

CANDLE_SECS = {
    "1m": 60, "5m": 300, "15m": 900,
    "1h": 3600, "4h": 14400, "1d": 86400
}


def start_all():
    global _thread, _stop_event
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="scheduler")
    _thread.start()
    logger.info("Scheduler started")


def stop_all():
    _stop_event.set()
    logger.info("Scheduler stopped")


def _loop():
    # Startup delay — let DB init complete
    time.sleep(10)

    last_engine = 0.0   # 0 = never ran → will fire after first full period
    last_risk   = time.time()
    last_alert  = time.time()
    last_snap   = time.time()
    last_report = 0.0

    while not _stop_event.is_set():
        now      = time.time()
        interval = _get_interval()
        period   = CANDLE_SECS.get(interval, 900)

        # ── Signal Engine ──────────────────────────────────────────────────────
        # Only fire when: bot active AND enough time has passed since last run
        if _is_active() and (now - last_engine) >= period:
            try:
                logger.info(f"Scheduler: running engine (period={period}s elapsed)")
                from backend.services.signal_engine import run_signal_engine
                run_signal_engine()
            except Exception as e:
                logger.error(f"Engine error: {e}", exc_info=True)
            finally:
                # Always update — prevents tight loop on error
                last_engine = now

        # ── Risk checks every 30s ──────────────────────────────────────────────
        if _is_active() and (now - last_risk) >= 30:
            try:
                from backend.services.risk_manager import run_risk_checks
                run_risk_checks()
            except Exception as e:
                logger.error(f"Risk error: {e}")
            last_risk = now

        # ── Alerts every 60s ──────────────────────────────────────────────────
        if (now - last_alert) >= 60:
            try:
                from backend.services.alert_system import process_pending_alerts
                process_pending_alerts()
            except Exception as e:
                logger.error(f"Alert error: {e}")
            last_alert = now

        # ── Fund snapshot every 4h ────────────────────────────────────────────
        if _is_active() and (now - last_snap) >= 14400:
            try:
                from backend.services.fund_manager import take_fund_snapshot
                take_fund_snapshot()
                logger.info("Periodic fund snapshot taken")
            except Exception as e:
                logger.error(f"Snapshot error: {e}")
            last_snap = now

        # ── Daily report at 14:30 UTC (8 PM IST) ─────────────────────────────
        dt = datetime.now(timezone.utc)
        if dt.hour == 14 and dt.minute == 30 and (now - last_report) >= 3600:
            try:
                from backend.services.daily_reporter import send_daily_report
                send_daily_report()
            except Exception as e:
                logger.error(f"Report error: {e}")
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
