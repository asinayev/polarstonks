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

def run_script(script_name):
    """Runs a script from the src directory."""
    script_path = os.path.join(os.path.dirname(__file__), script_name)
    logger.info(f"Running {script_name}...")
    
    # Use subprocess.run and let stdout/stderr flow to the console
    cmd = [sys.executable, script_path]
    result = subprocess.run(cmd)
    
    if result.returncode != 0:
        logger.error(f"")
        logger.error(f"FATAL ERROR: {script_name} failed with exit code {result.returncode}")
        logger.error(f"See the error message above from {script_name} for details.")
        logger.error(f"")
        sys.exit(result.returncode)

def main():
    parser = argparse.ArgumentParser(description="Polarstonks Dispatcher")
    parser.add_argument("--run", dest="run_what", required=True, help="Task to run (am, news, etc)")
    args = parser.parse_args()
    
    run_what = args.run_what
    
    if run_what in ['am', 'am_data', 'get_data']:
        # Fetch data
        run_script("get_data.py")
        
    if run_what in ['am', 'am_out']:
        # Run strategies
        run_script("am_stocks.py")
        run_script("am_etfs.py")
        run_script("am_short_interest.py")
        
    if run_what == 'news' or run_what == 'night_snapshot':
        if run_what == 'night_snapshot':
            run_script("get_snapshot.py")
        run_script("am_news.py")
        
    if run_what == 'snapshot':
        run_script("get_snapshot.py")
        
    if run_what == 'all':
        run_script("get_data.py")
        run_script("am_stocks.py")
        run_script("am_etfs.py")
        run_script("am_short_interest.py")
        run_script("am_news.py")

if __name__ == "__main__":
    main()
