import polars as pl
import os
import sys

# Add parent dir to path if needed for config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import OUT_DIR

def format_strat(df, name, action="BUY", order_type="MKT", time_in_force="OPG", base_dir=OUT_DIR):
    """
    Helper to select standard columns and write strategy output.
    Handles empty dataframes by providing correct headers.
    """
    if df.is_empty():
        # Create empty df with expected schema for write_strat
        out = pl.DataFrame(schema={
            "symbol": pl.Utf8, 
            "action": pl.Utf8, 
            "order_type": pl.Utf8, 
            "time_in_force": pl.Utf8, 
            "strike_price": pl.Float64
        })
    else:
        out = df.select([
            pl.col("symbol"),
            pl.lit(action).alias("action"),
            pl.lit(order_type).alias("order_type"),
            pl.lit(time_in_force).alias("time_in_force"),
            pl.col("close").alias("strike_price")
        ])
    
    write_strat(out, name, base_dir=base_dir)

def write_strat(df, strat_name, base_dir=OUT_DIR, append=False, 
                price_condition_min=False, price_condition_max=False):
    """Writes strategy output to CSV with standard filtering and cleaning."""
    # Ensure directory exists
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)

    # Standard columns
    write_cols = ['symbol','action','order_type','strike_price','time_in_force']
    if price_condition_min:
        write_cols.append('price_condition_min')
    if price_condition_max:
        write_cols.append('price_condition_max')

    # If dataframe is NOT empty, apply filters and cleaning
    if not df.is_empty():
        blacklist = ['GOOG','GOOGL','FUTY','FBND','BAR','GGLS','GGLL','GOOW','GOOP','GOOY']
        df = df.filter(~pl.col("symbol").is_in(blacklist))
        
        # Clean symbol: alphanumeric only
        df = df.with_columns(
            pl.col("symbol").str.replace_all("[^[:alnum:] ]", " ")
        )
        
        # Ensure strike_price exists (fallback to close if it was missing in input)
        if "strike_price" not in df.columns:
            if "close" in df.columns:
                df = df.with_columns(pl.col("close").alias("strike_price"))
            else:
                df = df.with_columns(pl.lit(None).alias("strike_price"))
    
    # Ensure all write_cols exist in df (even if empty)
    for col in write_cols:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))

    out_file = os.path.join(base_dir, f"{strat_name}.csv")
    
    try:
        # Polars write_csv handles empty dataframes by writing header only.
        df.select(write_cols).write_csv(out_file)
        print(f"Wrote {len(df)} rows to {out_file}")
    except Exception as e:
        print(f"Error writing {strat_name}: {e}")
