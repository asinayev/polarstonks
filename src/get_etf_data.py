import polars as pl
from datetime import date, timedelta
import os
import sys
import logging
import concurrent.futures

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY, OUT_DIR
from src.polygon_client import PolygonClient
from src.get_data import fetch_ticker_details, fetch_ticker_history

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_etf_data():
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set.")
        sys.exit(1)

    client = PolygonClient(POLYGON_KEY)
    
    # 1. Get ETF list (ETF and ETV)
    # R: stocklist_from_polygon(..., ticker_type='ETF') rbind ... 'ETV'
    # Date: Sys.Date()-1 (yesterday)
    
    date_val = date.today() - timedelta(days=1)
    
    logger.info("Fetching ETF list...")
    etfs = client.get_stock_list(date_active=date_val, market='stocks', type='ETF', limit=1000)
    etvs = client.get_stock_list(date_active=date_val, market='stocks', type='ETV', limit=1000)
    
    if etfs.is_empty() and etvs.is_empty():
        logger.error("No ETFs found.")
        sys.exit(1)
        
    tickers_df = pl.concat([etfs, etvs]) if not etvs.is_empty() else etfs
    if not etfs.is_empty() and not etvs.is_empty():
        tickers_df = tickers_df.unique(subset=["symbol"])
        
    logger.info(f"Found {len(tickers_df)} ETFs/ETVs.")
    
    # Load etf_list.csv for category/leverage
    # Path relative to src: ../other_datasources/etf_list.csv
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    etf_list_path = os.path.join(repo_root, "other_datasources", "etf_list.csv")
    if os.path.exists(etf_list_path):
        # Specify schema to avoid inference errors on mixed types
        # leverage appears to be numeric (float/int)
        try:
            etf_meta = pl.read_csv(etf_list_path, infer_schema_length=10000, ignore_errors=True)
            # Or cast leverage to Float64 explicitly
            etf_meta = etf_meta.with_columns(pl.col("leverage").cast(pl.Float64, strict=False))
        except Exception as e:
            logger.error(f"Error reading etf_list.csv: {e}")
            # Fallback to empty
            etf_meta = pl.DataFrame({"ticker": [], "category": [], "leverage": []})

        # merge(etf_list[,.(ticker,category,leverage)], by='ticker', all.x=T)
        # Select relevant cols
        etf_meta = etf_meta.select([
            pl.col("ticker").alias("symbol"), 
            pl.col("category"), 
            pl.col("leverage")
        ])
        
        tickers_df = tickers_df.join(etf_meta, on="symbol", how="left")
    else:
        logger.error(f"ETF list not found at {etf_list_path}. Cannot proceed without categories and leverage data.")
        sys.exit(1)

    # 2. Fetch History (3 years back)
    # R: start_date = Sys.Date()-3*365
    end_date = date.today() + timedelta(days=2) # Matching R's Sys.Date()+2
    start_date = date.today() - timedelta(days=3*365)
    
    all_prices = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ticker_history, client, row['symbol'], start_date, end_date): row['symbol'] for row in tickers_df.to_dicts()}
        
        for future in concurrent.futures.as_completed(futures):
            try:
                data = future.result()
                if data is not None and not data.is_empty():
                    all_prices.append(data)
            except Exception as e:
                pass

    if not all_prices:
        logger.error("No ETF price data fetched.")
        sys.exit(1)

    prices_df = pl.concat(all_prices)
    
    # Filter trailing partial dates (e.g. partial weekend prints) before returning
    from src.features import filter_to_last_full_date, get_last_full_date
    last_full_date = get_last_full_date(prices_df)
    if last_full_date is not None:
        logger.info(f"Last date with full ETF data in fetched history: {last_full_date}")
        prices_df = filter_to_last_full_date(prices_df)

    # Merge with ticker details (category, leverage, name)
    # prices = prices[...] %>% merge(stocklist[,.(symbol=ticker, name, category, leverage)], all.x=T)
    
    # Ensure columns exist in tickers_df
    cols_to_join = ["symbol", "name", "category", "leverage"]
    available_cols = [c for c in cols_to_join if c in tickers_df.columns]
    
    prices_df = prices_df.join(tickers_df.select(available_cols), on="symbol", how="left")
    
    return prices_df

if __name__ == "__main__":
    # Test fetch
    df = get_etf_data()
    print(df.head())
    df.write_parquet(os.path.join(OUT_DIR, "prices_etf.parquet"))
