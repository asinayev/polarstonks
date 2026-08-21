"""Reconcile open IBKR positions against Google Sheets assets held."""

import os
import sys
import csv
import io
import argparse
import urllib.request
from typing import Dict, List, Optional

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import OUT_DIR
from src.execution.connection import initiate_ib, safe_disconnect

DEFAULT_ASSETS_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/export?format=csv"
DEFAULT_ALLOW_LIST_URL = "https://docs.google.com/spreadsheets/d/1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE/export?format=csv&gid=1023788458"
DEFAULT_CURRENT_STATUS_FILE = os.path.join(OUT_DIR, "current_moves.csv")
DEFAULT_OUT_FILE = os.path.join(OUT_DIR, "assets_held_auto.csv")


def load_csv_or_sheet(source: str) -> List[Dict[str, str]]:
    """Loads CSV rows from a Google Sheets URL or local file path."""
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))
    with open(source, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def extract_close_types_and_liquidities(asset_dict):
    close_types = {}
    liquidities = {}
    desired_positions = {}
    found_col = None
    
    for row in asset_dict:
        symbol = row.get("symbol")
        if not symbol:
            continue
        close_types[symbol] = row.get("close_type", "")
        liquidities[symbol] = row.get("liquid", "1")
        
        if found_col is None:
            for col in ["desired_position", "desired position"]:
                if col in row:
                    found_col = col
                    break
        
        if found_col:
            desired_positions[symbol] = row[found_col]
            
    return close_types, liquidities, desired_positions, found_col


def find_delta(ticker, current_status_list):
    for row in current_status_list:
        if row.get("symbol") == ticker and float(row.get("volume", 0)) > 50000:
            open_p = float(row.get("open", 1))
            if open_p > 0:
                return float(row.get("AdjClose", 0)) / open_p
    return 1.0


def find_liquidity(ticker, current_status_list):
    for row in current_status_list:
        if row.get("symbol") == ticker and float(row.get("volume", 0)) > 150000:
            return "1"
    return "UNKNOWN"


def mtm_position(ticker, current_status_list, position):
    for row in current_status_list:
        if row.get("symbol") == ticker and "AdjClose" in row:
            return round(position * float(row["AdjClose"]))
    return "UNKNOWN"


def print_correct_asset_file(
    close_types,
    position_tickers,
    liquidities,
    current_status_list,
    out_file,
    desired_positions=None,
    found_col=None,
):
    correct_asset_file = {sym: close_types[sym] for sym in close_types if sym in position_tickers}
    for ticker in position_tickers:
        if ticker not in close_types:
            delta = find_delta(ticker, current_status_list)
            correct_asset_file[ticker] = (
                "last_high_eod" if position_tickers[ticker] > 0 and delta > 0.815 else "low_close_moo"
            )
            liquidities[ticker] = find_liquidity(ticker, current_status_list)
            if liquidities[ticker] == "UNKNOWN" and out_file:
                liquidities[ticker] = "0"
                
    mtm_positions = {t: mtm_position(t, current_status_list, position_tickers[t]) for t in correct_asset_file}
    
    header = "symbol,close_type,liquid,position,mtm_dollars"
    if found_col:
        header += f",{found_col}"
        
    lines = [header]
    for t in correct_asset_file:
        row_str = f"{t},{correct_asset_file[t]},{liquidities[t]},{position_tickers[t]},{mtm_positions[t]}"
        if found_col:
            val = (desired_positions or {}).get(t, "")
            row_str += f",{val}"
        lines.append(row_str)
        
    output_content = "\n".join(lines) + "\n"
    if out_file:
        os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(output_content)
        print(f"Reconciled asset file written to {out_file}")
    else:
        print(output_content)


def check_assets(args):
    asset_dict = load_csv_or_sheet(args.file)
    close_types, liquidities, desired_positions, found_col = extract_close_types_and_liquidities(asset_dict)
    
    ib = initiate_ib(args, client_id=args.clientid, real=args.real)
    try:
        open_positions = ib.positions()
        if args.account:
            open_positions = [p for p in open_positions if p.account == args.account]
        position_tickers = {p.contract.symbol: p.position for p in open_positions if p.position != 0}
        
        current_status_list = []
        if os.path.exists(args.current_status_file):
            current_status_list = load_csv_or_sheet(args.current_status_file)
            
        print_correct_asset_file(
            close_types,
            position_tickers,
            liquidities,
            current_status_list,
            args.out_file,
            desired_positions,
            found_col,
        )
    finally:
        safe_disconnect(ib)


def main():
    parser = argparse.ArgumentParser(description="Check and reconcile open positions against asset file.")
    parser.add_argument("--file", type=str, default=DEFAULT_ASSETS_URL, help="Path or URL to asset file")
    parser.add_argument("--allow_list", type=str, default=DEFAULT_ALLOW_LIST_URL, help="Path or URL to allow list")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account")
    parser.add_argument("--account", type=str, required=False, help="Account number")
    parser.add_argument("--clientid", type=int, default=133, help="Client ID")
    parser.add_argument("--current_status_file", type=str, default=DEFAULT_CURRENT_STATUS_FILE, help="Path to current status file")
    parser.add_argument("--out_file", type=str, default=DEFAULT_OUT_FILE, help="Output file path")

    args = parser.parse_args()
    check_assets(args)


if __name__ == "__main__":
    main()
