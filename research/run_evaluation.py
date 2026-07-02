import sys
import os
import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from dotenv import load_dotenv
import glob

# Setup environment and paths
load_dotenv("../.env")
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))) # For research/

from research.performance import evaluate_performance, summarize_performance
from features import (
    calculate_session_id, 
    add_lags_leads, 
    add_rolling_features, 
    calculate_orders, 
    rally, 
    add_etf_specific_features
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_data_for_years(load_years):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    etf_list_path = os.path.join(repo_root, "other_datasources", "etf_list.csv")
    
    if not os.path.exists(etf_list_path):
        etf_list_path = os.path.abspath("../other_datasources/etf_list.csv")

    etf_meta = pl.read_csv(etf_list_path, infer_schema_length=10000, ignore_errors=True)\
        .with_columns(pl.col("leverage").cast(pl.Float64, strict=False))\
        .select([
            pl.col("ticker").alias("symbol"), 
            pl.col("category"), 
            pl.col("leverage")
        ])
    
    schema_overrides = {"cik": pl.Utf8, "sic_description": pl.Utf8, "market_cap": pl.Float64, "date": pl.Date, "list_date": pl.Date}
    
    home = os.path.expanduser("~")
    lazy_frames = []
    
    for year in load_years:
        f = os.path.join(home, "data", f"{year}.csv.gz")
        if not os.path.exists(f):
            continue
        try:
            lf = pl.scan_csv(
                f,
                infer_schema_length=5000,
                schema_overrides=schema_overrides,
                ignore_errors=True
            )
            cols = lf.collect_schema().names()
            
            if "type" in cols:
                lf = lf.filter(pl.col("type").is_in(["ETF", "INDEX", "ETV"]))
                
            if "stock" in cols and "symbol" not in cols:
                lf = lf.rename({"stock": "symbol"})
            elif "stock" in cols and "symbol" in cols:
                lf = lf.with_columns(pl.coalesce(["symbol", "stock"]).alias("symbol")).drop("stock")
                
            if all(c in cols for c in ["volume", "open", "close"]):
                lf = lf.filter((pl.col("volume") * pl.col("open") * pl.col("close")) > 0)
                
            lazy_frames.append(lf)
        except Exception as e:
            logger.warning(f"Error scanning {f}: {e}")
            
    if not lazy_frames:
        return pl.DataFrame()
        
    lazy_prices = pl.concat(lazy_frames, how="diagonal_relaxed")
    lazy_prices = lazy_prices.join(etf_meta.lazy(), on="symbol", how="left")
    
    return lazy_prices.collect(engine="streaming")

def process_features(df):
    df = calculate_session_id(df)
    df = add_lags_leads(df)
    df = add_rolling_features(df, roll_window=25, short_roll_window=5, corr_window=100)
    df = rally(df)
    
    if "market_cap" not in df.columns:
        df = df.with_columns(pl.lit(0).alias("market_cap"))
        
    df = calculate_orders(df) 
    df = add_etf_specific_features(df)
    return df

def generate_signals_for_chunk(df, target_years):
    # 1. Pull execution metrics from T+1 back to T using shift(-1)
    df = df.with_columns([
        (pl.col("open").shift(-1).over("symbol")).alias("next_open"),
        (pl.col("sell_rally").shift(-1).over("symbol")).alias("next_sell_rally"),
        (pl.col("sell_rally_date").shift(-1).over("symbol")).alias("next_sell_rally_date")
    ])
    
    # Strictly filter to the target years (we drop the padding years used for features)
    df = df.filter(pl.col("date").dt.year().is_in(target_years))
    
    strategies = {}

    # Default sort key: day_drop_norm / sd_from0 (matches src/am_etfs.py)
    default_sort_expr = pl.col("day_drop_norm") / pl.col("sd_from0")
    #default_sort_expr = pl.col("symbol").hash(seed=42)

    # 1. rally_etfs
    strategies["rally_etfs"] = (
        df.filter(
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 1000000) &
            (pl.col("unadjClose") > 7) &
            (pl.col("category") == 'equity basket') &
            (pl.col("close") < pl.col("lag1high")) &
            (pl.col("sell_rally_day") > 10) &
            (pl.col("sd_from0") > 0.015)
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )

    # 2. revert_etfs
    strategies["revert_etfs"] = (
        df.filter(
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 1000000) &
            (pl.col("unadjClose") > 7) &
            pl.col("category").is_in(['equity basket','physical commodities']) &
            (((pl.col("high") - pl.col("close")) / pl.col("avg_range")) > 1) &
            (pl.col("avg_delta") < (1 - 0.005 * pl.col("leverage").abs()))
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )

    # 3. corr_long_etfs
    strategies["corr_long_etfs"] = (
        df.filter(
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 1000000) &
            (pl.col("unadjClose") > 7) &
            pl.col("category").is_in(['equity basket','physical commodities']) &
            (pl.col("avg_delta_short") < 0.975) &
            (pl.col("lagging_corr_long") > 0.7)
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )
    
    # 4. drop_etfs
    strategies["drop_etfs"] = (
        df.filter(
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 1000000) &
            (pl.col("unadjClose") > 7) &
            pl.col("category").is_in(['equity basket','physical commodities']) &
            (((pl.col("close") - pl.col("low")) / (pl.col("high") - pl.col("low"))) < 0.15) &
            ((pl.col("MACD") / pl.col("MACD_slow")) < 0.98)
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )

    # 5. deviant_etfs
    strategies["deviant_etfs"] = (
        df.filter(
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 500000) &
            (pl.col("unadjClose") > 7) &
            (pl.col("category") == 'equity basket') &
            ((pl.col("lag1close") - pl.col("close")) > (pl.col("avg_range") * 0.25))
        ).with_columns(pl.col("sd_from0").alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, True])
    )

    # 6. tru_rally
    strategies["tru_rally"] = (
        df.filter(
            pl.col("symbol").is_in(['TNA','UPRO','YINN']) &
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 100000) &
            (pl.col("unadjClose") > 7) &
            (pl.col("close") > pl.col("lag1close") * 1.03)
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )

    # 7. corr_reverse
    strategies["corr_reverse"] = (
        df.filter(
            pl.col("category").is_in(['equity basket', 'physical commodities']) &
            ((pl.col("volume") * pl.col("close") / pl.col("unadjClose")) > 1000000) &
            (pl.col("unadjClose") > 7) &
            (pl.col("leverage") > 0) &
            (pl.col("avg_root_delta") < -0.03) &
            (pl.col("root_delta_corr") > 0.15)
        ).with_columns(default_sort_expr.alias("sort_val"))
         .sort(["date", "sort_val"], descending=[False, False])
    )

    results = {}
    for name, sig_df in strategies.items():
        results[name] = sig_df.select([
            pl.col("date"),
            pl.col("symbol"),
            (pl.col("next_sell_rally") / pl.col("next_open") - 1).alias("outcome"),
            (pl.col("next_sell_rally_date")).alias("sell_date")
        ])
    return results

