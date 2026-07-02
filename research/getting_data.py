import polars as pl
from datetime import date, timedelta
import os
import sys
import logging
import concurrent.futures
import argparse

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY
from src.polygon_client import PolygonClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_ticker_history(client, ticker, start_date, end_date):
    """Fetches adjusted history + unadjusted close for a single ticker.
    Mirrors R: stock_history(..., get_unadjusted=T) which makes a second
    API call with adjusted=false and merges the unadjusted close in.
    """
    try:
        # Adjusted bars (primary)
        df = client.get_history(ticker, start_date, end_date, adjusted=True)
        if df.is_empty():
            return None
        
        # Ensure consistent schema
        df = df.with_columns([
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64), 
            pl.col("vwap").cast(pl.Float64),
            pl.col("transactions").cast(pl.Int64)
        ])
        
        # Unadjusted bars — just need the close (unadjClose)
        unadj = client.get_history(ticker, start_date, end_date, adjusted=False)
        if not unadj.is_empty():
            unadj = unadj.select([
                pl.col("date"),
                pl.col("close").cast(pl.Float64).alias("unadjClose")
            ])
            df = df.join(unadj, on="date", how="left")
        else:
            df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("unadjClose"))
        
        return df.with_columns(pl.lit(ticker).alias("symbol"))
    except Exception as e:
        # logger.error(f"Error fetching {ticker}: {e}")
        return None

def fetch_ticker_details(client, ticker, date_val=None):
    """Fetches details for a single ticker."""
    return client.get_ticker_details(ticker, date_val=date_val)

def process_year(client, year: int):
    logger.info(f"====== Starting processing for year {year} ======")
    
    # Active data reference date: end of year, or today if current year
    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    if year == date.today().year:
        end_date = date.today()
        # active date for list
        date_val = end_date - timedelta(days=5) 
    else:
        date_val = end_date
    
    logger.info(f"[{year}] Fetching active stock list as of {date_val}...")
    tickers_df = client.get_stock_list(date_active=date_val, market='stocks', type='', limit=1000)
    
    if tickers_df.is_empty():
        logger.error(f"[{year}] No tickers found. Skipping.")
        return
        
    logger.info(f"[{year}] Found {len(tickers_df)} active tickers.")
    
    if "locale" in tickers_df.columns:
        tickers_df = tickers_df.filter(pl.col("locale") == "us")
        logger.info(f"[{year}] Remaining US tickers: {len(tickers_df)}")
        
    logger.info(f"[{year}] Fetching ticker details (market_cap)...")
    details_list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ticker_details, client, row['symbol'], date_val=date_val): row['symbol'] for row in tickers_df.to_dicts()}
        for future in concurrent.futures.as_completed(futures):
            try:
                d = future.result()
                if d:
                    details_list.append(d)
            except Exception:
                pass
    
    if details_list:
        details_df = pl.DataFrame(details_list)
        # Join details to tickers_df
        tickers_df = tickers_df.join(details_df, on='symbol', how='left')
    else:
        logger.warning(f"[{year}] No details fetched.")
        tickers_df = tickers_df.with_columns(pl.lit(None).cast(pl.Float64).alias("market_cap"))

    logger.info(f"[{year}] Fetching history for each ticker from {start_date} to {end_date}...")
    
    all_prices = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_ticker_history, client, row['symbol'], start_date, end_date): row['symbol'] for row in tickers_df.to_dicts()}
        
        count = 0
        total = len(futures)
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            count += 1
            if count % 1000 == 0:
                logger.info(f"[{year}] Progress: {count}/{total} tickers downloaded...")
            try:
                data = future.result()
                if data is not None and not data.is_empty():
                    all_prices.append(data)
            except Exception as e:
                pass 
                
    if not all_prices:
        logger.error(f"[{year}] No price data fetched. Skipping.")
        return

    prices_df = pl.concat(all_prices)
    
    # Merge with details directly on the 'symbol'
    prices_df = prices_df.join(tickers_df, on='symbol', how='left')
    
    # Optional renaming to mimic R
    if "symbol" in prices_df.columns and "stock" not in prices_df.columns:
        prices_df = prices_df.rename({"symbol": "stock"})
        
    out_dir = os.path.expanduser("~/data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{year}.csv.gz")

    if os.path.exists(out_path):
        logger.info(f"[{year}] Output {out_path} already exists! Skipping.")
        return

    logger.info(f"[{year}] Saving {len(prices_df)} rows to {out_path}...")
    try:
        # Attempt to natively gzip the csv
        prices_df.write_csv(out_path, compression="gzip")
    except TypeError:
        # Fallback for old polars versions
        prices_df.write_csv(out_path, check_extension=False)
        
    logger.info(f"[{year}] Finished {year}.")


def main():
    parser = argparse.ArgumentParser(description="Fetch historical stock data for specific years from Polygon.io")
    parser.add_argument("years", nargs="+", type=int, help="Years to fetch (e.g. 2025 2026)")
    args = parser.parse_args()
    
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set in .env. Exiting.")
        sys.exit(1)

    client = PolygonClient(POLYGON_KEY)
    
    for year in args.years:
        process_year(client, year)
        
if __name__ == "__main__":
    main()
