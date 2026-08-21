"""Morning Orders Execution Pipeline."""

import os
import sys
import csv
import io
import math
import argparse
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import OUT_DIR, GCS_BUCKET
from src.archive_logs import upload_order_logs
from src.execution.connection import initiate_ib, safe_disconnect
from src.execution.trade_logger import log_trade
from src.execution.order_builder import (
    convert_to_export_url,
    qualify_stock_contract,
    get_stock_price,
)
from ib_async import Order, Stock

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/edit?gid=1955878182#gid=1955878182"
DEFAULT_CSV_DIR = OUT_DIR
DEFAULT_LOG_FILE = os.path.join(OUT_DIR, "order_logs.json")
DEFAULT_SPY_FILE = os.path.join(OUT_DIR, "spy_change.csv")
DEFAULT_ASSETS_FILE = os.path.join(OUT_DIR, "assets_held.csv")
DEFAULT_STATUS_LIST = os.path.join(OUT_DIR, "allow_list.csv")


def fetch_strategy_allocations(source: str) -> List[Tuple[str, float]]:
    allocations = []
    if source.startswith(("http://", "https://")):
        csv_url = convert_to_export_url(source)
        req = urllib.request.Request(csv_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = resp.read().decode("utf-8").splitlines()
    else:
        with open(source, "r", encoding="utf-8") as f:
            lines = f.readlines()

    reader = csv.reader(lines)
    for row in reader:
        if not row or len(row) < 2:
            continue
        strat_name = row[0].strip()
        cash_str = row[1].strip().replace("$", "").replace(",", "")
        if not strat_name or strat_name.lower() in ("strategy", "name", "strategy name"):
            continue
        try:
            cash = float(cash_str)
            if cash > 0:
                allocations.append((strat_name, cash))
        except ValueError:
            continue
    return allocations


def check_spy_filter(args, strat_name: str) -> Optional[str]:
    if not args.minspymove and not args.maxspymove:
        return None
    if not os.path.exists(args.spyfile):
        return f"SPY file '{args.spyfile}' not found"
    try:
        with open(args.spyfile, "r", encoding="utf-8") as f:
            spy_data = next(csv.DictReader(f))
            todays_change = float(spy_data["todaysChangePerc"])
            if args.minspymove and todays_change < float(args.minspymove):
                return f"SPY move {todays_change}% < min {args.minspymove}%"
            if args.maxspymove and todays_change > float(args.maxspymove):
                return f"SPY move {todays_change}% > max {args.maxspymove}%"
    except Exception as e:
        return f"Error reading SPY file: {e}"
    return None


def check_allow_list(allow_list_path: str, strat_name: str) -> Optional[str]:
    if not os.path.exists(allow_list_path):
        return None
    try:
        with open(allow_list_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("strategy") == strat_name:
                    if int(row.get("allow", "1")) == 0:
                        return f"Strategy '{strat_name}' disabled in allow_list"
    except Exception as e:
        return f"Error reading allow_list: {e}"
    return None


def load_permanent_assets(assets_file_path: str) -> Set[str]:
    permanent = set()
    if not os.path.exists(assets_file_path):
        return permanent
    try:
        with open(assets_file_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("close_type", "").strip().lower() == "permanent":
                    sym = row.get("symbol", "").strip().upper()
                    if sym:
                        permanent.add(sym)
    except Exception as e:
        print(f"Warning: Could not read assets held file '{assets_file_path}': {e}")
    return permanent


def place_order(
    row: Dict[str, str],
    ib,
    cash: float,
    strat_file: str,
    args,
    permanent_assets: Optional[Set[str]] = None,
):
    symbol = row.get("symbol", "").strip().upper()
    if not symbol:
        return
    if permanent_assets and symbol in permanent_assets:
        print(f"Skipping permanent held asset: {symbol}")
        return

    contract = qualify_stock_contract(symbol, ib)
    if not contract:
        return

    price = get_stock_price(contract, row, ib)
    if math.isnan(price) or price <= 0:
        print(f"Could not retrieve price for {symbol}. Skipping.")
        return

    if price < args.minprice:
        print(f"Skipping {symbol}: Price ${price:.2f} < minprice ${args.minprice:.2f}")
        return

    quantity = round(cash / price)
    if quantity <= 0:
        print(f"Skipping {symbol}: Computed quantity is 0 (price=${price:.2f}, cash=${cash:,.2f})")
        return

    order = Order(
        action="BUY",
        orderType="MOC",
        totalQuantity=quantity,
        tif=None,
        account=args.account,
    )

    print(f"Placing MOC BUY: {quantity} shares of {symbol} @ ~${price:.2f} (Total: ${quantity * price:,.2f})")
    trade = ib.placeOrder(contract, order)

    try:
        log_trade(trade, strat_file, args.logfile, {}, ib)
    except Exception as e:
        print(f"Failed to log trade: {e}")


AM_OUT_STRATEGIES: Set[str] = {
    # am_stocks
    "bigcap_long",
    "correlated_long",
    "megacap",
    "megacaphist",
    "volumelong",
    "volumehist",
    # am_etfs
    "rally_etfs",
    "revert_etfs",
    "corr_long_etfs",
    "drop_etfs",
    "deviant_etfs",
    "arb_etfs",
    "tru_rally",
    "corr_reverse",
    # am_short_interest
    "short_no_interest",
}


def execute_morning_orders(args):
    print(f"Fetching allocations from: {args.sheet_url}")
    allocations = fetch_strategy_allocations(args.sheet_url)
    if not allocations:
        print("No active strategy allocations found.")
        return

    print(f"\nFetched {len(allocations)} total strategy allocations from source:")
    for name, cash in allocations:
        print(f"  - {name}: ${cash:,.2f}")

    # Filter based on early_submission mode
    if getattr(args, "early_submission", False):
        print(f"\n[MODE: EARLY SUBMISSION] Selecting only am_out strategies ({len(AM_OUT_STRATEGIES)} candidate types)...")
        allocations = [(name, cash) for name, cash in allocations if name in AM_OUT_STRATEGIES]
    elif not getattr(args, "all_strategies", False):
        print(f"\n[MODE: REGULAR/LATE SUBMISSION] Selecting only remaining strategies (excluding am_out strategies)...")
        allocations = [(name, cash) for name, cash in allocations if name not in AM_OUT_STRATEGIES]
    else:
        print(f"\n[MODE: ALL STRATEGIES] Submitting all active allocations...")

    if not allocations:
        print("No strategies match the current submission criteria.")
        return

    print(f"Targeting {len(allocations)} active strategies for this run:")
    for name, cash in allocations:
        print(f"  * {name}: ${cash:,.2f}")

    permanent_assets = load_permanent_assets(args.assets_file)
    executable_strategies = []

    for strat_name, cash in allocations:
        spy_err = check_spy_filter(args, strat_name)
        if spy_err:
            print(f"Skipping {strat_name}: {spy_err}")
            continue

        allow_err = check_allow_list(args.trixstatuslist, strat_name)
        if allow_err:
            print(f"Skipping {strat_name}: {allow_err}")
            continue

        strat_file = os.path.join(args.csv_dir, f"{strat_name}.csv")
        if not os.path.exists(strat_file):
            print(f"Warning: File '{strat_file}' not found for strategy '{strat_name}'. Skipping.")
            continue

        with open(strat_file, "r", encoding="utf-8") as f:
            stock_rows = list(csv.DictReader(f))

        if not stock_rows:
            print(f"Warning: No rows found in '{strat_file}'. Skipping.")
            continue

        executable_strategies.append((strat_name, cash, strat_file, stock_rows))

    if args.dry_run:
        print(f"\n[DRY RUN] Would execute {len(executable_strategies)} strategies.")
        for strat_name, cash, strat_file, stock_rows in executable_strategies:
            print(f"  Strategy: {strat_name} (${cash:,.2f}) with {len(stock_rows)} target stocks.")
        return

    ib = None
    try:
        ib = initiate_ib(args, client_id=args.clientid, real=args.real)
        for strat_name, cash, strat_file, stock_rows in executable_strategies:
            print(f"\n>>> Executing Strategy: {strat_name} (${cash:,.2f})")
            for row in stock_rows:
                place_order(row, ib, cash, strat_file, args, permanent_assets=permanent_assets)
    finally:
        safe_disconnect(ib)

    # Sync order logs to GCS
    try:
        upload_order_logs(out_dir=OUT_DIR, bucket_name=GCS_BUCKET)
    except Exception as e:
        print(f"Warning: Failed to sync order logs to GCS: {e}")


def main():
    parser = argparse.ArgumentParser(description="Execute morning strategy orders via IBKR.")
    parser.add_argument("--sheet_url", type=str, default=DEFAULT_SHEET_URL, help="Google Sheets URL or CSV path")
    parser.add_argument("--csv_dir", type=str, default=DEFAULT_CSV_DIR, help="Directory containing strategy CSVs")
    parser.add_argument("--assets_file", type=str, default=DEFAULT_ASSETS_FILE, help="Path to held assets CSV")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account port")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account port")
    parser.add_argument("--account", type=str, required=False, help="IBKR Account number")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")
    parser.add_argument("--minprice", type=float, default=6.0, help="Minimum stock price allowed")
    parser.add_argument("--minspymove", type=float, required=False, help="Min SPY move threshold")
    parser.add_argument("--maxspymove", type=float, required=False, help="Max SPY move threshold")
    parser.add_argument("--spyfile", type=str, default=DEFAULT_SPY_FILE, help="SPY change CSV")
    parser.add_argument("--trixstatuslist", type=str, default=DEFAULT_STATUS_LIST, help="Allow list CSV")
    parser.add_argument("--logfile", type=str, default=DEFAULT_LOG_FILE, help="Order logs JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Preview orders without connecting to IBKR")
    parser.add_argument(
        "--early_submission",
        "--early",
        dest="early_submission",
        action="store_true",
        help="Submit only strategies produced during am_out (stock & ETF features). If omitted, submits only non-am_out strategies.",
    )
    parser.add_argument(
        "--all_strategies",
        action="store_true",
        help="Submit all strategy allocations without filtering by early/late stage.",
    )

    args = parser.parse_args()
    execute_morning_orders(args)


if __name__ == "__main__":
    main()