def build_html_report(strategy_results, output_file="evaluation_report.html"):
    html_sections = [
        "<html><head><title>ETF Strategy Evaluations</title></head>",
        "<body style='background-color:#111; color:#fff; font-family:sans-serif;'>",
        "<h1 style='text-align:center;'>ETF Strategy Performance Report</h1>"
    ]
    
    first_fig = True
    for strategy_name, (daily_perf, yearly_summary) in strategy_results.items():
        html_sections.append(f"<hr><h2 style='color:#3498db; text-align:center;'>Strategy: {strategy_name}</h2>")
        if yearly_summary.is_empty():
            html_sections.append(f"<p style='text-align:center;'>No signals fired for <b>{strategy_name}</b>.</p>")
            continue
            
        # Format pandas to html
        pd_df = yearly_summary.to_pandas()
        table_html = pd_df.to_html(index=False, classes='table table-dark', border=0)
        table_html = table_html.replace('class="dataframe table table-dark"', 'style="margin:auto; text-align:right; color:#fff; border-collapse: collapse; width:80%; font-size:14px;" border="1" cellpadding="5"')
        html_sections.append("<h3 style='text-align:center;'>Yearly Summary</h3>")
        html_sections.append(table_html)
        
        # Add Plotly Figure DIV
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(x=daily_perf["date"], y=daily_perf["outcome"].cum_sum(), name="Cumulative Return", line=dict(color="#2ecc71", width=2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=daily_perf["date"], y=daily_perf["drawdown"], name="Drawdown", fill='tozeroy', line=dict(color="#e74c3c")), row=2, col=1)
        
        # Positions Held with custom hover text
        hover_text = daily_perf["held_symbols"].to_list()
        fig.add_trace(go.Scatter(
            x=daily_perf["date"], 
            y=daily_perf["n_held"], 
            name="Positions Held", 
            line=dict(color="#3498db", dash='dot'),
            hovertext=hover_text,
            hovertemplate="Held: %{y}<br>Symbols: %{hovertext}<extra></extra>"
        ), row=1, col=1)
        
        fig.update_layout(template="plotly_dark", height=500, margin=dict(l=20, r=20, t=20, b=20), showlegend=True)
        
        html_sections.append("<h3 style='text-align:center;'>Performance Chart</h3>")
        
        js_inclusion = 'cdn' if first_fig else False
        first_fig = False
        html_sections.append(fig.to_html(full_html=False, include_plotlyjs=js_inclusion))
        
    # Add Correlation Matrix Heatmap
    valid_strats = [name for name, (daily, yearly) in strategy_results.items() if not yearly.is_empty()]
    if len(valid_strats) > 1:
        # Get all unique dates
        all_dates = pl.concat([strategy_results[s][0].select("date") for s in valid_strats]).unique().sort("date")
        
        corr_df = all_dates
        for s in valid_strats:
            strat_outcomes = strategy_results[s][0].select(["date", pl.col("outcome").alias(s)])
            # Fill dates where the strategy didn't trade with 0 outcome
            corr_df = corr_df.join(strat_outcomes, on="date", how="left").with_columns(pl.col(s).fill_null(0.0))
            
        # 90-day rolling sums over the trading days
        exprs = []
        for s in valid_strats:
            exprs.append(pl.col(s).rolling_sum(window_size=90, min_periods=1).alias(s))
            
        roll_df = corr_df.select(exprs)
        corr_matrix = roll_df.to_pandas().corr()
        
        # Build Heatmap
        heatmap = go.Figure(data=go.Heatmap(
            z=corr_matrix.values,
            x=corr_matrix.columns,
            y=corr_matrix.columns,
            colorscale='RdBu',
            zmin=-1, zmax=1,
            text=corr_matrix.values.round(2),
            texttemplate="%{text}",
            hoverinfo="z"
        ))
        heatmap.update_layout(
            title="90-Day Running Returns Correlation", 
            template="plotly_dark", 
            width=800, height=800,
            xaxis_title="Strategy",
            yaxis_title="Strategy"
        )
        
        html_sections.append("<hr><h2 style='color:#f39c12; text-align:center;'>Strategy Correlations</h2>")
        html_sections.append(heatmap.to_html(full_html=False, include_plotlyjs=js_inclusion))

    html_sections.append("</body></html>")
    
    # Save the file
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(repo_root, output_file)
    with open(out_path, "w") as f:
        f.write("\n".join(html_sections))
    logger.info(f"Saved complete HTML report to {out_path}")

