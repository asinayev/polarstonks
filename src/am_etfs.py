import polars as pl
import os
import sys
import logging
from datetime import date

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import OUT_DIR
from src.features import (
    calculate_session_id,
    add_lags_leads,
    add_rolling_features,
    calculate_orders,
    rally,
    matching_pairs_for_year,
    add_etf_specific_features,
    get_last_full_date,
    filter_to_last_full_date,
)
from src.outputs import format_strat
from src.get_etf_data import get_etf_data

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    logger.info("Fetching ETF data...")
    prices = get_etf_data()
    
    if prices.is_empty():
        logger.error("No ETF data.")
        sys.exit(1)

    # Determine last date with full data and filter out any trailing partial dates
    last_full_date = get_last_full_date(prices)
    logger.info(f"Last date with full ETF data: {last_full_date}")
    if last_full_date is not None:
        prices = prices.filter(pl.col("date") <= last_full_date)
        max_date = last_full_date
    else:
        max_date = prices.select(pl.col("date").max()).item()
    logger.info(f"Target date for ETF strategies: {max_date}")
    
    # Filter universe
    valid_symbols = prices.filter(
        (pl.col("date") == max_date) &
        (pl.col("volume") > 75000) &
        (pl.col("close") > 7)
    ).select("symbol").unique()
    
    prices = prices.join(valid_symbols, on="symbol", how="inner")
    
    if prices.is_empty():
        logger.error("No ETFs passed filters.")
        sys.exit(1)

    # 3. Features
    logger.info("Calculating features...")
    prices = calculate_session_id(prices)
    prices = add_lags_leads(prices)
    prices = add_rolling_features(prices, roll_window=25, short_roll_window=5, corr_window=100)
    prices = rally(prices)
    
    if "market_cap" not in prices.columns:
        prices = prices.with_columns(pl.lit(0).alias("market_cap"))
    
    prices = calculate_orders(prices) 
    
    # ETF Specific Features
    prices = add_etf_specific_features(prices)
    
    # 4. Strategies
    logger.info("Applying strategies...")
    
    today_prices = prices.filter(pl.col("date") == max_date)
    
    # common formatting args for ETFs
    f_args = {"order_type": "Adaptive", "time_in_force": "DAY"}

    # 1. rally_etfs
    s1 = today_prices.filter(
        (pl.col("volume") > 1000000) &
        (pl.col("close") > 7) &
        (pl.col("category") == 'equity basket') &
        (pl.col("close") < pl.col("lag1high")) &
        (pl.col("sell_rally_day") > 10) &
        (pl.col("sd_from0") > 0.015)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s1, 'rally_etfs', **f_args)

    # 2. revert_etfs
    s2 = today_prices.filter(
        (pl.col("volume") > 1000000) &
        (pl.col("close") > 7) &
        pl.col("category").is_in(['equity basket','physical commodities']) &
        (((pl.col("high") - pl.col("close")) / pl.col("avg_range")) > 1) &
        (pl.col("avg_delta") < (1 - 0.005 * pl.col("leverage").abs()))
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s2, 'revert_etfs', **f_args)

    # 3. corr_long_etfs
    s3 = today_prices.filter(
        (pl.col("volume") > 1000000) &
        (pl.col("close") > 7) &
        pl.col("category").is_in(['equity basket','physical commodities']) &
        (pl.col("avg_delta_short") < 0.975) &
        (pl.col("lagging_corr_long") > 0.7)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s3, 'corr_long_etfs', **f_args)

    # 4. drop_etfs
    s4 = today_prices.filter(
        (pl.col("volume") > 1000000) &
        (pl.col("close") > 7) &
        pl.col("category").is_in(['equity basket','physical commodities']) &
        (((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low"))) < 0.15) &
        ((pl.col("MACD") / pl.col("MACD_slow")) < 0.98)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s4, 'drop_etfs', **f_args)

    # 5. deviant_etfs
    s5 = today_prices.filter(
        (pl.col("volume") > 500000) &
        (pl.col("close") > 7) &
        (pl.col("category") == 'equity basket') &
        ((pl.col("lag1close") - pl.col("close")) > (pl.col("avg_range") * 0.25))
    ).sort("sd_from0", descending=True).head(1)
    format_strat(s5, 'deviant_etfs', **f_args)

    # 6. arb_etfs
    reference_etfs = ['ACWI','AGG','DIA','EEM','EFA','EWC','EWJ','EWY','EWZ','FXI','HYG','GDX','GDXJ',
                      'IAU','IBB','IEI','IJR','ITA','IWM','IWP','IWO','IWR','IYF','IYR','IYW',
                      'KRE','LQD','MBB','QQQ','SHY','SJNK','SLQD','SLV','SLVP','SOXX','SPHB','SPY',
                      'SPYG','STPZ','TIP','TLT','UNG','USIG','USO','VBR','VDC','VEA','VGK','VIXY',
                      'VNQ','VTV','VXUS','XBI','XLB','XLE','XLF','XLI','XLK','XLU','XLV','XLY','XOP']
    
    arb_universe = prices.filter(pl.col("category").is_in(['equity basket', 'physical commodities']))
    yr = max_date.year
    
    logger.info("Calculating arbitrage pairs...")
    pairs_df = matching_pairs_for_year(yr, arb_universe, reference_etfs)
    
    if not pairs_df.is_empty():
        arb_today = pairs_df.filter(
            (pl.col("date") == max_date) &
            (pl.col("volume") > 500000) &
            (pl.col("close") > 7) &
            (pl.col("avg_delta_short") < 1) &
            (pl.col("rsq") > 0.98) &
            ((pl.col("mult") - pl.col("mult").round(0)).abs() < 0.15)
        ).with_columns(
            (
                1 - (pl.col("close")/pl.col("lag1close")) - 
                ((pl.col("reference_delta") - 1) * pl.col("mult").round(0))
            ).abs().alias("arb_dev")
        ).filter(
            pl.col("arb_dev") > 0.0075
        )
        
        s6 = arb_today.sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
        format_strat(s6, 'arb_etfs', **f_args)
    else:
        format_strat(pl.DataFrame(), 'arb_etfs', **f_args)

    # 7. tru_rally
    s7 = today_prices.filter(
        (pl.col("date") == max_date) &
        pl.col("symbol").is_in(['TNA','UPRO','YINN']) &
        (pl.col("volume") > 100000) &
        (pl.col("close") > 7) &
        (pl.col("close") > pl.col("lag1close") * 1.03)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s7, 'tru_rally', **f_args)

    # 8. corr_reverse
    s8 = today_prices.filter(
        pl.col("category").is_in(['equity basket', 'physical commodities']) &
        (pl.col("volume") > 1000000) &
        (pl.col("close") > 7) &
        (pl.col("leverage") > 0) &
        (pl.col("avg_root_delta") < -0.03) &
        (pl.col("root_delta_corr") > 0.15)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(s8, 'corr_reverse', **f_args)

    # Save all_etfs.csv
    logger.info("Writing all_etfs.csv...")
    today_prices.write_csv(os.path.join(OUT_DIR, "all_etfs.csv"))
    logger.info("Done.")

if __name__ == "__main__":
    main()
