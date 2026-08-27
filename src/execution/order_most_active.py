"""Order Most Active Stock (vp_order == 1) via IBKR."""

import os
import sys
import csv
import math
import argparse
from typing import Optional

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import OUT_DIR, GCS_BUCKET
from src.archive_logs import upload_order_logs
from src.execution.connection import initiate_ib, safe_disconnect
from src.execution.trade_logger import log_trade
from src.execution.order_builder import qualify_stock_contract, get_stock_price
from ib_async import Order


def check_spy(args) -> str:
    if args.minspymove or args.maxspymove:
        if not args.spyfile or not os.path.exists(args.spyfile):
            return "SPY file not specified or found"
        try:
            with open(args.spyfile, "r", encoding="utf-8") as f:
                spy = next(csv.DictReader(f))
            todays_change = float(spy["todaysChangePerc"])
            if args.minspymove and todays_change < float(args.minspymove):
                return f"SPY moved too low ({todays_change}%) for {args.file}"
            if args.maxspymove and todays_change > float(args.maxspymove):
                return f"SPY moved too high ({todays_change}%) for {args.file}"
        except Exception as e:
            return f"Error checking SPY status: {e}"
    return ""


def check_status_list(args) -> str:
    if args.trixstatuslist and os.path.exists(args.trixstatuslist):
        try:
            with open(args.trixstatuslist, "r", encoding="utf-8") as f:
                status_list = list(csv.DictReader(f))
            strat_names = ["order_most_active", os.path.splitext(os.path.basename(args.file))[0]]
            for row in status_list:
                if row.get("strategy") in strat_names:
                    if not int(row.get("allow", "1")):
                        return f"{row['strategy']} not allowed by status list"
        except Exception as e:
            return f"Error checking status list: {e}"
    return ""


def find_most_active_stock(file_path: str):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                vp_val = row.get("vp_order")
                if vp_val:
                    try:
                        if float(vp_val) == 1.0:
                            return row
                    except ValueError:
                        pass
    except Exception as e:
        print(f"Error reading CSV {file_path}: {e}")
    return None


def run_order_most_active(args):
    # 1. Safety checks
    spy_issue = check_spy(args)
    status_issue = check_status_list(args)
    if spy_issue or status_issue:
        issues = [i for i in [spy_issue, status_issue] if i]
        print("Safety check(s) failed:\n" + "\n".join(issues))
        return

    # 2. Find target stock
    target_row = find_most_active_stock(args.file)
    if not target_row:
        print(f"No stock found with vp_order == 1 in {args.file}")
        return

    symbol = target_row.get("symbol")
    if not symbol:
        print(f"Row with vp_order == 1 is missing symbol: {target_row}")
        return

    print(f"Targeting most active stock: {symbol} (vp_order == 1)")

    if args.dry_run:
        print(f"[DRY RUN] Would buy {symbol} with ${args.cash:,.2f} at Market on Close (MOC)")
        return

    # 3. Connect to IBKR
    ib = initiate_ib(args, client_id=args.clientid, real=args.real)
    try:
        contract = qualify_stock_contract(symbol, ib)
        if not contract:
            return

        price = get_stock_price(contract, target_row, ib)
        if math.isnan(price) or price <= 0:
            print(f"Could not determine a valid price for {symbol}.")
            return

        print(f"Current price for {symbol}: {price:.2f}")
        if price < args.minprice:
            print(f"Skipping: price {price:.2f} is below minprice {args.minprice}")
            return

        quantity = round(args.cash / price)
        if quantity <= 0:
            print(f"Calculated quantity is 0 for price {price:.2f} and cash {args.cash}. Skipping.")
            return

        order = Order(
            action="BUY",
            orderType="MOC",
            totalQuantity=quantity,
            tif=None,
            account=args.account,
        )

        print(f"Placing MOC BUY order: {quantity} shares of {symbol} (~${quantity * price:,.2f})")
        trade = ib.placeOrder(contract, order)

        log_file = args.logfile or os.path.join(OUT_DIR, "order_logs.json")
        try:
            log_trade(trade, args.file, log_file, {}, ib)
        except Exception as e:
            print(f"Failed to log trade: {e}")
    finally:
        safe_disconnect(ib)

    # 4. Sync order logs to GCS
    try:
        upload_order_logs(out_dir=OUT_DIR, bucket_name=GCS_BUCKET)
    except Exception as e:
        print(f"Warning: Failed to sync order logs to GCS: {e}")


def main():
    parser = argparse.ArgumentParser(description="Buy the stock with vp_order==1 at market on close.")
    parser.add_argument("--file", type=str, required=True, help="CSV file with stock rankings")
    parser.add_argument("--cash", type=float, required=True, help="Cash amount to spend")
    parser.add_argument("--minprice", type=float, default=6.0, help="Minimum stock price allowed")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account")
    parser.add_argument("--account", type=str, required=False, help="IBKR account number")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")
    parser.add_argument("--minspymove", type=float, required=False, help="Minimum SPY move threshold")
    parser.add_argument("--maxspymove", type=float, required=False, help="Maximum SPY move threshold")
    parser.add_argument("--spyfile", type=str, default=os.path.join(OUT_DIR, "spy_change.csv"), help="Path to SPY change CSV")
    parser.add_argument("--trixstatuslist", type=str, default=os.path.join(OUT_DIR, "allow_list.csv"), help="Path to allow list CSV")
    parser.add_argument("--logfile", type=str, default=os.path.join(OUT_DIR, "order_logs.json"), help="Order log file")
    parser.add_argument("--dry-run", action="store_true", help="Preview trade without sending to IBKR")

    args, _ = parser.parse_known_args()
    run_order_most_active(args)


if __name__ == "__main__":
    main()
