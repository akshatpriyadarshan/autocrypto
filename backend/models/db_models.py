"""All DB models."""
from datetime import datetime
from enum import Enum
from sqlalchemy import (Column, String, Float, Boolean, DateTime,
    Integer, Text, Enum as SAEnum, Numeric, ForeignKey, Index)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

class TradeStatus(str, Enum):
    PENDING="pending"; OPEN="open"; CLOSED="closed"; CANCELLED="cancelled"; FAILED="failed"

class TradeDirection(str, Enum):
    BUY="buy"; SELL="sell"

class SignalSource(str, Enum):
    TRADINGVIEW="tradingview"; MANUAL="manual"; SYSTEM="system"

class OrderType(str, Enum):
    MARKET="market"; LIMIT="limit"

class AlertLevel(str, Enum):
    INFO="info"; WARNING="warning"; CRITICAL="critical"

class Config(Base):
    __tablename__ = "config"
    id         = Column(Integer, primary_key=True)
    key        = Column(String(100), unique=True, nullable=False, index=True)
    value      = Column(Text, nullable=True)
    is_secret  = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class FundSnapshot(Base):
    __tablename__ = "fund_snapshots"
    id            = Column(Integer, primary_key=True)
    total_balance = Column(Numeric(20,8), nullable=False)
    available     = Column(Numeric(20,8), nullable=False)
    locked_25pct  = Column(Numeric(20,8), default=0)
    in_trades     = Column(Numeric(20,8), default=0)
    starting_fund = Column(Numeric(20,8), nullable=False)
    pnl_today     = Column(Numeric(20,8), default=0)
    pnl_total     = Column(Numeric(20,8), default=0)
    milestone_hit = Column(Boolean, default=False)
    snapshot_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class Signal(Base):
    __tablename__ = "signals"
    id            = Column(Integer, primary_key=True)
    source        = Column(SAEnum(SignalSource), default=SignalSource.SYSTEM)
    direction     = Column(SAEnum(TradeDirection), nullable=False)
    pair          = Column(String(20), nullable=False, index=True)
    price         = Column(Numeric(20,8), nullable=False)
    atr           = Column(Numeric(20,8), nullable=True)
    raw_payload   = Column(Text, nullable=True)
    processed     = Column(Boolean, default=False)
    rejected      = Column(Boolean, default=False)
    reject_reason = Column(Text, nullable=True)
    received_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    trades        = relationship("Trade", back_populates="signal")

class Trade(Base):
    __tablename__ = "trades"
    id                = Column(Integer, primary_key=True)
    signal_id         = Column(Integer, ForeignKey("signals.id"), nullable=True)
    exchange_order_id = Column(String(100), nullable=True)
    pair              = Column(String(20), nullable=False, index=True)
    direction         = Column(SAEnum(TradeDirection), nullable=False)
    order_type        = Column(SAEnum(OrderType), default=OrderType.MARKET)
    status            = Column(SAEnum(TradeStatus), default=TradeStatus.PENDING, index=True)
    quantity          = Column(Numeric(20,8), nullable=False)
    entry_price       = Column(Numeric(20,8), nullable=True)
    exit_price        = Column(Numeric(20,8), nullable=True)
    stop_loss_price   = Column(Numeric(20,8), nullable=True)
    take_profit_price = Column(Numeric(20,8), nullable=True)
    pnl               = Column(Numeric(20,8), nullable=True)
    pnl_pct           = Column(Numeric(10,4), nullable=True)
    fee               = Column(Numeric(20,8), default=0)
    fund_at_entry     = Column(Numeric(20,8), nullable=True)
    notes             = Column(Text, nullable=True)
    opened_at         = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    closed_at         = Column(DateTime(timezone=True), nullable=True)
    signal            = relationship("Signal", back_populates="trades")

class DailyReport(Base):
    __tablename__ = "daily_reports"
    id             = Column(Integer, primary_key=True)
    report_date    = Column(DateTime(timezone=True), nullable=False)
    starting_fund  = Column(Numeric(20,8), nullable=False)
    ending_fund    = Column(Numeric(20,8), nullable=False)
    locked_fund    = Column(Numeric(20,8), default=0)
    trades_count   = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades  = Column(Integer, default=0)
    pnl_day        = Column(Numeric(20,8), default=0)
    pnl_total      = Column(Numeric(20,8), default=0)
    email_sent     = Column(Boolean, default=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

class Alert(Base):
    __tablename__ = "alerts"
    id          = Column(Integer, primary_key=True)
    level       = Column(SAEnum(AlertLevel), default=AlertLevel.INFO, index=True)
    category    = Column(String(50), nullable=False)
    message     = Column(Text, nullable=False)
    resolved    = Column(Boolean, default=False)
    notified    = Column(Boolean, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
