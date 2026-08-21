"""Transaction and trade logging utilities."""

import os
import datetime
from typing import Optional, Dict, Any
from ib_async import Trade, IB


def log_trade(
    trade: Trade,
    trade_reason: str,
    log_file: str,
    notes: Optional[Dict[str, Any]] = None,
    ib: Optional[IB] = None,
):
    """Logs a trade execution details to the specified JSON log file."""
    continue_i = 30
    while trade.orderStatus.status == "PendingSubmit" and continue_i > 0:
        if ib:
            ib.sleep(1)
        continue_i -= 1

    if not continue_i:
        print("Order still pending after 30s")
    else:
        print(f"Order status: {trade.orderStatus.status}")

    traded = len(trade.fills) > 0
    quantity = sum([fill.execution.shares for fill in trade.fills]) if traded else trade.order.totalQuantity
    
    first_fill_ts = min([fill.time for fill in trade.fills]).timestamp() if traded else 0
    order_id = [fill.execution.orderId for fill in trade.fills][0] if traded else trade.order.orderId
    price_paid = sum([f.execution.price * f.execution.shares for f in trade.fills]) / quantity if traded and quantity > 0 else 0

    to_log = {
        "symbol": trade.contract.localSymbol,
        "date": str(datetime.datetime.now().date()),
        "order_id": order_id,
        "action": trade.order.action,
        "quantity": quantity,
        "order_type": trade.order.orderType,
        "lmt_price": trade.order.lmtPrice,
        "tif": trade.order.tif,
        "status": trade.orderStatus.status,
        "fills": len(trade.fills),
        "price_paid": price_paid,
        "commission": sum([fill.commissionReport.commission for fill in trade.fills]),
        "realizedPNL": sum([fill.commissionReport.realizedPNL for fill in trade.fills]),
        "trade_reason": trade_reason,
        "record_ts": datetime.datetime.now().timestamp(),
        "first_fill_ts": first_fill_ts,
    }

    if notes:
        to_log.update(notes)

    to_log = {k: v for k, v in to_log.items() if v is not None}

    os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(str(to_log) + "\n")
