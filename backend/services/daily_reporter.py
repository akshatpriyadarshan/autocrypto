"""Daily email report — sync."""
import smtplib
from decimal import Decimal
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from loguru import logger
from backend.db.database import get_session
from backend.models.db_models import Trade, TradeStatus, DailyReport, FundSnapshot
from backend.config.config_manager import get_config
from sqlalchemy import select, func


def send_daily_report():
    with get_session() as db:
        try:
            data = _build(db)
            html = _render(data)
            sent = _send(db, f"AutoCrypto Daily — {data['date']} | P&L: ₹{data['pnl_day']:+,.2f}", html)
            db.add(DailyReport(
                report_date=datetime.now(timezone.utc),
                starting_fund=Decimal(str(data["start"])),
                ending_fund=Decimal(str(data["end"])),
                locked_fund=Decimal(str(data["locked"])),
                trades_count=data["total"], winning_trades=data["won"],
                losing_trades=data["lost"],
                pnl_day=Decimal(str(data["pnl_day"])),
                pnl_total=Decimal(str(data["pnl_total"])),
                email_sent=sent,
            ))
            # committed by get_session() on exit
        except Exception as e:
            logger.error(f"Daily report: {e}", exc_info=True)


def _build(db) -> dict:
    today = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
    trades = db.execute(select(Trade).where(Trade.status==TradeStatus.CLOSED, Trade.closed_at>=today)).scalars().all()
    won=[t for t in trades if t.pnl and float(t.pnl)>0]
    pnl=sum(float(t.pnl or 0) for t in trades)
    snap=db.execute(select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1)).scalar_one_or_none()
    starting=float(get_config(db,"starting_capital") or "0")
    end=float(snap.total_balance) if snap else starting
    locked=float(snap.locked_25pct) if snap else 0.0
    ds=db.execute(select(FundSnapshot).where(FundSnapshot.snapshot_at>=today).order_by(FundSnapshot.snapshot_at.asc()).limit(1)).scalar_one_or_none()
    return {
        "date": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "start": float(ds.total_balance) if ds else starting,
        "end": end, "locked": locked,
        "pnl_day": round(pnl,2), "pnl_total": round(end+locked-starting,2),
        "total": len(trades), "won": len(won), "lost": len(trades)-len(won),
        "win_rate": round(len(won)/len(trades)*100 if trades else 0,1),
        "rows": [{"pair":t.pair,"dir":t.direction.value.upper(),
                  "entry":float(t.entry_price or 0),"exit":float(t.exit_price or 0),
                  "pnl":float(t.pnl or 0)} for t in trades]
    }


def _render(d: dict) -> str:
    c="#48bb78" if d["pnl_day"]>=0 else "#fc8181"
    rows="".join(
        f"<tr><td>{r['pair']}</td><td style='color:{'#48bb78' if r['dir']=='BUY' else '#fc8181'}'>{r['dir']}</td>"
        f"<td>₹{r['entry']:,.2f}</td><td>₹{r['exit']:,.2f}</td>"
        f"<td style='color:{'#48bb78' if r['pnl']>=0 else '#fc8181'}'>{'+' if r['pnl']>=0 else ''}₹{r['pnl']:,.2f}</td></tr>"
        for r in d["rows"]
    ) or "<tr><td colspan='5' style='text-align:center;color:#718096'>No trades today</td></tr>"
    return f"""<html><body style="background:#0a0e1a;font-family:Arial;padding:24px;color:#e2e8f0">
<div style="max-width:600px;margin:0 auto">
<div style="background:#141c2e;border-radius:12px;padding:20px;margin-bottom:16px">
  <div style="font-family:monospace;color:#63b3ed">AutoCrypto Trader</div>
  <h2 style="margin:8px 0;color:#e2e8f0">Daily Report — {d['date']}</h2>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
  <div style="background:#141c2e;border-radius:10px;padding:16px">
    <div style="font-size:11px;color:#718096;text-transform:uppercase">Today P&L</div>
    <div style="font-size:24px;font-weight:700;color:{c}">{'+' if d['pnl_day']>=0 else ''}₹{d['pnl_day']:,.2f}</div></div>
  <div style="background:#141c2e;border-radius:10px;padding:16px">
    <div style="font-size:11px;color:#718096;text-transform:uppercase">Total Fund</div>
    <div style="font-size:24px;font-weight:700;color:#63b3ed">₹{d['end']:,.2f}</div></div>
  <div style="background:#141c2e;border-radius:10px;padding:16px">
    <div style="font-size:11px;color:#718096;text-transform:uppercase">Locked</div>
    <div style="font-size:24px;font-weight:700;color:#48bb78">₹{d['locked']:,.2f}</div></div>
  <div style="background:#141c2e;border-radius:10px;padding:16px">
    <div style="font-size:11px;color:#718096;text-transform:uppercase">Win Rate</div>
    <div style="font-size:24px;font-weight:700;color:#f6ad55">{d['win_rate']}%</div>
    <div style="font-size:12px;color:#718096">{d['won']}W/{d['lost']}L</div></div>
</div>
<div style="background:#141c2e;border-radius:10px;overflow:hidden">
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:rgba(255,255,255,0.03)">
      <th style="padding:8px;text-align:left;color:#718096">Pair</th>
      <th style="padding:8px;text-align:left;color:#718096">Dir</th>
      <th style="padding:8px;text-align:left;color:#718096">Entry</th>
      <th style="padding:8px;text-align:left;color:#718096">Exit</th>
      <th style="padding:8px;text-align:left;color:#718096">P&L</th></tr>
    {rows}
  </table>
</div></div></body></html>"""


def _send(db, subject: str, html: str) -> bool:
    try:
        host=get_config(db,"smtp_host") or ""; port=int(get_config(db,"smtp_port") or "587")
        user=get_config(db,"smtp_user") or ""; pwd=get_config(db,"smtp_password") or ""
        tls=(get_config(db,"smtp_use_tls") or "true")=="true"
        to=get_config(db,"email_address") or ""
        if not all([host,user,pwd,to]): return False
        msg=MIMEMultipart("alternative")
        msg["Subject"]=subject; msg["From"]=user; msg["To"]=to
        msg.attach(MIMEText(html,"html"))
        if tls:
            server=smtplib.SMTP(host,port); server.starttls()
        else:
            server=smtplib.SMTP_SSL(host,port)
        server.login(user,pwd); server.send_message(msg); server.quit()
        return True
    except Exception as e:
        logger.error(f"Report email: {e}")
        return False
