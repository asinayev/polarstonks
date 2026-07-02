import polars as pl
from datetime import date, datetime, timedelta, timezone
import os
import sys
import logging

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import POLYGON_KEY, OUT_DIR
from src.features import calculate_session_id, add_lags_leads, add_rolling_features
from src.outputs import write_strat
from src.polygon_client import PolygonClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def clean_news(news_df):
    """Cleans news dataframe."""
    # R: single_ticker logic (if tickers list length == 1)
    # Polars: tickers is List(Utf8).
    
    if "tickers" not in news_df.columns:
        return pl.DataFrame()
        
    # Check length of tickers list
    news_df = news_df.with_columns(
        pl.col("tickers").list.len().alias("ticker_count")
    )
    
    news_df = news_df.with_columns(
        pl.when(pl.col("ticker_count") == 1)
        .then(pl.col("tickers").list.get(0))
        .otherwise(None)
        .alias("single_ticker")
    )
    
    # Explode tickers to have one row per ticker?
    # R: news = news[,.(symbol=unlist(tickers) )...]
    # Yes, it explodes tickers.
    
    news_df = news_df.explode("tickers").rename({"tickers": "symbol"})
    
    # R also keeps 'keywords' (first one).
    # We'll just keep 'keywords' if it exists (usually list).
    
    return news_df

def get_prev_day_news(client, date_val):
    """Fetches news from Polygon."""
    # R logic: 
    # if Monday (wday=2 in R? R wday: 1=Sun, 2=Mon... wait. R wday depends on locale? usually 1-7. lubridate wday(date) 1=Sun)
    # Script says: if wday(date) == 2, yesterday = date-3 (Friday).
    # if wday between 2 and 6 (Mon-Fri) -> fetch.
    # Actually R script checks if TODAY is Tue-Sat (2-6)?
    # "if(!lubridate::wday(date) %between% c(2,6)){ return(NULL) }"
    # So if Sunday (1) or Saturday (7)? No, wday(date) return 1-7.
    # If today is Monday (2), yesterday is Friday.
    # If today is Sunday (1), return NULL.
    # If today is Saturday (7), return NULL?
    # Let's assume standard logic: fetch news since "last trading close".
    
    # In python: date.weekday() -> Mon=0, Sun=6.
    
    # We want news published between yesterday 16:00 and today 07:30?
    # R: full_prevday=F, apply_=T
    # -> open = yesterday 16:00, close = today 09:30 (apply_=T)
    # The URL calls /v2/reference/news?published_utc.gt=...&lt=...
    
    # Determine "yesterday" (last trading day)
    today = date_val # Sys.Date()
    
    # If today is Mon (0), prev is Fri (-3 days)
    # If today is Tue (1), prev is Mon (-1 day)
    # If today is Sat/Sun, maybe skip? R skips Sat(7)/Sun(1) or Mon(2)-Fri(6)?
    # R: wday(Mon)=2. 2 %between% 2,6 is True.
    # R: wday(Fri)=6. 6 %between% 2,6 is True.
    # R: wday(Sat)=7. False.
    # R: wday(Sun)=1. False.
    # So it runs Mon-Fri.
    
    wd = today.weekday() # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    if wd >= 5: # Sat or Sun
        logger.info("Weekend, skipping news.")
        return pl.DataFrame()
        
    if wd == 0: # Mon
        prev_day = today - timedelta(days=3)
    else:
        prev_day = today - timedelta(days=1)
        
    # Time range: prev_day 16:00 ET to today 09:30 ET
    # Convert to UTC.
    # ET is UTC-5 (or -4). Using polygon client or manual string?
    # Let's use simple UTC assumption or pytz.
    
    # Assuming standard time (UTC-5) for simplicity or use offset-aware
    # Polygon expects UTC strings.
    
    # prev 16:00 ET -> 21:00 UTC (approx)
    # today 09:30 ET -> 14:30 UTC
    
    # R uses "America/New_York"
    
    start_dt = datetime.combine(prev_day, datetime.strptime("16:00", "%H:%M").time()).replace(tzinfo=timezone(timedelta(hours=-5))) # Approx
    end_dt = datetime.combine(today, datetime.strptime("09:30", "%H:%M").time()).replace(tzinfo=timezone(timedelta(hours=-5)))
    
    # We'll use the RESTClient list_news method if available, or manual URL.
    # client.list_news(published_utc_gt=..., published_utc_lt=...)
    
    news_items = []
    try:
        # Convert to YYYY-MM-DDTHH:MM:SSZ strings in UTC
        start_str = start_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = end_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        logger.info(f"Fetching news from {start_str} to {end_str}...")
        
        # Use list_ticker_news (which is the v2 reference news endpoint wrapper)
        for n in client.client.list_ticker_news(published_utc_gt=start_str, published_utc_lt=end_str, limit=1000):
            news_items.append({
                "id": n.id,
                "publisher_name": n.publisher.name,
                "date": n.published_utc, 
                "title": n.title,
                "author": n.author,
                "tickers": n.tickers,
                "keywords": n.keywords
            })
    except Exception as e:
        logger.error(f"Error fetching news: {e}")
        return pl.DataFrame()
        
    if not news_items:
        return pl.DataFrame()
        
    df = pl.DataFrame(news_items)
    # Clean date to just YYYY-MM-DD? R uses date=yesterday?
    # R: today_news$date=yesterday.
    # It assigns the "trading date" associated with the news (which is prev_day?)
    # Wait, R: just_news = get_prev_day_news(Sys.Date()...)
    # In R function: today_news$date = yesterday.
    # So the 'date' column in dataframe is set to prev_day (the "close" date we are comparing to?)
    
    # If we merge with prices, prices has 'date'.
    # If we use prev_day, we merge with prev_day prices.
    
    df = df.with_columns(pl.lit(prev_day).alias("date"))
    return df

