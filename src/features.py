import polars as pl
from datetime import date, timedelta
import numpy as np

def calculate_session_id(df, gap_days=10):
    """Identifies unbroken sessions by finding large gaps in dates."""
    # Ensure ordered by symbol, date
    df = df.sort(["symbol", "date"])
    
    # Calculate date diff in days
    # Increment session ID when gap > gap_days
    
    session_change = (
        (pl.col("date") - pl.col("date").shift(1)).dt.total_days().fill_null(0) > gap_days
    ).cast(pl.Int32)
    
    # Cumulative sum over symbol gives session ID within symbol
    session_id = session_change.cum_sum().over("symbol")
    
    return df.with_columns([
        (pl.col("symbol") + "_" + session_id.cast(pl.Utf8)).alias("symbol_session")
    ])

def add_lags_leads(df):
    """Adds lagged and lead columns for close, open, high, low, volume."""
    # Operates over "symbol_session" to avoid bleeding across sessions
    # Using window function over "symbol_session" (or "symbol" if sessions handle gaps)
    # But R script used symbol_session.
    
    lags = [1, 2, 5]
    leads = [1, 2, 5]
    
    exprs = []
    for l in lags:
        exprs.append(pl.col("close").shift(l).over("symbol_session").alias(f"lag{l}close"))
        if l <= 2:
            exprs.append(pl.col("open").shift(l).over("symbol_session").alias(f"lag{l}open"))
            exprs.append(pl.col("high").shift(l).over("symbol_session").alias(f"lag{l}high"))
            exprs.append(pl.col("low").shift(l).over("symbol_session").alias(f"lag{l}low"))
            if l == 1:
                exprs.append(pl.col("volume").shift(l).over("symbol_session").alias(f"lag{l}volume"))

    for l in leads:
        exprs.append(pl.col("close").shift(-l).over("symbol_session").alias(f"lead{l}close"))
        if l <= 2:
             exprs.append(pl.col("open").shift(-l).over("symbol_session").alias(f"lead{l}open"))
             exprs.append(pl.col("high").shift(-l).over("symbol_session").alias(f"lead{l}high"))

    return df.with_columns(exprs)

def add_rolling_features(df, roll_window=25, short_roll_window=5, corr_window=100):
    """Adds rolling means, SDs, correlations."""
    # R: is_valid := sum(!is.na(close/lag1close)) >= roll_window
    # Polars: filter or calculate only where valid.
    
    # Pre-calculate delta
    df = df.with_columns((pl.col("close") / pl.col("lag1close")).alias("delta"))
    
    # Rolling expressions
    # Note: R uses `align='right'`, Polars rolling_mean defaults to center=False (right aligned)
    
    roll_exprs = [
        pl.col("delta").rolling_mean(roll_window).over("symbol_session").alias("avg_delta"),
        pl.col("delta").rolling_mean(short_roll_window).over("symbol_session").alias("avg_delta_short"),
        pl.col("low").rolling_min(roll_window).over("symbol_session").alias("running_low"),
        (pl.col("high") - pl.col("low")).rolling_mean(roll_window).over("symbol_session").alias("avg_range"),
        
        # sd_from0: sqrt(mean((1-delta)^2))
        ((1 - pl.col("delta"))**2).rolling_mean(roll_window).over("symbol_session").sqrt().alias("sd_from0"),
        
        # avg_drop: sqrt(mean(ifelse(low<lag1close, 1-low/lag1close, 0)^2))
        (
            pl.when(pl.col("low") < pl.col("lag1close"))
            .then(1 - pl.col("low") / pl.col("lag1close"))
            .otherwise(0.0)
            ** 2
        ).rolling_mean(roll_window).over("symbol_session").sqrt().alias("avg_drop"),
        
        pl.col("volume").rolling_mean(roll_window).over("symbol_session").alias("avg_volume"),
        
        # avg_vp: mean(close*volume)
        (pl.col("close") * pl.col("volume")).rolling_mean(roll_window).over("symbol_session").alias("avg_vp"),
        
        # max_volume (over corr_window)
        pl.col("volume").rolling_max(corr_window).over("symbol_session").alias("max_volume"),
        
        # max_price_short
        pl.col("high").rolling_max(short_roll_window).over("symbol_session").alias("max_price_short"),
        
        # MACD (EMA 12 / EMA 26) -- note: R script calculates ratio, usually MACD is diff?
        # Script says: EMA(close, 12) / EMA(close, 26). Unusual but we follow.
        (pl.col("close").ewm_mean(span=12, adjust=False) / pl.col("close").ewm_mean(span=26, adjust=False)).over("symbol_session").alias("MACD"),
    ]
    
    df = df.with_columns(roll_exprs)
    
    # MACD_slow (EMA 9 of MACD)
    df = df.with_columns(
        pl.col("MACD").ewm_mean(span=9, adjust=False).over("symbol_session").alias("MACD_slow")
    )
    
    # day_drop_norm
    df = df.with_columns(
        ((pl.col("high") - pl.col("close")) / pl.col("avg_range")).alias("day_drop_norm")
    )
    
    # Correlations (runCor in R)
    # Polars rolling_corr: rolling_corr(a, b, window_size)
    # lagging_corr_long: corr(close/open, close/lag1close, corr_window)
    
    df = df.with_columns([
        pl.rolling_corr(pl.col("close")/pl.col("open"), pl.col("close")/pl.col("lag1close"), window_size=corr_window).over("symbol_session").alias("lagging_corr_long")
    ])

    return df

