import polars as pl
from datetime import date, timedelta
import os
import sys
import logging
import concurrent.futures

# Add parent dir to path if needed for config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY, OUT_DIR
from src.polygon_client import PolygonClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fetch_ticker_history(client, ticker, start_date, end_date):
    """Fetches history for a single ticker."""
    try:
        df = client.get_history(ticker, start_date, end_date)
        if df.is_empty():
            return None
        
        # Ensure consistent schema
        df = df.with_columns([
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64), # Float to avoid Int/Float mismatch
            pl.col("vwap").cast(pl.Float64),
            pl.col("transactions").cast(pl.Int64)
        ])
        
        return df.with_columns(pl.lit(ticker).alias("symbol"))
    except Exception as e:
        logger.error(f"Error fetching {ticker}: {e}")
        return None

def fetch_ticker_details(client, ticker, date_val=None):
    """Fetches details for a single ticker."""
    return client.get_ticker_details(ticker, date_val=date_val)

def main():
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set. Exiting.")
        sys.exit(1)

    client = PolygonClient(POLYGON_KEY)
    
    # 1. Get list of active stocks
    # Match R logic: active date 30 days ago
    date_val = date.today() - timedelta(days=30)
    logger.info(f"Fetching active stock list as of {date_val}...")
    tickers_df = client.get_stock_list(date_active=date_val, market='stocks', type='CS', limit=1000)
    
    if tickers_df.is_empty():
        logger.error("No tickers found. Exiting.")
        sys.exit(1)
        
    logger.info(f"Found {len(tickers_df)} active tickers.")
    
    # Filter for US locale if R script implicitly had that
    # R output shows 'us' in locale column.
    if "locale" in tickers_df.columns:
        logger.info("Filtering for locale='us'...")
        tickers_df = tickers_df.filter(pl.col("locale") == "us")
        logger.info(f"Remaining tickers: {len(tickers_df)}")
        
    # 1b. Fetch details (market_cap)
    logger.info("Fetching ticker details (market_cap)...")
    # Using same date_val for consistency with R script (stock_deets call passes date)
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
        logger.warning("No details fetched. Strategies relying on market_cap will fail.")
        # Add null market_cap column to avoid error
        tickers_df = tickers_df.with_columns(pl.lit(None).cast(pl.Float64).alias("market_cap"))

    # 2. Fetch history for each ticker (5 years back)
    end_date = date.today()
    start_date = end_date - timedelta(days=5*365)
    
    all_prices = []
    
    # Use ThreadPoolExecutor for concurrency
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ticker_history, client, row['symbol'], start_date, end_date): row['symbol'] for row in tickers_df.to_dicts()}
        
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                data = future.result()
                if data is not None and not data.is_empty():
                    all_prices.append(data)
            except Exception as e:
                logger.error(f"Worker error for {ticker}: {e}")
                
    if not all_prices:
        logger.error("No price data fetched. Exiting.")
        sys.exit(1)

    # 3. Combine and save
    prices_df = pl.concat(all_prices)
    
    # Filter trailing partial dates (e.g. partial weekend prints) before saving
    from src.features import filter_to_last_full_date, get_last_full_date
    last_full_date = get_last_full_date(prices_df)
    if last_full_date is not None:
        logger.info(f"Last date with full data in fetched history: {last_full_date}")
        prices_df = filter_to_last_full_date(prices_df)

    # Join with ticker details (name, market cap, etc.) if needed?
    # R script merges: prices = prices[...] %>% merge(stocklist, by.x='symbol', by.y='ticker')
    # So we should join tickers_df.
    
    # Rename 'symbol' in tickers_df to match if needed, but both have 'symbol' now (fetch_ticker_history adds it)
    # tickers_df has 'symbol', 'name', etc.
    
    prices_df = prices_df.join(tickers_df, on='symbol', how='left')
    
    # Save to CSV as requested (and maybe Parquet for speed)
    output_path = os.path.join(OUT_DIR, 'prices.csv')
    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR)

    logger.info(f"Saving {len(prices_df)} rows to {output_path}...")
    prices_df.write_csv(output_path)
    
    # Also save parquet for internal use
    prices_df.write_parquet(os.path.join(OUT_DIR, 'prices.parquet'))
    logger.info("Done.")

if __name__ == "__main__":
    main()
