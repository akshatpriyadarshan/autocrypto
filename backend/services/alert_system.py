"""Alert system — emails WARNING/CRITICAL alerts."""
from loguru import logger
from backend.db.database import AsyncSessionLocal
from backend.models.db_models import Alert, AlertLevel
from backend.config.config_manager import get_config
from sqlalchemy import select


async def process_pending_alerts():
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Alert).where(
            Alert.notified==False,
            Alert.level.in_([AlertLevel.CRITICAL, AlertLevel.WARNING])
        ).order_by(Alert.created_at.asc()).limit(10))
        for alert in r.scalars().all():
            await _email_alert(db, alert)
            alert.notified = True
        await db.commit()


async def create_alert(category: str, message: str, level: AlertLevel = AlertLevel.WARNING):
    async with AsyncSessionLocal() as db:
        db.add(Alert(level=level, category=category, message=message))
        await db.commit()


async def _email_alert(db, alert: Alert) -> bool:
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        host  = await get_config(db,"smtp_host") or ""
        port  = int(await get_config(db,"smtp_port") or "587")
        user  = await get_config(db,"smtp_user") or ""
        pwd   = await get_config(db,"smtp_password") or ""
        tls   = (await get_config(db,"smtp_use_tls") or "true") == "true"
        to    = await get_config(db,"email_address") or ""
        if not all([host,user,pwd,to]): return False
        color = "#fc8181" if alert.level==AlertLevel.CRITICAL else "#f6ad55"
        html  = f"""<div style="background:#0a0e1a;padding:24px;font-family:Arial">
<div style="background:#141c2e;border:1px solid {color}44;border-radius:10px;padding:20px;color:#e2e8f0">
<div style="color:{color};font-size:12px;text-transform:uppercase">[{alert.level.value.upper()}] AutoCrypto Alert</div>
<div style="font-size:16px;font-weight:600;margin:8px 0">{alert.category}</div>
<div style="color:#a0aec0">{alert.message}</div>
</div></div>"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AutoCrypto {alert.level.value.upper()}] {alert.message[:60]}"
        msg["From"] = user; msg["To"] = to
        msg.attach(MIMEText(html,"html"))
        await aiosmtplib.send(msg, hostname=host, port=port, username=user,
                               password=pwd, use_tls=tls, start_tls=not tls)
        return True
    except Exception as e:
        logger.error(f"Alert email failed: {e}")
        return False
