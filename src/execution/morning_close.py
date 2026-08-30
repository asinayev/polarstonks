"""Morning Close Script for closing strategy positions and assets held."""

import os
import sys
import csv
import argparse
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import OUT_DIR, GCS_BUCKET, TRADING_ACCOUNT
from src.archive_logs import upload_order_logs
from src.execution.connection import initiate_ib, safe_disconnect
from src.execution.trade_logger import log_trade
from src.execution.order_builder import (
    convert_to_export_url,
    qualify_stock_contract,
    build_close_order,
)
from src.execution.morning_orders import fetch_strategy_allocations, load_permanent_assets

DEFAULT_ALLOCATIONS_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/edit?gid=1955878182#gid=1955878182"
DEFAULT_CLOSE_TYPES_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/edit?gid=61355146#gid=61355146"
DEFAULT_ASSETS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/export?format=csv"
DEFAULT_CSV_DIR = OUT_DIR
DEFAULT_LOG_FILE = os.path.join(OUT_DIR, "order_logs.json")
DEFAULT_CURRENT_STATUS_FILE = os.path.join(OUT_DIR, "current_moves.csv")
DEFAULT_ASSETS_FILE = os.path.join(OUT_DIR, "assets_held.csv")
DEFAULT_AUTO_ASSETS_FILE = os.path.join(OUT_DIR, "assets_held_auto.csv")