if __name__ == '__main__':
    logger.info("Starting chunked ETF Evaluation for all strategies...")
    
    # Find all available years
    home = os.path.expanduser("~")
    data_files = glob.glob(os.path.join(home, "data", "*.csv.gz"))
    all_years = sorted({int(os.path.basename(f).split('.')[0]) for f in data_files if os.path.basename(f).split('.')[0].isdigit()})
    
    if not all_years:
        logger.error("No yearly data found in ~/data/")
        sys.exit(1)
        
    chunk_size = 4
    strategy_names = [
        "rally_etfs", "revert_etfs", "corr_long_etfs", 
        "drop_etfs", "deviant_etfs", "tru_rally", "corr_reverse"
    ]
    
    all_results = {s: [] for s in strategy_names}
    
    for i in range(0, len(all_years), chunk_size):
        target_years = all_years[i:i+chunk_size]
        min_yr, max_yr = min(target_years), max(target_years)
        load_years = list(range(min_yr - 1, max_yr + 2))
        
        logger.info(f"Processing chunk for target years {target_years} (loading {load_years})...")
        
        raw_prices = load_data_for_years(load_years)
        if raw_prices.is_empty():
            continue
            
        prices = process_features(raw_prices)
        del raw_prices # Free memory
        
        chunk_results = generate_signals_for_chunk(prices, target_years)
        
        for s in strategy_names:
            if not chunk_results[s].is_empty():
                all_results[s].append(chunk_results[s])
        
        del prices
        del chunk_results
        
    logger.info("Finished compiling all signals. Running portfolio evaluations...")
    
    final_evaluations = {}
    for s in strategy_names:
        hold_max = 1 if s=='deviant_etfs' else 5
        if all_results[s]:
            combined_signals = pl.concat(all_results[s])
            daily_results = evaluate_performance(
                combined_signals, 
                hold_max=hold_max, 
                buy_per_day_max=1,
                hold_same_max=5
            )
            yearly_summary = summarize_performance(daily_results, combined_signals)
            final_evaluations[s] = (daily_results, yearly_summary)
        else:
            final_evaluations[s] = (pl.DataFrame(), pl.DataFrame())
            
    logger.info("Generating unified HTML report...")
    build_html_report(final_evaluations, "evaluation_report.html")
    
    logger.info("Done. The HTML report was written to 'evaluation_report.html'.")
