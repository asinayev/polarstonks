# Research Directory

This directory contains scripts and utilities for data acquisition, feature engineering, and backtesting ETF strategies.

## Contents

- **`getting_data.py`**: Fetches historical stock data (adjusted and unadjusted) from Polygon.io.
- **`run_evaluation.py`**: The main backtesting engine. It processes features, runs multiple ETF strategies, and generates a comprehensive HTML performance report.
- **`performance.py`**: A utility module providing functions for portfolio simulation, drawdown calculation, and performance summarization.
- **`notebook_utils.py`**: Helper functions for enhanced DataFrame visualization within Jupyter notebooks.

## How to Run

These commands should be run from the project root using the Python executable within the virtual environment.

### 1. Fetch Historical Data

Before running evaluations, you need to download historical price data. The `getting_data.py` script fetches data for specific years and saves them to `~/data/*.csv.gz`.

```bash
# Example: Fetch data for 2024 and 2025
./.venv/bin/python research/getting_data.py 2024 2025
```

**Note:** Ensure your `POLYGON_KEY` is set in the `.env` file in the project root.

### 2. Run Strategy Evaluation

Once the data is available in `~/data`, you can run the full backtest suite. This script will:
1. Load data for all available years.
2. Calculate technical features (lags, rolling windows, rallies, etc.).
3. Generate signals for multiple strategies (e.g., `rally_etfs`, `revert_etfs`, `deviant_etfs`).
4. Simulate portfolio performance with constraints (max holdings, daily buy limits).
5. Generate an `evaluation_report.html` in the project root.

```bash
./.venv/bin/python research/run_evaluation.py
```

### 3. Interactive Analysis

For interactive research, use the Jupyter notebook located at `notebooks/strategy_evaluation.ipynb`. It utilizes `notebook_utils.py` for better data visualization.

## Dependencies

These scripts rely on:
- `polars`: For high-performance data manipulation.
- `plotly`: For generating interactive charts in the HTML report.
- `python-dotenv`: For managing API keys.
- `src/`: The project's core logic for feature calculation and API clients.
