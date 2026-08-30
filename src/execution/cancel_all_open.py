"""Cancel or list all open IBKR orders."""

import os
import sys
import argparse
from typing import Optional

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.execution.connection import initiate_ib, safe_disconnect


def cancel_all_open_orders(
    real: bool = True,
    account: Optional[str] = None,
    client_id: int = 14,
    just_print: bool = False,
):
    """Lists or cancels all open orders."""
    ib = initiate_ib(client_id=client_id, real=real)
    try:
        ib.client.reqAllOpenOrders()
        ib.reqOpenOrders()
        ib.sleep(1.0)  # Allow socket to receive open orders from gateway/TWS
        trades = ib.openTrades()
        if account:
            trades = [t for t in trades if t.order.account == account]

        if just_print:
            if not trades:
                print("No open orders found.")
                return

            print(f"Found {len(trades)} open order(s):\n")
            #print(f"{OrderId:<10} {Action:<6} {Qty:<8} {Symbol:<8} {Type:<6} {LmtPrice:<10} {Status:<14} {TIF:<6} {Account}")
            print("-" * 80)
            for t in trades:
                order = t.order
                status = t.orderStatus.status if t.orderStatus else "Unknown"
                filled = t.orderStatus.filled if t.orderStatus else 0
                qty_str = f"{filled}/{order.totalQuantity}" if filled > 0 else f"{order.totalQuantity}"
                lmt_str = f"${order.lmtPrice:.2f}" if order.lmtPrice else "N/A"
                tif_str = str(order.tif) if order.tif else "N/A"
                acct_str = str(order.account) if order.account else ""
                print(
                    f"{order.orderId:<10} {order.action:<6} {qty_str:<8} {t.contract.symbol:<8} "
                    f"{order.orderType:<6} {lmt_str:<10} {status:<14} {tif_str:<6} {acct_str}"
                )
        else:
            if not trades:
                print("No open orders to cancel.")
                return
            print(f"Canceling {len(trades)} open orders...")
            for t in trades:
                ib.cancelOrder(t.order)
            ib.sleep(0.5)
            print("Cancellation requests submitted.")
    finally:
        safe_disconnect(ib)


def main():
    parser = argparse.ArgumentParser(description="Cancel or list all open orders in IBKR.")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account port (4001)")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account port (4002)")
    parser.add_argument("--account", type=str, required=False, help="IBKR Account number")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")
    parser.add_argument(
        "--justprint",
        "--view",
        "--list",
        "--dry-run",
        dest="justprint",
        action="store_true",
        help="Only view/print open orders without canceling",
    )
    args, _ = parser.parse_known_args()

    cancel_all_open_orders(
        real=args.real,
        account=args.account,
        client_id=args.clientid,
        just_print=args.justprint,
    )


if __name__ == "__main__":
    main()
