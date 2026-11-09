# Workspace Guidelines & Key Learnings

## 1. Environment & Execution
- **Python Virtualenv**: Always use the project virtual environment (e.g., `./run.sh` or `.venv/bin/python3` in project roots like `polarstonks`). System Python lacks libraries like `polars`, `ib_async`, `google-cloud-storage`.
- **Symlinked Directories**: Directories in `/home/aleks-remote/` (like `polarstonks`) symlink to `/home/aleks/`. If IDE tools encounter path permission boundary issues, bash execution via terminal commands works reliably.
- **Git Commits**: Use the repository identity `asinayev <asinayev@gmail.com>` for commits (`git config user.name "asinayev" && git config user.email "asinayev@gmail.com"`).

## 2. Google Cloud Platform & Authentication
- **Service Account Key**: GCP IAM credentials are stored at `/home/aleks-remote/.gcp/service-account.json`.
- **Environment Variable**: Ensure `GOOGLE_APPLICATION_CREDENTIALS=/home/aleks-remote/.gcp/service-account.json` is set for GCS operations.
- **API Keys vs IAM**: `GOOGLEKEY` in `.env` is specifically for Google Custom Search / Search API (`SEARCHID`), not Google Cloud Storage. GCS bucket uploads require IAM Service Account authentication.

## 3. Interactive Brokers (IBKR) Trading & Execution
- **Ports**: 
  - Real / Live: `4001` (flag `--real`)
  - Paper: `4002` (flag `--paper`)
- **Open Orders & Socket Timing**: When requesting open orders (`ib.reqAllOpenOrders()` / `ib.reqOpenOrders()`), always call `ib.sleep(1.0)` to allow the socket to receive incoming messages from IB Gateway / TWS before querying `ib.openTrades()`.
- **CLI Commands**:
  - View open orders: `./run.sh --run open_orders --paper` (or `./run.sh --run cancel_all_open --justprint --paper`)
  - Execute morning orders: `./run.sh --run orders --paper --all_strategies`
  - Cancel all orders: `./run.sh --run cancel_all_open --paper`

## 4. Google Sheets & Data Formats
- Allocation sheets (e.g. `gid=1955878182`) use a **horizontal matrix table** where Row 0 contains strategy names across columns, and subsequent rows contain date + dollar allocations. Parsing functions should support both horizontal matrix layouts and standard vertical 2-column key-value CSVs.

## 5. Market Data & Date Handling
- **Partial Weekend/Stray Data**: Data providers (e.g., Polygon daily aggregates) can occasionally return bars on non-trading days (such as Saturday OTC trades, test prints, or early morning pre-market records).
- **Selecting Date for Strategy Outputs**: Never use naive `prices.select(pl.col("date").max()).item()` to select the day to print out data. Doing so can cause Monday runs to pick up partial Saturday data (e.g., 2-3 tickers) and produce empty strategy outputs. Always use `get_last_full_date(prices)` from `src.features`, which dynamically verifies that the daily active symbol count meets universe capacity (e.g. $\ge 75\%$ of the rolling peak).
- **No Early Date Truncation**: Do NOT filter or prune the `prices` DataFrame by date before rolling calculations (`add_rolling_features`, `rally`, `calculate_orders`) or universe membership filtering. Keep the historical series intact and apply the `max_date` filter only at the final step when generating today's output (`today_prices = prices.filter(pl.col("date") == max_date)`).