def main():
    if not POLYGON_KEY:
        logger.error("POLYGONKEY not set.")
        sys.exit(1)
        
    client = PolygonClient(POLYGON_KEY)
    
    # 1. Load Prices & Snapshot
    # prices = fread('/tmp/prices.csv') (CS only presumably)
    prices_path = os.path.join(OUT_DIR, "prices.parquet")
    if not os.path.exists(prices_path):
        prices_path = os.path.join(OUT_DIR, "prices.csv")
    
    if not os.path.exists(prices_path):
        logger.error("Prices not found.")
        sys.exit(1)
        
    prices = pl.read_parquet(prices_path) if prices_path.endswith('.parquet') else pl.read_csv(prices_path)
    
    # Filter last 150 days per symbol
    prices = prices.sort(["symbol", "date"])
    # Tail 150
    # prices = prices.group_by("symbol").tail(150) # Polars has tail
    # But R: prices = prices[!is.na(close), tail(.SD,150), by=symbol]
    
    # 2. Features
    logger.info("Calculating price features...")
    prices = calculate_session_id(prices)
    prices = add_lags_leads(prices)
    prices = add_rolling_features(prices, roll_window=25, short_roll_window=5, corr_window=100)
    
    # 3. Snapshot (Trending)
    # The snapshot path should be where get_snapshot.py writes it.
    # In R it was hardcoded to /tmp/current_moves.csv, but get_snapshot.py uses /tmp/stonksanalysis/
    # Let's check for both or use a configurable path.
    
    snapshot_path = os.path.join("/tmp/stonksanalysis", "current_moves.csv")
    if not os.path.exists(snapshot_path):
        snapshot_path = "/tmp/current_moves.csv"
        
    trending_symbols = []
    
    if os.path.exists(snapshot_path):
        snapshot = pl.read_csv(snapshot_path)
        
        # R: trending=snapshot[((as.integer(Sys.time())-as.integer(updated))/60/60)<1 & overnight_delta>1.0175]
        now_ts = int(datetime.now().timestamp())
        
        if "overnight_delta" in snapshot.columns and "updated" in snapshot.columns:
            trending = snapshot.filter(
                ((now_ts - pl.col("updated")) / 3600 < 1) & 
                (pl.col("overnight_delta") > 1.0175)
            )
            trending_symbols = trending["symbol"].to_list()
    else:
        logger.warning(f"Snapshot {snapshot_path} not found. Trending list empty.")

    # 4. Fetch News
    news_df = get_prev_day_news(client, date.today())
    
    if news_df.is_empty():
        logger.info("No news.")
        return

    news_clean = clean_news(news_df)
    
    # Merge with prices
    # merge(prices, by=c('date','symbol'), all.x=T)
    # Note: prices date is Date. news date is Date (set to prev_day).
    
    news_moves = news_clean.join(prices, on=["date", "symbol"], how="left")
    
    # 5. Strategies
    
    def format_strat(df, name, price_cond=False):
        if df.is_empty():
            out = pl.DataFrame(schema={
                "symbol": pl.Utf8, "action": pl.Utf8, "order_type": pl.Utf8, 
                "time_in_force": pl.Utf8, "strike_price": pl.Float64
            })
            if price_cond: out = out.with_columns(pl.lit(None).cast(pl.Float64).alias("price_condition_min"))
        else:
            out = df.select([
                pl.col("symbol"),
                pl.lit("BUY").alias("action"),
                pl.lit("Adaptive").alias("order_type"),
                pl.lit("DAY").alias("time_in_force"),
                pl.col("close").alias("strike_price")
            ])
            if price_cond:
                out = out.with_columns(df["price_condition_min"])
                
        write_strat(out, name, base_dir=OUT_DIR, price_condition_min=price_cond)

    # A. div_news
    # title matches keywords
    # avg_delta_short < 1
    # symbol in trending
    # !is.na(single_ticker) (handled by join? clean_news creates single_ticker col)
    # volume, close filters
    
    regex = r'(dividend|repurchase|buyback|outlook|guidance|public offer|strategic)'
    
    s1 = news_moves.filter(
        pl.col("title").str.contains(regex, strict=False) & # case insensitive? polars regex is case sensitive usually. Need (?i)
        (pl.col("avg_delta_short") < 1) &
        pl.col("symbol").is_in(trending_symbols) &
        pl.col("single_ticker").is_not_null() &
        (pl.col("avg_volume") > 100000) &
        (pl.col("volume") > 100000) &
        (pl.col("close") > 6)
    ).with_columns(
        (pl.col("close") * 1.0175).alias("price_condition_min")
    ).unique(subset=["symbol"], keep="first").sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    
    # R regex ignore.case=T -> (?i)
    # Polars `str.contains("(?i)...")` works if backend supports it (Rust regex).
    
    format_strat(s1, 'div_news', price_cond=True)
    
    # B. news_trend
    # symbol in trending
    # publisher in ['The Motley Fool','GlobeNewswire Inc.']
    
    s2 = news_moves.filter(
        pl.col("symbol").is_in(trending_symbols) &
        pl.col("publisher_name").is_in(['The Motley Fool','GlobeNewswire Inc.']) &
        pl.col("single_ticker").is_not_null() &
        (pl.col("avg_volume") > 100000) &
        (pl.col("volume") > 100000) &
        (pl.col("close") > 6)
    ).with_columns(
        (pl.col("close") * 1.0175).alias("price_condition_min")
    ).unique(subset=["symbol"], keep="first").sort("close").head(1)
    
    format_strat(s2, 'news_trend', price_cond=True)
    
    # C. news_revert
    # avg_delta_short < .99
    # title matches (public offer|quarter)
    # order MKT, OPG
    
    regex_revert = r'(?i)(public offer|quarter)'
    
    s3 = news_moves.filter(
        (pl.col("avg_delta_short") < 0.99) &
        pl.col("title").str.contains(regex_revert) &
        pl.col("single_ticker").is_not_null() &
        (pl.col("avg_volume") > 100000) &
        (pl.col("volume") > 100000) &
        (pl.col("close") > 6)
    ).unique(subset=["symbol"], keep="first").sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    
    # Helper for specific strat format (MKT OPG)
    if s3.is_empty():
        out3 = pl.DataFrame(schema={"symbol": pl.Utf8, "action": pl.Utf8, "order_type": pl.Utf8, "time_in_force": pl.Utf8, "strike_price": pl.Float64})
    else:
        out3 = s3.select([
            pl.col("symbol"),
            pl.lit("BUY").alias("action"),
            pl.lit("MKT").alias("order_type"),
            pl.lit("OPG").alias("time_in_force"),
            pl.col("close").alias("strike_price")
        ])
    write_strat(out3, 'news_revert', base_dir=OUT_DIR)
    
    # Save all news (first row per symbol)
    logger.info("Writing all_news.csv...")
    all_news = news_moves.unique(subset=["symbol"], keep="first")
    
    # Convert list columns to string for CSV compatibility
    # Specifically 'keywords' is likely a list
    if "keywords" in all_news.columns:
        # Check type
        dtype = all_news.schema["keywords"]
        if isinstance(dtype, pl.List):
            all_news = all_news.with_columns(
                pl.col("keywords").list.join(",").alias("keywords")
            )
            
    all_news.write_csv(os.path.join(OUT_DIR, "all_news.csv"))
    logger.info("Done.")

if __name__ == "__main__":
    main()
