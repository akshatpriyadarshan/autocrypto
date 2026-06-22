"""
Delta Exchange India — direct REST API, no ccxt.
Endpoint: https://api.india.delta.exchange
HMAC-SHA256 auth. Bypasses ccxt version issues entirely.
"""
import hashlib, hmac, os, time, json
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential
import httpx

from backend.config.config_manager import get_config
from backend.db.database import get_session
from backend.models.db_models import Trade, TradeStatus, TradeDirection
from sqlalchemy import select

BASE_URL         = "https://api.india.delta.exchange"
DELTA_API_KEY    = "76wEBRrPbx64EUzphk43LIX1kCWrFb"
DELTA_API_SECRET = "3lJghi3DLRdgeoesLYxfBg5l9jH4Q0HEjLMOkN744dp9dOH4ddiHG6Mv09cH"

PAIR_TO_SYMBOL = {
    "BTC/USDT":  "BTCUSDT",  "ETH/USDT":  "ETHUSDT",
    "SOL/USDT":  "SOLUSDT",  "XRP/USDT":  "XRPUSDT",
    "DOGE/USDT": "DOGEUSDT", "LINK/USDT": "LINKUSDT",
    "AVAX/USDT": "AVAXUSDT", "ADA/USDT":  "ADAUSDT",
}

_product_cache: dict = {}


def _get_delta_settings() -> tuple[str, str, bool]:
    with get_session() as db:
        api_key = os.getenv("DELTA_API_KEY") or get_config(db, "delta_api_key") or DELTA_API_KEY
        api_secret = os.getenv("DELTA_API_SECRET") or get_config(db, "delta_api_secret") or DELTA_API_SECRET
        if os.getenv("DELTA_TESTNET") is not None:
            testnet = os.getenv("DELTA_TESTNET").lower() in ("1", "true", "yes", "y")
        else:
            testnet = get_config(db, "delta_testnet") == "true"
    return api_key, api_secret, testnet


def _delta_base_url(testnet: bool = False) -> str:
    return "https://cdn-ind.testnet.deltaex.org" if testnet else BASE_URL


def _sign(method: str, path: str, body: str = "") -> dict:
    api_key, api_secret, _ = _get_delta_settings()
    ts  = str(int(time.time()))
    sig = hmac.new(
        api_secret.encode(),
        (method + ts + path + body).encode(),
        hashlib.sha256
    ).hexdigest()
    return {"api-key": api_key, "timestamp": ts, "signature": sig,
            "Content-Type": "application/json", "User-Agent": "autocrypto/1.0"}


def _get(path: str) -> dict:
    _, _, testnet = _get_delta_settings()
    r = httpx.get(f"{_delta_base_url(testnet)}{path}", headers=_sign("GET", path), timeout=10.0)
    if r.status_code != 200:
        try:
            err = r.json()
        except Exception:
            err = r.text
        raise RuntimeError(f"Delta API GET {path} failed {r.status_code}: {err}")
    return r.json()


def _post(path: str, body: dict) -> dict:
    s    = json.dumps(body, separators=(",", ":"))
    _, _, testnet = _get_delta_settings()
    r    = httpx.post(f"{_delta_base_url(testnet)}{path}", headers=_sign("POST", path, s), content=s, timeout=10.0)
    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Failed to parse API response: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"Delta API POST {path} failed {r.status_code}: {data}")
    
    if not data.get("success"):
        error = data.get("error", data)
        raise RuntimeError(f"Delta API error: {error}")
    
    result = data.get("result")
    if result is None:
        raise RuntimeError("API returned success=true but no result field")
    
    return data


def get_wallet_balance_sync() -> Optional[float]:
    api_key, _, _ = _get_delta_settings()
    if api_key == DELTA_API_KEY:
        logger.warning("Using built-in default Delta API key. This key is only whitelisted for Streamlit Cloud IPs and may fail locally.")
    try:
        data = _get("/v2/wallet/balances")
        if data.get("success"):
            for a in data.get("result", []):
                if a.get("asset_symbol") in ("USDT", "USD"):
                    bal = float(a.get("balance") or a.get("available_balance") or 0)
                    logger.info(f"Delta wallet: ${bal:.2f} USDT")
                    return bal
        logger.warning(f"Balance unexpected: {str(data)[:200]}")
        return None
    except Exception as e:
        logger.warning(f"Balance: {e}")
        return None


def get_market_price_sync(pair: str) -> Optional[float]:
    sym = PAIR_TO_SYMBOL.get(pair, pair.replace("/", ""))
    try:
        data = _get(f"/v2/tickers/{sym}")
        if not isinstance(data, dict) or not data.get("success"):
            return None
        r = data.get("result", {})
        p = r.get("close") or r.get("mark_price") or r.get("last_price")
        return float(p) if p else None
    except Exception as e:
        logger.warning(f"Price {pair}: {e}")
        return None