def calculate_orders(df):
    """Calculates rank orders for volume*price (vp_order) and market_cap (cap_order)."""
    # Orders are per date, descending
    return df.with_columns([
        pl.col("avg_vp").rank("ordinal", descending=True).over("date").alias("vp_order"),
        pl.col("market_cap").rank("ordinal", descending=True).over("date").alias("cap_order")
    ])

def rally(df, sell_close=True):
    """Calculates rally metrics."""
    condition = (pl.col("close") >= pl.col("lag1high")).shift(1).fill_null(False)
    
    # Increment ID on each rally
    df = df.with_columns(
        condition.cast(pl.Int32).cum_sum().over("symbol_session").alias("rally_id")
    )
    
    # Calculate metrics per rally_id
    # Broadcast end-of-rally values to all rows in that rally
    
    # Polars window functions over (symbol_session, rally_id)
    window = ["symbol_session", "rally_id"]
    
    if sell_close:
        val_col = "close"
    else:
        val_col = "open"
        
    df = df.with_columns([
        pl.col(val_col).last().over(window).alias("sell_rally"),
        pl.col("date").last().over(window).alias("sell_rally_date"),
        pl.count().over(window).alias("sell_rally_day") # Approximate duration (rows in group)
        # Actually R uses seq_len(.N), which creates 1,2,3...N. 
        # So `sell_rally_day` is the "day number within rally".
    ])
    
    # Fix sell_rally_day to be sequence
    # Use rank('ordinal') on date within the rally group to get 1, 2, 3...
    df = df.with_columns(
        pl.col("date").rank("ordinal").over(window).alias("sell_rally_day")
    )
    
    return df

