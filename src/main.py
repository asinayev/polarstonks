import argparse
import os
import sys
import logging
import subprocess

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_script(script_name, extra_args=None):
    """Runs a script from the src directory."""
    script_path = os.path.join(os.path.dirname(__file__), script_name)
    logger.info(f"Running {script_name}...")
    
    # Use subprocess.run and let stdout/stderr flow to the console
    cmd = [sys.executable, script_path]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        logger.error(f"")
        logger.error(f"FATAL ERROR: {script_name} failed with exit code {result.returncode}")
        logger.error(f"See the error message above from {script_name} for details.")
        logger.error(f"")
        sys.exit(result.returncode)

def main():
    parser = argparse.ArgumentParser(description="Polarstonks Dispatcher")
    parser.add_argument("--run", dest="run_what", required=True, help="Task to run (am, news, orders, close, etc)")
    args, remaining_args = parser.parse_known_args()
    
    run_what = args.run_what
    
    if run_what in ['am_data']:
        # Fetch data
        run_script("get_data.py", extra_args=remaining_args)
        
    if run_what in ['snapshot']:
        run_script("get_snapshot.py", extra_args=remaining_args)

    if run_what in ['sheets', 'sync_sheets', 'google_sheets']:
        run_script("sync_sheets.py", extra_args=remaining_args)

    if run_what == 'news':
        run_script("am_news.py", extra_args=remaining_args)

    if run_what in ['announcements', 'corporate_announcements', 'steves_news', 'steves_buybacks']:
        run_script(os.path.join("llm_parsing", "announcements_scanner.py"), extra_args=remaining_args)

    if run_what == 'buybacks':
        run_script(os.path.join("llm_parsing", "buybacks_pipeline.py"), extra_args=remaining_args)

    if run_what == 'etf_parser':
        run_script("etf_parser.py", extra_args=remaining_args)

    # Execution & Order Management
    if run_what in ['check_assets']:
        run_script(os.path.join("execution", "check_assets.py"), extra_args=remaining_args)

    if run_what in ['morning_orders']:
        run_script(os.path.join("execution", "morning_orders.py"), extra_args=remaining_args)

    if run_what in ['morning_close', 'close', 'close_orders']:
        run_script(os.path.join("execution", "morning_close.py"), extra_args=remaining_args)

    if run_what in ['order_most_active']:
        run_script(os.path.join("execution", "order_most_active.py"), extra_args=remaining_args)

    if run_what in ['cancel_all_open']:
        run_script(os.path.join("execution", "cancel_all_open.py"), extra_args=remaining_args)

    if run_what in ['orders']:
        run_script(os.path.join("execution", "cancel_all_open.py"), extra_args=["--justprint"] + remaining_args)

    if run_what in ['trades']:
        run_script(os.path.join("execution", "view_trades.py"), extra_args=remaining_args)

    if run_what in ['archive_orders']:
        run_script("archive_logs.py", extra_args=["--orders_only"] + remaining_args)

    if run_what in ['archive']:
        run_script("archive_logs.py", extra_args=remaining_args)
        
    if run_what == 'am':
        run_script("get_data.py")
        run_script("am_stocks.py")
        run_script("am_etfs.py")
        run_script("am_short_interest.py")
        run_script("get_snapshot.py")
        run_script("sync_sheets.py", extra_args=remaining_args)
        run_script(os.path.join("execution", "check_assets.py"), extra_args=remaining_args)
        morning_args = remaining_args.copy() if remaining_args else []
        if "--early" not in morning_args:
            morning_args.insert(0, "--early")
        run_script(os.path.join("execution", "morning_orders.py"), extra_args=morning_args)
        run_script(os.path.join("llm_parsing", "announcements_scanner.py"), extra_args=remaining_args)

    if run_what == 'pre_open':
        run_script("get_snapshot.py")
        run_script("am_news.py")
        run_script("sync_sheets.py", extra_args=remaining_args)
        run_script(os.path.join("execution", "morning_orders.py"), extra_args=remaining_args)
        
if __name__ == "__main__":
    main()
