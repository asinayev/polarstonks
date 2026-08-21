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
        trades = ib.openTrades()
        if account:
            trades = [t for t in trades if t.order.account == account]

        if just_print:
            print(f"Found {len(trades)} open orders:")
            for t in trades:
                print(
                    f"{t.order.action} {t.order.totalQuantity} {t.contract.symbol} "
                    f"Type: {t.order.orderType} Lmt: {t.order.lmtPrice} TIF: {t.order.tif}"
                )
        else:
            print(f"Canceling {len(trades)} open orders...")
            for t in trades:
                ib.cancelOrder(t.order)
            print("Cancellation requests submitted.")
    finally:
        safe_disconnect(ib)


def main():
    parser = argparse.ArgumentParser(description="Cancel or list all open orders in IBKR.")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account port (4001)")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account port (4002)")
    parser.add_argument("--account", type=str, required=False, help="IBKR Account number")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")
    parser.add_argument("--justprint", "--dry-run", dest="justprint", action="store_true", help="Only print open orders")
    args = parser.parse_args()

    cancel_all_open_orders(
        real=args.real,
        account=args.account,
        client_id=args.clientid,
        just_print=args.justprint,
    )


if __name__ == "__main__":
    main()
