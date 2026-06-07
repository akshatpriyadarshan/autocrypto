"""Alert system — sync email for WARNING/CRITICAL alerts."""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from loguru import logger
from backend.db.database import get_session
from backend.models.db_models import Alert, AlertLevel
from backend.config.config_manager import get_config
from sqlalchemy import select


def process_pending_alerts():
    with get_session() as db:
        alerts = db.execute(select(Alert).where(
            Alert.notified==False,
            Alert.level.in_([AlertLevel.CRITICAL, AlertLevel.WARNING])
        ).order_by(Alert.created_at.asc()).limit(10)).scalars().all()
        for alert in alerts:
            _email_alert(db, alert)
            alert.notified = True
        db.commit()


def _email_alert(db, alert: Alert):
    try:
        host=get_config(db,"smtp_host") or ""; port=int(get_config(db,"smtp_port") or "587")
        user=get_config(db,"smtp_user") or ""; pwd=get_config(db,"smtp_password") or ""
        tls=(get_config(db,"smtp_use_tls") or "true")=="true"
        to=get_config(db,"email_address") or ""
        if not all([host,user,pwd,to]): return
        color="#fc8181" if alert.level==AlertLevel.CRITICAL else "#f6ad55"
        msg=MIMEMultipart("alternative")
        msg["Subject"]=f"[AutoCrypto {alert.level.value.upper()}] {alert.message[:60]}"
        msg["From"]=user; msg["To"]=to
        html=f"""<div style="background:#0a0e1a;padding:24px;font-family:Arial">
<div style="background:#141c2e;border:1px solid {color}44;border-radius:10px;padding:20px;color:#e2e8f0">
<div style="color:{color};font-size:12px;text-transform:uppercase">[{alert.level.value.upper()}] AutoCrypto</div>
<div style="margin:8px 0;font-size:16px;font-weight:600">{alert.category}</div>
<div style="color:#a0aec0">{alert.message}</div></div></div>"""
        msg.attach(MIMEText(html,"html"))
        if tls:
            server=smtplib.SMTP(host,port); server.starttls()
        else:
            server=smtplib.SMTP_SSL(host,port)
        server.login(user,pwd); server.send_message(msg); server.quit()
    except Exception as e:
        logger.error(f"Alert email: {e}")