def load_close_types(source: str) -> Dict[str, str]:
    close_types = {}
    if source.startswith(("http://", "https://")):
        csv_url = convert_to_export_url(source)
        req = urllib.request.Request(csv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = resp.read().decode("utf-8").splitlines()
    else:
        if not os.path.exists(source):
            return close_types
        with open(source, "r", encoding="utf-8") as f:
            lines = f.readlines()

    reader = csv.reader(lines)
    for row in reader:
        if not row or len(row) < 2:
            continue
        strat = row[0].strip()
        ctype = row[1].strip()
        if strat and ctype and strat.lower() not in ("strategy", "name"):
            close_types[strat] = ctype
    return close_types


def load_current_status(current_status_file: str) -> Dict[str, Dict[str, str]]:
    status_dict = {}
    if not os.path.exists(current_status_file):
        return status_dict
    try:
        with open(current_status_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                sym = row.get("symbol", "").strip().upper()
                if sym:
                    status_dict[sym] = row
    except Exception as e:
        print(f"Warning: Could not read current status file: {e}")
    return status_dict


def execute_morning_close(args):
    print("Starting Morning Close Routine...")
    allocations = fetch_strategy_allocations(args.sheet_url)
    close_types = load_close_types(args.close_types_url)
    current_status = load_current_status(args.currentstatusfile)
    permanent_assets = load_permanent_assets(args.assets_file)

    if args.dry_run:
        print(f"[DRY RUN] Morning close would process {len(allocations)} strategies.")
        return

    ib = initiate_ib(args, client_id=args.clientid, real=args.real)
    orders_placed = 0

    try:
        positions_list = ib.positions()
        if args.account:
            positions_list = [p for p in positions_list if p.account == args.account]
        current_positions = {p.contract.symbol.upper(): p.position for p in positions_list if p.position != 0}

        # Build set of symbols that already have an active open order (avoid duplicates)
        open_trades_symbols = {
            t.contract.symbol.upper() for t in ib.openTrades()
            if not args.account or t.order.account == args.account
        }

        def close_symbol(symbol: str, closetype: str, illiquid: bool, strat_ref: str):
            nonlocal orders_placed
            pos_qty = current_positions.get(symbol, 0)
            if pos_qty == 0:
                return

            # Skip if there's already an active order for this symbol
            if symbol in open_trades_symbols:
                print(f"Skipping {symbol} - there is already an active trade in progress.")
                return

            # Require current_status data for all non-permanent close types
            stat_row = current_status.get(symbol, {})
            if closetype != "permanent" and not stat_row:
                print(f"Stock {symbol} does not have current data in status file. CLOSE MANUALLY")
                return

            contract = qualify_stock_contract(symbol, ib)
            if not contract:
                return

            # Compute limit price based on close type
            lmt_price = None
            if stat_row:
                try:
                    close_price = float(stat_row.get("close", 0) or 0)
                    high_price = float(stat_row.get("high", 0) or 0)
                    low_price = float(stat_row.get("low", 0) or 0)
                    open_price = float(stat_row.get("open", 0) or 0)
                except (ValueError, TypeError):
                    close_price = high_price = low_price = open_price = 0

                if closetype == "last_high_eod":
                    lmt_price = round(high_price, 2) if high_price else None

                elif closetype == "tru_rally":
                    # Reference formula: close + high - low
                    if close_price and high_price and low_price:
                        lmt_price = round(close_price + high_price - low_price, 2)

                elif closetype == "low_close_moo":
                    # Check entry conditions before proceeding
                    if pos_qty < 0:  # short position — BUY to cover
                        low_close_short_cond = (
                            high_price > low_price and
                            close_price < (low_price + 0.2 * (high_price - low_price))
                        )
                        if not low_close_short_cond:
                            print(f"Stock {symbol} did not meet low_close_moo (short) conditions. Skipping.")
                            return
                        lmt_price = round(high_price * 1.1, 2) if high_price else None
                    else:  # long position — SELL
                        low_close_long_cond = (
                            open_price > 0 and close_price < open_price * 0.875
                        )
                        if not low_close_long_cond:
                            print(f"Stock {symbol} did not meet low_close_moo (long) conditions. Skipping.")
                            return
                        lmt_price = round(low_price * 0.8, 2) if low_price else None

            order = build_close_order(
                account=args.account,
                position_qty=pos_qty,
                closetype=closetype,
                illiquid=illiquid,
                lmt_price=lmt_price,
                contract=contract,
            )

            if order:
                print(f"Placing close order: {order.action} {order.totalQuantity} {symbol} ({closetype})")
                trade = ib.placeOrder(contract, order)
                orders_placed += 1
                try:
                    log_trade(trade, strat_ref, args.logfile, {}, ib)
                except Exception as e:
                    print(f"Failed to log trade: {e}")

        # Process morning strategies
        for strat_name, _ in allocations:
            ctype = close_types.get(strat_name, "last_high_eod")
            strat_file = os.path.join(args.csv_dir, f"{strat_name}.csv")
            if not os.path.exists(strat_file):
                continue
            with open(strat_file, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sym = row.get("symbol", "").strip().upper()
                    if sym and sym not in permanent_assets:
                        close_symbol(sym, ctype, False, strat_file)

        # Process auto assets held
        if os.path.exists(args.auto_assets_file):
            with open(args.auto_assets_file, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sym = row.get("symbol", "").strip().upper()
                    ctype = row.get("close_type", "last_high_eod").strip().lower()
                    if sym and ctype != "permanent":
                        close_symbol(sym, ctype, row.get("liquid", "1").strip() == "0", args.auto_assets_file)

        print(f"\n>>> Morning close complete. Total close orders placed: {orders_placed}")

    finally:
        safe_disconnect(ib)

    # Sync order logs to GCS
    try:
        upload_order_logs(out_dir=OUT_DIR, bucket_name=GCS_BUCKET)
    except Exception as e:
        print(f"Warning: Failed to sync order logs to GCS: {e}")


def main():
    parser = argparse.ArgumentParser(description="Automate closing orders via IBKR.")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account")
    parser.add_argument("--account", type=str, default=TRADING_ACCOUNT, required=False, help="IBKR Account number")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")
    parser.add_argument("--csv_dir", type=str, default=DEFAULT_CSV_DIR, help="Directory with strategy CSVs")
    parser.add_argument("--currentstatusfile", type=str, default=DEFAULT_CURRENT_STATUS_FILE, help="Current prices CSV")
    parser.add_argument("--assets_file", type=str, default=DEFAULT_ASSETS_FILE, help="Held assets CSV")
    parser.add_argument("--auto_assets_file", type=str, default=DEFAULT_AUTO_ASSETS_FILE, help="Auto assets CSV")
    parser.add_argument("--logfile", type=str, default=DEFAULT_LOG_FILE, help="Order logs JSON path")
    parser.add_argument("--sheet_url", type=str, default=DEFAULT_ALLOCATIONS_URL, help="Allocations Sheet URL")
    parser.add_argument("--close_types_url", type=str, default=DEFAULT_CLOSE_TYPES_URL, help="Close types Sheet URL")
    parser.add_argument("--dry-run", action="store_true", help="Preview closing orders without submitting")

    args, _ = parser.parse_known_args()
    execute_morning_close(args)


if __name__ == "__main__":
    main()
