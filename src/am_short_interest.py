import polars as pl
import os
import sys
import logging
from datetime import date, timedelta

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY, OUT_DIR
from src.features import (
    calculate_session_id,
    add_lags_leads,
    add_rolling_features,
    calculate_orders,
    rally,
    get_last_full_date,
    filter_to_last_full_date,
)
from src.outputs import format_strat
from src.polygon_client import PolygonClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def subtract_business_days(dates_series, n):
    """Subtracts n business days (skipping weekends). matches R's subtract_weekdays logic."""
    # R logic: 
    # weeks = n %/% 5
    # remainder = n %% 5
    # new_dates = dates - (weeks * 7)
    # Then loop remainder: new_dates = new_dates - 1, if Sat/Sun roll back further.
    
    weeks = n // 5
    rem = n % 5
    
    res = dates_series - timedelta(days=weeks * 7)
    
    for _ in range(rem):
        res = res - timedelta(days=1)
        # Polars weekday: 1=Mon, 7=Sun
        # R POSIXlt wday: 0=Sun, 6=Sat
        # R logic: 
        # is_sun = (wd == 0) -> -2
        # is_sat = (wd == 6) -> -1
        
        res = pl.when(res.dt.weekday() == 7).then(res - timedelta(days=2)) \
                .when(res.dt.weekday() == 6).then(res - timedelta(days=1)) \
                .otherwise(res)
        
    return res
        
    return res

def add_short_interest(client, df):
    """Fetches and merges short interest data."""
    logger.info("Fetching short interest...")
    
    shorts = []
    try:
        # Fetch with a large limit to get everything, as R does (limit=50000)
        # The list_short_interest iteration handles paging.
        for s in client.client.list_short_interest(limit=5000): 
             shorts.append({
                 "symbol": s.ticker,
                 "settlement_date": s.settlement_date, 
                 "short_interest": s.short_interest,
                 "days_to_cover": getattr(s, 'days_to_cover', None)
             })
             if len(shorts) >= 50000: # Safety cap
                 break
                 
    except Exception as e:
        logger.error(f"Error fetching short interest: {e}")
        return df

    if not shorts:
        logger.warning("No short interest data found.")
        return df
        
    shorts_df = pl.DataFrame(shorts)
    
    # Cast dates and ensure unique per symbol/date
    shorts_df = shorts_df.with_columns(
        pl.col("settlement_date").str.to_date()
    ).unique(subset=["symbol", "settlement_date"])
    
    # Calculate join_date on prices df using EXACT R logic
    df = df.with_columns(
        subtract_business_days(pl.col("date"), 8).alias("join_date")
    )
    
    # Join
    # R: on = .(symbol, settlement_date = join_date), roll = TRUE
    # This matches to the closest settlement_date <= join_date.
    
    # Sort for join_asof: must be sorted by 'by' then 'on' column
    df = df.sort(["symbol", "join_date"])
    shorts_df = shorts_df.sort(["symbol", "settlement_date"])
    
    df = df.join_asof(
        shorts_df, 
        left_on="join_date", 
        right_on="settlement_date", 
        by="symbol", 
        strategy="backward",
        check_sortedness=False
    )
    
    return df

def main():
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set.")
        sys.exit(1)
        
    client = PolygonClient(POLYGON_KEY)

    # 1. Load Data
    prices_path = os.path.join(OUT_DIR, "prices.parquet")
    if not os.path.exists(prices_path):
        prices_path = os.path.join(OUT_DIR, "prices.csv")
    
    if not os.path.exists(prices_path):
        logger.error("Prices not found.")
        sys.exit(1)
        
    prices = pl.read_parquet(prices_path) if prices_path.endswith('.parquet') else pl.read_csv(prices_path)
    
    # 2. Add Short Interest
    prices = add_short_interest(client, prices)

    # Determine last date with full data and filter out any trailing partial dates
    last_full_date = get_last_full_date(prices)
    logger.info(f"Last date with full data: {last_full_date}")
    if last_full_date is not None:
        prices = prices.filter(pl.col("date") <= last_full_date)
        max_date = last_full_date
    else:
        max_date = prices.select(pl.col("date").max()).item()
    logger.info(f"Target date for short interest strategies: {max_date}")

    # 3. Features
    # R: prices = only_passing(prices, min_volume=0, min_close=0, last_n = 150)
    # Filter for symbols that exist on max_date and have volume/close > 0
    passing_symbols = prices.filter(
        (pl.col("date") == max_date) & 
        (pl.col("close") > 0) & 
        (pl.col("volume") > 0)
    ).select("symbol").unique()["symbol"]
    
    prices = prices.filter(pl.col("symbol").is_in(passing_symbols.to_list()))
    
    # Sort and take last 150
    prices = prices.sort(["symbol", "date"])
    prices = prices.group_by("symbol").tail(150).sort(["symbol", "date"])
    
    prices = calculate_session_id(prices)
    prices = add_lags_leads(prices)
    prices = add_rolling_features(prices, roll_window=25, short_roll_window=5, corr_window=100)
    prices = rally(prices)
    prices = calculate_orders(prices)

    # 4. Strategy: short_no_interest
    # date==max
    # vp_order < 500
    # close > 7
    # avg_delta > 1.01
    # avg_delta_short > 1.03
    # short_interest > 100000
    # days_to_cover == 1 (approx? R says ==1)
    
    # Check if short columns exist
    if "short_interest" not in prices.columns:
        logger.warning("Short interest columns missing. Skipping strategy.")
        return

    s1 = prices.filter(
        (pl.col("date") == max_date) &
        (pl.col("vp_order") < 500) &
        (pl.col("close") > 7) &
        (pl.col("avg_delta") > 1.01) &
        (pl.col("avg_delta_short") > 1.03) &
        (pl.col("short_interest") > 100000) &
        (pl.col("days_to_cover").round(0) == 1)
    ).sort(
        ["date", "day_drop_norm", "sd_from0"], 
        descending=[False, False, False]
    ).sort("volume", descending=False).head(1)
    
    format_strat(s1, 'short_no_interest', action='SELL')
    logger.info("Done.")

if __name__ == "__main__":
    main()