def matching_pairs_for_year(year_val, dataset, reference_etfs):
    """Calculates correlation pairs for arb strategy."""
    # dataset[,target_var:=avg_delta_short-1]
    dataset = dataset.with_columns((pl.col("avg_delta_short") - 1).alias("target_var"))
    
    # key_etfs = dataset[year(date)==(yr-1)...]
    # Filter for year-1
    prev_year_data = dataset.filter(pl.col("date").dt.year() == (year_val - 1))
    
    if prev_year_data.is_empty():
        return pl.DataFrame()

    # Aggregates
    key_etfs = prev_year_data.group_by("symbol").agg([
        pl.col("volume").mean().alias("volume"),
        pl.col("target_var").std().alias("volatility"),
        pl.count().alias("count")
    ]).filter(
        (pl.col("volume") > 100000) & (pl.col("count") > 50)
    )
    
    symbols_to_keep = key_etfs["symbol"]
    
    # Pivot for correlation
    # R: dcast(date~symbol, value.var='target_var', fun.aggregate = mean)
    # Polars pivot
    
    pivot_data = prev_year_data.filter(
        pl.col("symbol").is_in(symbols_to_keep) & pl.col("target_var").is_not_null()
    )
    
    if pivot_data.is_empty():
        return pl.DataFrame()
        
    pivoted = pivot_data.pivot(
        values="target_var",
        index="date",
        columns="symbol",
        aggregate_function="mean"
    )
    
    # Correlation matrix
    
    # Drop date column
    data_cols = [c for c in pivoted.columns if c != "date"]
    # Converting to pandas for pairwise correlation handling
    pdf = pivoted.select(data_cols).to_pandas()
    corr_matrix = pdf.corr()
    
    # Find high correlations (> .98 or < -.98)
    # Melt/stack to get pairs
    # R: which((etf_corrs>.98 | etf_corrs< -.98),arr.ind=T)
    
    # In python:
    # Stack, filter
    
    # Reset index to make 'target' a column
    corr_melted = corr_matrix.stack().reset_index()
    corr_melted.columns = ['target', 'reference', 'corr']
    
    # Filter
    hi_corr = corr_melted[
        (corr_melted['corr'].abs() > 0.98) & 
        (corr_melted['target'] != corr_melted['reference'])
    ]
    
    if hi_corr.empty:
        return pl.DataFrame()
        
    # Convert back to Polars
    hi_corr_pl = pl.from_pandas(hi_corr)
    
    # Merge volume/volatility details
    # R merges key_etfs again
    
    hi_corr_pl = hi_corr_pl.join(key_etfs, left_on='target', right_on='symbol').rename({"volume": "target_volume", "volatility": "target_volatility"})
    hi_corr_pl = hi_corr_pl.join(key_etfs, left_on='reference', right_on='symbol').rename({"volume": "reference_volume", "volatility": "reference_volatility", "count": "count_ref"})
    
    # Filter tradeable: reference_volume > target_volume & reference in reference_etfs
    hi_corr_tradeable = hi_corr_pl.filter(
        (pl.col("reference_volume") > pl.col("target_volume")) &
        pl.col("reference").is_in(reference_etfs)
    )
    
    # Calculate linear regression coefficients (beta/mult) for each pair
    # R: lm(target_delta_short ~ 0 + reference_delta_short) -> no intercept
    
    results = []
    
    # Optimization: Do this loop efficiently or in parallel?
    # For now, simple loop over pairs.
    
    import numpy as np
    
    # Prepare data dict for fast lookup
    # Need date-aligned data for regression.
    # Reuse 'pivoted' or 'pdf'
    
    # Pairs list
    pairs = hi_corr_tradeable.select(['target', 'reference']).to_dicts()
    
    for p in pairs:
        tgt = p['target']
        ref = p['reference']
        
        # Get data for regression (prev year)
        # We need rows where both exist
        subset = pdf[[tgt, ref]].dropna()
        
        if len(subset) < 20: # Arbitrary small check
            continue
            
        y = subset[tgt].values
        x = subset[ref].values
        
        # Regression through origin: y = beta * x
        # beta = sum(x*y) / sum(x^2)
        # R squared calculation
        
        dot_xx = np.dot(x, x)
        if dot_xx == 0:
            continue
            
        beta = np.dot(x, y) / dot_xx
        
        # R-squared for model through origin
        # R^2 = 1 - SSE/SST
        # SSE = sum((y - beta*x)^2)
        # Uncentered parameter for Rsquared when intercept=0
        y_pred = beta * x
        sse = np.sum((y - y_pred)**2)
        sst = np.sum(y**2)
        rsq = 1 - sse/sst
        
        results.append({
            'target': tgt,
            'reference': ref,
            'mult': beta,
            'rsq': rsq
        })
        
    if not results:
        return pl.DataFrame()
        
    coefs_df = pl.DataFrame(results)
    
    # Join coefs back to pairs
    # In R: hi_corr_tradeable = cbind(hi_corr_tradeable, t(apply...))
    
    hi_corr_tradeable = hi_corr_tradeable.join(coefs_df, on=['target', 'reference'])
    
    # Merge back to original dataset (current year)
    # comparison_to_reference=dataset[year(date)==yr] %>% merge...
    
    current_year_data = dataset.filter(pl.col("date").dt.year() == year_val)
    
    # Join target data
    # We want: target columns, plus reference columns (avg_delta, etc)
    
    # First join logic: map target symbols to their reference & coefs
    # A symbol might be a target for multiple references? 
    # R: merge(hi_corr_tradeable, by.x='symbol', by.y='target') -> This implies filtering dataset to only targets?
    # Yes, hi_corr_tradeable has 'target' and 'reference'.
    # It joins dataset (on symbol=target) with hi_corr_tradeable.
    
    out = current_year_data.join(hi_corr_tradeable, left_on='symbol', right_on='target')
    
    # Then join reference data
    # merge(dataset[...], by=c('date', 'reference'))
    # But dataset has 'symbol', we need to join on symbol=reference
    
    ref_data = current_year_data.select([
        pl.col("date"),
        pl.col("symbol").alias("reference"),
        pl.col("avg_delta_short").alias("reference_delta_short"),
        pl.col("avg_delta").alias("reference_avg_delta"),
        (pl.col("close") / pl.col("lag1close")).alias("reference_delta")
    ])
    
    out = out.join(ref_data, on=["date", "reference"])
    
    return out

def add_etf_specific_features(df):
    """Calculates ETF specific features like avg_root_delta and root_delta_corr."""
    # Helper for cube root preserving sign
    def cbrt(expr):
        return (expr.abs().pow(1/3)) * expr.sign()
    
    # Calculate avg_root_delta
    df = df.with_columns(
        cbrt(
            ((pl.col("delta") - 1).pow(3)).rolling_mean(5).over("symbol_session")
        ).alias("avg_root_delta")
    )
    
    # Calculate root_delta_corr
    df = df.with_columns(
        pl.col("avg_root_delta").shift(5).over("symbol_session").alias("avg_root_delta_lag")
    )
    
    df = df.with_columns(
        pl.rolling_corr(
            pl.col("close") / pl.col("lag5close"),
            pl.col("avg_root_delta_lag"),
            window_size=100
        ).over("symbol_session").alias("root_delta_corr")
    )
    
    return df