def _get_product_id(symbol: str) -> Optional[int]:
    if symbol in _product_cache:
        return _product_cache[symbol]
    for ctype in ["spot", "perpetual_futures"]:
        try:
            data = _get(f"/v2/products?contract_type={ctype}&state=live")
            if data.get("success"):
                for p in data.get("result", []):
                    if p.get("symbol") == symbol:
                        pid = int(p["id"])
                        _product_cache[symbol] = pid
                        logger.info(f"{symbol} → product_id={pid} ({ctype})")
                        return pid
        except Exception as e:
            logger.warning(f"Product lookup {ctype} {symbol}: {e}")
    logger.error(f"No product_id for {symbol}")
    return None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _place_order(pair: str, side: str, qty: float) -> dict:
    sym  = PAIR_TO_SYMBOL.get(pair, pair.replace("/", ""))
    pid  = _get_product_id(sym)
    if not pid:
        raise RuntimeError(f"Product not found: {sym}")
    # Keep decimal precision for crypto — always use float
    size = float(round(qty, 8))
    body = {"product_id": pid, "size": size, "side": side.lower(),
            "order_type": "market_order", "time_in_force": "ioc"}
    logger.info(f"Order: {side.upper()} {sym} size={size} pid={pid}")
    data = _post("/v2/orders", body)
    res  = data.get("result", {})
    logger.info(f"Placed: id={res.get('id')} state={res.get('state')} avg={res.get('average_fill_price')}")
    return res


def execute_trade(trade_id: int):
    with get_session() as db:
        trade = db.execute(select(Trade).where(Trade.id == trade_id)).scalar_one_or_none()
        if not trade or trade.status != TradeStatus.PENDING:
            return
        try:
            res  = _place_order(str(trade.pair), str(trade.direction.value), float(trade.quantity))
            
            # ── Validate API response structure ──────────────────────────────────
            if not isinstance(res, dict):
                raise RuntimeError(f"Invalid API response type: {type(res)}")
            
            order_id = res.get("id")
            if not order_id:
                raise RuntimeError("Order placed but no order ID in response")
            
            # ── Check order state and fill status ─────────────────────────────────
            order_state = res.get("state", "").lower()
            filled_qty = float(res.get("filled_quantity") or res.get("size", 0) or 0)
            requested_qty = float(trade.quantity or 0)
            
            if filled_qty <= 0:
                raise RuntimeError(f"Order not filled: state={order_state}, qty={filled_qty}")
            
            if filled_qty < requested_qty * 0.95:  # Allow 5% tolerance for rounding
                logger.warning(f"Partial fill: requested={requested_qty:.6f} filled={filled_qty:.6f}")
            
            # ── Validate and extract fill price ──────────────────────────────────
            fill = float(res.get("average_fill_price") or res.get("last_price") or 0)
            if fill <= 0:
                raise RuntimeError(f"Invalid fill price in response: {res.get('average_fill_price')} | {res.get('last_price')}")
            
            # ── Update trade with validated data ─────────────────────────────────
            trade.exchange_order_id = str(order_id)
            trade.entry_price = Decimal(str(round(fill, 8)))
            trade.status = TradeStatus.OPEN
            logger.info(f"Trade #{trade_id} OPEN @ ${fill:.4f} qty={filled_qty:.6f} state={order_state}")
            
        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.notes  = str(e)[:500]
            logger.error(f"execute_trade #{trade_id}: {e}")
    try:
        from backend.services.fund_manager import take_fund_snapshot
        take_fund_snapshot()
    except Exception as e:
        logger.error(f"Snapshot: {e}")


def close_trade_market(trade_id: int, exit_price: float, reason: str = "signal"):
    with get_session() as db:
        trade = db.execute(select(Trade).where(Trade.id == trade_id)).scalar_one_or_none()
        if not trade or trade.status != TradeStatus.OPEN:
            return
        try:
            cside  = "sell" if trade.direction == TradeDirection.BUY else "buy"
            res    = _place_order(str(trade.pair), cside, float(trade.quantity))
            
            # Validate close order response
            if not res.get("id"):
                raise RuntimeError("Close order placed but no ID in response")
            
            filled_qty = float(res.get("filled_quantity") or res.get("size", 0) or 0)
            if filled_qty <= 0:
                raise RuntimeError(f"Close order not filled: state={res.get('state')}")
            
            actual = float(res.get("average_fill_price") or res.get("last_price") or 0)
            if actual <= 0:
                raise RuntimeError(f"Invalid close price in response: {res}")
            entry  = float(trade.entry_price or actual)
            pnl    = ((actual-entry) if trade.direction==TradeDirection.BUY else (entry-actual)) * float(trade.quantity)
            pnl_pct = ((actual-entry)/entry*100) if entry>0 else 0
            if trade.direction == TradeDirection.SELL: pnl_pct = -pnl_pct
            trade.status     = TradeStatus.CLOSED
            trade.exit_price = Decimal(str(round(actual, 8)))
            trade.pnl        = Decimal(str(round(pnl, 2)))
            trade.pnl_pct    = Decimal(str(round(pnl_pct, 4)))
            trade.closed_at  = datetime.now(timezone.utc)
            trade.notes      = (trade.notes or "") + f" | {reason}"
            logger.info(f"Trade #{trade_id} CLOSED pnl=${pnl:.4f}")
        except Exception as e:
            logger.error(f"close_trade #{trade_id}: {e}")
    try:
        from backend.services.fund_manager import take_fund_snapshot
        take_fund_snapshot()
    except Exception as e:
        logger.error(f"Snapshot: {e}")
