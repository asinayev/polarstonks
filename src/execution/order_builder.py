"""Order construction and market data helpers."""

import math
import re
from datetime import datetime
from typing import Optional, Dict, Any

from ib_async import (
    IB,
    Order,
    Stock,
    TagValue,
    TimeCondition,
    PriceCondition,
    ExecutionCondition,
)


def convert_to_export_url(url: str) -> str:
    """Convert Google Sheets URL to CSV export format."""
    if "export?format=csv" in url:
        return url
    sheet_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not sheet_match:
        return url
    sheet_id = sheet_match.group(1)
    gid_match = re.search(r"[#&?]gid=([0-9]+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def create_time_condition(time_str: Optional[str] = None) -> TimeCondition:
    """Creates an IB TimeCondition defaulting to 15:50:00 US/Eastern of today."""
    if not time_str:
        time_str = datetime.today().strftime("%Y%m%d") + " 15:50:00 US/Eastern"
    return TimeCondition(isMore=True, time=time_str, conjunction="a")


def qualify_stock_contract(symbol: str, ib: IB) -> Optional[Stock]:
    """Qualifies and returns the Stock contract via IBKR."""
    qualifieds = ib.qualifyContracts(Stock(symbol, exchange="SMART", currency="USD"))
    if len(qualifieds) == 0:
        print(f"Symbol not found: {symbol}")
        return None
    elif len(qualifieds) > 1:
        print(f"Multiple contracts found for {symbol}. Skipping.")
        return None
    return qualifieds[0]


def get_stock_price(contract: Stock, row: Optional[Dict[str, Any]], ib: IB) -> float:
    """Robust price retrieval with live market data and CSV fallback."""
    ib.reqMarketDataType(1)  # Live
    ticker = ib.reqMktData(contract, genericTickList="", snapshot=True, regulatorySnapshot=False, mktDataOptions=None)
    
    i = 0
    while math.isnan(ticker.last):
        ib.sleep(0.1)
        i += 1
        if i > 30:
            break

    price = ticker.last if not math.isnan(ticker.last) else ticker.close
    if (math.isnan(price) or price <= 0) and row and "close" in row and row["close"]:
        try:
            price = float(row["close"])
        except ValueError:
            pass
    return price


def build_close_order(
    account: Optional[str],
    position_qty: float,
    closetype: str,
    illiquid: bool,
    lmt_price: Optional[float] = None,
    contract: Optional[Stock] = None,
    action_override: Optional[str] = None,
) -> Optional[Order]:
    """
    Constructs an IB closing Order based on current position and strategy close type.
    """
    action = action_override if action_override else ("SELL" if position_qty > 0 else "BUY")
    qty = abs(position_qty)
    time_condition = create_time_condition()
    limit_close_type = closetype in ("last_high_eod", "tru_rally")

    # Case 1: Permanent Asset
    if closetype == "permanent":
        if illiquid:
            return Order(
                action=action,
                orderType="MKT",
                algoStrategy="Adaptive",
                algoParams=[TagValue("adaptivePriority", "Patient")],
                totalQuantity=qty,
                tif="DAY",
                conditions=[time_condition],
                account=account,
            )
        else:
            return Order(
                action=action,
                orderType="MOC",
                totalQuantity=qty,
                tif=None,
                account=account,
            )

    # Case 2: Illiquid limit close (SELL for long)
    if illiquid and limit_close_type and position_qty > 0:
        return Order(
            action="SELL",
            orderType="LMT",
            algoStrategy="Adaptive",
            algoParams=[TagValue("adaptivePriority", "Patient")],
            totalQuantity=qty,
            tif="DAY",
            conditions=[time_condition],
            lmtPrice=lmt_price,
            account=account,
        )

    # Case 3: Last High EOD (BUY for short)
    elif closetype == "last_high_eod" and position_qty < 0:
        price_cond = PriceCondition(
            1, conjunction="a", isMore=True,
            price=lmt_price, exch="SMART", conId=contract.conId if contract else 0
        )
        return Order(
            action="BUY",
            orderType="MKT",
            totalQuantity=qty,
            tif="DAY",
            conditions=[price_cond, time_condition],
            account=account,
        )

    # Case 4: Last High EOD / Tru Rally (SELL for long)
    elif limit_close_type and position_qty > 0:
        return Order(
            action="SELL",
            orderType="LOC",
            totalQuantity=qty,
            tif=None,
            lmtPrice=lmt_price,
            account=account,
        )

    # Case 5: Low Close MOO / MKT EOD (SELL for long)
    elif closetype in ("low_close_moo", "mkt_eod") and position_qty > 0:
        if illiquid:
            return Order(
                action="SELL",
                orderType="MKT",
                algoStrategy="Adaptive",
                algoParams=[TagValue("adaptivePriority", "Patient")],
                totalQuantity=qty,
                tif="DAY",
                conditions=[time_condition],
                account=account,
            )
        else:
            return Order(
                action="SELL",
                orderType="MOC",
                totalQuantity=qty,
                tif=None,
                account=account,
            )

    return None
