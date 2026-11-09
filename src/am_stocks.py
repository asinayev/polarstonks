import polars as pl
import os
import sys
import logging
from datetime import date

# Add parent dir to path if needed for config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import OUT_DIR
from src.features import (
    calculate_session_id,
    add_lags_leads,
    add_rolling_features,
    calculate_orders,
    rally,
    get_last_full_date,
)
from src.outputs import format_strat

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    # 1. Load Data
    input_path = os.path.join(OUT_DIR, 'prices.parquet')
    if not os.path.exists(input_path):
        input_path = os.path.join(OUT_DIR, 'prices.csv')
        if not os.path.exists(input_path):
            logger.error(f"Prices file not found at {input_path}. Run get_data.py first.")
            sys.exit(1)
            
    logger.info(f"Loading prices from {input_path}...")
    if input_path.endswith('.parquet'):
        prices = pl.read_parquet(input_path)
    else:
        prices = pl.read_csv(input_path)

    if prices.is_empty():
        logger.error("Empty prices dataframe.")
        sys.exit(1)

    # 2. Filter last 150 rows per symbol
    # Sort by symbol, date desc, take head 150, then sort back ascending
    prices = prices.sort(["symbol", "date"], descending=[False, True])
    prices = prices.group_by("symbol").head(150)
    prices = prices.sort(["symbol", "date"]) # Ascending for rolling calcs

    # 3. Features
    logger.info("Calculating features...")
    prices = calculate_session_id(prices)
    prices = add_lags_leads(prices)
    # rolling features: roll_window=25, short=5, corr=100
    prices = add_rolling_features(prices, roll_window=25, short_roll_window=5, corr_window=100)
    prices = rally(prices)
    prices = calculate_orders(prices) # vp_order, cap_order
    
    # 4. Strategies
    max_date = get_last_full_date(prices)
    logger.info(f"Applying strategies for date: {max_date}")

    # Strategies handled by src.outputs.format_strat

    # Filter for today
    today_prices = prices.filter(pl.col("date") == max_date)
    
    # A. bigcap_long
    # bigcaps = prices[volume>500000 & close>7 & cap_order<250]
    # filter: ((avg_delta>1.0025 & avg_delta_short<.985) | (close>open*1.05 & avg_delta_short<1))
    # order: volume ascending -> head(1)
    
    strat_a = today_prices.filter(
        (pl.col("volume") > 500000) &
        (pl.col("close") > 7) &
        (pl.col("cap_order") < 250) &
        (
            ((pl.col("avg_delta") > 1.0025) & (pl.col("avg_delta_short") < 0.985)) |
            ((pl.col("close") > pl.col("open") * 1.05) & (pl.col("avg_delta_short") < 1))
        )
    ).sort("volume").head(1)
    format_strat(strat_a, "bigcap_long")

    # B. correlated_long
    # close>7 & avg_volume>1000000 & cap_order<1500
    # close<lag1high & sell_rally_day>4 & avg_drop>.075
    # order: day_drop_norm/sd_from0 ascending -> head(1)
    
    strat_b = today_prices.filter(
        (pl.col("close") > 7) &
        (pl.col("avg_volume") > 1000000) &
        (pl.col("cap_order") < 1500) &
        (pl.col("close") < pl.col("lag1high")) &
        (pl.col("sell_rally_day") > 4) &
        (pl.col("avg_drop") > 0.075)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(strat_b, "correlated_long")

    # C. megacap
    # avg_delta_short<avg_delta*.985 & cap_order<50 & lagging_corr_long>.7
    # order: day_drop_norm/sd_from0 ascending -> head(1)
    
    strat_c = today_prices.filter(
        (pl.col("avg_delta_short") < pl.col("avg_delta") * 0.985) &
        (pl.col("cap_order") < 50) &
        (pl.col("lagging_corr_long") > 0.7)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(strat_c, "megacap")

    # D. megacaphist
    # avg_delta_short<.975 & vp_order<25
    # order: day_drop_norm/sd_from0 ascending -> head(1)
    
    strat_d = today_prices.filter(
        (pl.col("avg_delta_short") < 0.975) &
        (pl.col("vp_order") < 25)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(strat_d, "megacaphist")

    # E. volumelong
    # close>25 & volume>100000 & volume>=max_volume & avg_delta_short<.99
    # vp_order>cap_order & (close-low)/avg_range<.1
    # order: day_drop_norm/sd_from0 ascending -> head(1)
    
    strat_e = today_prices.filter(
        (pl.col("close") > 25) &
        (pl.col("volume") > 100000) &
        (pl.col("volume") >= pl.col("max_volume")) &
        (pl.col("avg_delta_short") < 0.99) &
        (pl.col("vp_order") > pl.col("cap_order")) &
        ((pl.col("close") - pl.col("low")) / pl.col("avg_range") < 0.1)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(strat_e, "volumelong")

    # F. volumehist
    # close>25 & volume>100000 & volume>=max_volume & volume<lag1volume*1.5
    # close<lag1close*.975 & vp_order<500
    # order: day_drop_norm/sd_from0 ascending -> head(1)
    
    strat_f = today_prices.filter(
        (pl.col("close") > 25) &
        (pl.col("volume") > 100000) &
        (pl.col("volume") >= pl.col("max_volume")) &
        (pl.col("volume") < pl.col("lag1volume") * 1.5) &
        (pl.col("close") < pl.col("lag1close") * 0.975) &
        (pl.col("vp_order") < 500)
    ).sort(pl.col("day_drop_norm") / pl.col("sd_from0")).head(1)
    format_strat(strat_f, "volumehist")

    # Write all stocks
    logger.info("Writing all_stocks.csv...")
    today_prices.write_csv(os.path.join(OUT_DIR, "all_stocks.csv"))
    logger.info("Done.")

if __name__ == "__main__":
    main()
