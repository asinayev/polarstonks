"""View and export executed IBKR trades."""

import os
import sys
import ast
import argparse
import datetime
from typing import List, Dict, Tuple, Optional
import pandas as pd

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import OUT_DIR
from src.execution.connection import initiate_ib, safe_disconnect
from src.execution.trade_logger import log_trade
from ib_async import IB, Trade


def get_trade_info(trade: Trade) -> List[str]:
    today = datetime.date.today().strftime("%m/%d/%Y")
    local_symbol = trade.contract.localSymbol
    action = trade.order.action
    total_shares = sum([fill.execution.shares for fill in trade.fills])
    price = str(sum([f.execution.price * f.execution.shares for f in trade.fills]) / total_shares) if total_shares > 0 else "0"
    shares = str(total_shares)
    status = str(trade.orderStatus.status)
    commission = str(sum([fill.commissionReport.commission for fill in trade.fills]))
    num_fills = str(len(trade.fills))
    time = trade.fills[0].execution.time.strftime("%H:%M:%S") if trade.fills else ""
    return [today, local_symbol, action, price, "", shares, status, num_fills, time, commission]


def log_json(ib: IB, args):
    out_file = args.out_file or os.path.join(OUT_DIR, "trade_logs.json")
    for t in ib.trades():
        if not args.account or t.order.account == args.account:
            log_trade(t, "", out_file, {}, ib)


def get_opens_and_closes(ib: IB, args) -> Tuple[List[List[str]], Dict[str, List[str]]]:
    opens = []
    closes = {}
    for t in ib.trades():
        if args.account and t.order.account != args.account:
            continue
        if len(t.fills) > 0 and all([f.commissionReport.realizedPNL == 0 for f in t.fills]):
            opens.append(get_trade_info(t))
        if len(t.fills) > 0 and any([f.commissionReport.realizedPNL != 0 for f in t.fills]):
            trade_info = get_trade_info(t)
            closes[t.contract.localSymbol] = trade_info
    return opens, closes


def pop_off_trade(order_stocks: pd.DataFrame, trade: List[str]) -> str:
    if order_stocks.empty:
        return ""
    matched = order_stocks[(order_stocks.symbol == trade[1]) & (abs(order_stocks.quantity - float(trade[5])) < 1)]
    if not matched.empty:
        first_matched = matched.index[0]
        matched_strat = order_stocks.loc[first_matched, "trade_reason"]
        if "/" in str(matched_strat):
            matched_strat = str(matched_strat).split("/")[-1].split(".")[0]
        order_stocks.drop(first_matched, inplace=True)
        return str(matched_strat)
    return ""


def get_formatted_trade(t: List[str], closes: Dict[str, List[str]], order_stocks: pd.DataFrame) -> str:
    close_price = closes.get(t[1], ["", "", "", ""])[3]
    t[2], t[3] = (str(t[3]), str(close_price)) if t[2] == "BUY" else (str(close_price), str(t[3]))
    t[4] = pop_off_trade(order_stocks, t)
    t.append("more closed than opened" if t[1] in closes and float(t[5]) < float(closes[t[1]][5]) else "")
    return ",".join(t)


def print_trades(opens, closes, order_stocks, out_file_path=None):
    out = open(out_file_path, "w") if out_file_path else sys.stdout
    try:
        for t in opens:
            print(get_formatted_trade(t, closes, order_stocks), file=out)
        print("#############################", file=out)
        for sym in closes:
            print(f"{sym},{closes[sym]}, CLOSE TRADE", file=out)
    finally:
        if out_file_path and out is not sys.stdout:
            out.close()


def generate_trades_report(
    real: bool = True,
    account: Optional[str] = None,
    file_type: str = "txt",
    out_file: Optional[str] = None,
    client_id: int = 14,
):
    ib = initiate_ib(real=real, client_id=client_id)
    try:
        class ArgsDummy:
            pass
        d_args = ArgsDummy()
        d_args.real = real
        d_args.account = account
        d_args.out_file = out_file
        d_args.file_type = file_type

        if file_type == "json":
            log_json(ib, d_args)
        else:
            opens, closes = get_opens_and_closes(ib, d_args)
            order_logs_path = os.path.join(OUT_DIR, "order_logs.json")
            order_stocks = pd.DataFrame()
            if os.path.exists(order_logs_path):
                with open(order_logs_path, "r", encoding="utf-8") as f:
                    lines = [ast.literal_eval(line.strip()) for line in f if line.strip()]
                    if lines:
                        order_stocks = pd.DataFrame(lines)
            print_trades(opens, closes, order_stocks, out_file)
    finally:
        safe_disconnect(ib)


def main():
    parser = argparse.ArgumentParser(description="View and format executed IBKR trades.")
    parser.add_argument("--real", action="store_true", default=True, help="Use real account port")
    parser.add_argument("--paper", dest="real", action="store_false", help="Use paper account port")
    parser.add_argument("--account", type=str, required=False, help="IBKR Account number")
    parser.add_argument("--out_file", type=str, required=False, help="Output file path")
    parser.add_argument("--file_type", type=str, choices=["txt", "json"], default="txt", help="Output format")
    parser.add_argument("--clientid", type=int, default=14, help="Client ID")

    args = parser.parse_args()
    generate_trades_report(
        real=args.real,
        account=args.account,
        file_type=args.file_type,
        out_file=args.out_file,
        client_id=args.clientid,
    )


if __name__ == "__main__":
    main()
