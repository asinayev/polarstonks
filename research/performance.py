import polars as pl
import numpy as np
from datetime import datetime

def calculate_drawdown(outcomes: pl.Series) -> pl.Series:
    """
    Calculates the path-dependent drawdown for a series of returns.
    Mimics the R implementation:
    drawdown = function(x){
      Reduce(function(.x,.y){
        if((.x+.y)>0){0} else {.x+.y}
      }, x, 0, accumulate=T)[-1]
    }
    """
    # Polars doesn't have a direct Reduce+accumulate for this specific logic
    # We use a python loop for the path-dependency, or cumulative sum with peak tracking
    # However, the R logic is: current_drawdown = min(0, prev_drawdown + current_return)
    
    returns = outcomes.to_list()
    drawdowns = []
    current_dd = 0.0
    for r in returns:
        current_dd = min(0.0, current_dd + r)
        drawdowns.append(current_dd)
    
    return pl.Series("drawdown", drawdowns)

def evaluate_performance(
    df: pl.DataFrame,
    date_col: str = "date",
    outcome_col: str = "outcome",
    symbol_col: str = "symbol",
    sell_date_col: str = "sell_date",
    hold_max: int = None,
    buy_per_day_max: int = 5,
    hold_same_max: int = None
) -> pl.DataFrame:
    """
    Evaluates strategy performance with portfolio constraints.
    Returns a daily summary DataFrame.
    """
    if df.is_empty():
        # Return empty df with expected columns
        return pl.DataFrame(schema={
            "date": df.schema.get("date", pl.Date),
            "outcome": pl.Float64,
            "trades": pl.Int64,
            "n_sold": pl.Int64,
            "drawdown": pl.Float64,
            "n_held": pl.Int64,
            "drawdown_i": pl.Int32
        })

    # Ensure correct types and ordering
    results = df.select([
        pl.col(date_col).alias("date"),
        pl.col(outcome_col).alias("outcome"),
        pl.col(symbol_col).alias("symbol"),
        pl.col(sell_date_col).alias("sell_date")
    ]).drop_nulls().sort("date")

    # Track held symbols per date for hover info
    date_held_symbols = {}

    if hold_max or buy_per_day_max or hold_same_max:
        results = results.with_row_index("row_id")
        to_include = [False] * len(results)
        
        # Track held positions: list of (symbol, sell_date)
        held_positions = []
        
        # Group by date for simulation
        dates = results["date"].unique().sort()
        
        results_list = results.to_dicts()
        # Index rows by date for faster lookup
        from collections import defaultdict
        date_to_rows = defaultdict(list)
        for i, row in enumerate(results_list):
            date_to_rows[row["date"]].append(i)

        for current_date in dates:
            # 1. Update Portfolio: "Sell" anything due before today
            held_positions = [pos for pos in held_positions if pos["sell_date"] >= current_date]
            
            # 2. Get today's candidates
            candidate_indices = date_to_rows[current_date]
            
            # 3. Decision logic
            buys_made_today = 0
            for idx in candidate_indices:
                candidate = results_list[idx]
                
                # Portfolio capacity check
                if hold_max and len(held_positions) >= hold_max:
                    break
                    
                # Daily buy limit check
                if buy_per_day_max and buys_made_today >= buy_per_day_max:
                    break
                    
                # Max same symbol check
                if hold_same_max:
                    current_symbol_count = sum(1 for pos in held_positions if pos["symbol"] == candidate["symbol"])
                    if current_symbol_count >= hold_same_max:
                        continue
                
                # If all checks pass
                to_include[idx] = True
                buys_made_today += 1
                held_positions.append({
                    "symbol": candidate["symbol"],
                    "sell_date": candidate["sell_date"]
                })
            
            # Record held symbols snapshot after today's buys
            date_held_symbols[current_date] = ", ".join(sorted(pos["symbol"] for pos in held_positions))
        
        results = results.with_columns(pl.Series("to_include", to_include)).filter(pl.col("to_include")).drop(["to_include", "row_id"])

    # Daily aggregation
    daily = results.group_by("date").agg([
        pl.col("outcome").sum().alias("outcome"),
        pl.len().cast(pl.Int64).alias("trades")
    ])
    
    # Calculate n_sold per date (based on sell_date)
    sold_counts = results.group_by("sell_date").agg(pl.len().cast(pl.Int64).alias("n_sold")).rename({"sell_date": "date"})
    
    daily = daily.join(sold_counts, on="date", how="full")
    
    # After full join, 'date' might be null in rows only in sold_counts (if not coalesced automatically)
    # or other columns might be null.
    # We use coalesce if date_right exists, or just fill_null for metrics.
    if "date_right" in daily.columns:
        daily = daily.with_columns(pl.coalesce(["date", "date_right"]).alias("date")).drop("date_right")
    
    daily = daily.sort("date").fill_null(0)
    
    # Metrics calculation
    daily = daily.with_columns([
        pl.col("outcome").map_batches(calculate_drawdown).alias("drawdown"),
        (pl.col("trades") - pl.col("n_sold")).cum_sum().alias("n_held")
    ])
    
    # drawdown_i: group indices for drawdown periods (where drawdown returned to 0)
    # Equivalent to R: cumsum(drawdown==0)
    daily = daily.with_columns(
        (pl.col("drawdown") == 0).cast(pl.Int32).cum_sum().alias("drawdown_i")
    )
    
    # Add held symbols info if tracked
    if date_held_symbols:
        # For sell-only dates not in date_held_symbols, propagate from simulation
        # by replaying the held state forward
        all_dates = daily["date"].to_list()
        held_symbols_list = []
        last_known = ""
        for d in all_dates:
            if d in date_held_symbols:
                last_known = date_held_symbols[d]
            held_symbols_list.append(last_known)
        daily = daily.with_columns(pl.Series("held_symbols", held_symbols_list))
    else:
        # No constraints applied — every signal was taken, build from results
        symbols_by_date = results.group_by("date").agg(
            pl.col("symbol").sort_by("symbol").str.concat(", ").alias("held_symbols")
        )
        daily = daily.join(symbols_by_date, on="date", how="left").with_columns(
            pl.col("held_symbols").fill_null("")
        )
    
    return daily

def summarize_performance(daily: pl.DataFrame, results_orig: pl.DataFrame) -> pl.DataFrame:
    """Provides a yearly summary of performance."""
    if daily.is_empty():
        return pl.DataFrame()
        
    # Yearly trade counts and average returns from original results
    yearly_trades = (
        results_orig.group_by(pl.col("date").dt.year().alias("year"))
        .agg([
            pl.col("outcome").mean().alias("avg_return"),
            pl.len().alias("total_trades")
        ])
    )

    summary = daily.group_by(pl.col("date").dt.year().alias("year")).agg([
        pl.col("drawdown").min().alias("max_drawdown"),
        pl.col("outcome").sum().alias("total_return"),
        pl.col("date").filter(pl.col("trades") > 0).n_unique().alias("days_traded"),
        pl.col("n_held").max().alias("max_held")
    ])
    
    return summary.join(yearly_trades, on="year").select([
        "year", "avg_return", "max_drawdown", "total_return", "total_trades", "days_traded", "max_held"
    ]).sort("year")
