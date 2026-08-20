"""Google Sheets CSV synchronizer for polarstonks.

Downloads strategy inputs and asset sheets directly from Google Sheets
into OUT_DIR (/tmp/stonksanalysis).
"""

import os
import sys
import logging
import argparse
from typing import Dict, Optional, List
import requests

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import OUT_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SPREADSHEET_ID = "1bSW8k45QPlnL1aIijoRvRdZYhfKvHoz_TCWVGAZdGaE"

# Mapping of filename -> Google Sheet GID (None for the first/default sheet)
DEFAULT_SHEETS_MAP: Dict[str, Optional[str]] = {
    "manual.csv": "1162523034",
    "guidance.csv": "1671215343",
    "manual_sells.csv": "2039238656",
    "allow_list.csv": "1023788458",
    "assets_held.csv": None,
}


def build_sheet_export_url(spreadsheet_id: str, gid: Optional[str] = None) -> str:
    """Builds the CSV export URL for a given spreadsheet and sheet GID."""
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"
    if gid:
        url += f"&gid={gid}"
    return url


def download_sheet_csv(
    spreadsheet_id: str,
    gid: Optional[str],
    destination_path: str,
    timeout: int = 15,
) -> bool:
    """Downloads a single Google Sheet tab as a CSV file to destination_path."""
    url = build_sheet_export_url(spreadsheet_id, gid)
    try:
        response = requests.get(url, timeout=timeout)
        if response.status_code != 200:
            logger.error(
                f"Failed to download sheet (HTTP {response.status_code}) from {url}"
            )
            return False

        csv_content = response.text
        os.makedirs(os.path.dirname(os.path.abspath(destination_path)), exist_ok=True)

        try:
            with open(destination_path, "w", encoding="utf-8") as f:
                f.write(csv_content)
            logger.info(f"Downloaded {os.path.basename(destination_path)} ({len(csv_content)} bytes)")
            return True
        except PermissionError:
            fallback = f"/tmp/{os.path.basename(destination_path)}"
            logger.warning(
                f"Permission denied writing to {destination_path}. Writing to fallback {fallback}."
            )
            with open(fallback, "w", encoding="utf-8") as f:
                f.write(csv_content)
            logger.info(f"Downloaded to fallback {fallback} ({len(csv_content)} bytes)")
            return True

    except Exception as e:
        logger.error(f"Error downloading sheet for {destination_path}: {e}")
        return False


def sync_sheets(
    spreadsheet_id: str = DEFAULT_SPREADSHEET_ID,
    sheets_map: Optional[Dict[str, Optional[str]]] = None,
    out_dir: str = OUT_DIR,
    target_names: Optional[List[str]] = None,
) -> Dict[str, bool]:
    """Downloads configured sheets to output directory.

    Args:
        spreadsheet_id: Google Spreadsheet ID.
        sheets_map: Dictionary mapping filename to GID.
        out_dir: Directory where CSV files will be saved.
        target_names: Optional subset of sheet filenames to download.

    Returns:
        Dictionary mapping filename to success boolean.
    """
    mapping = sheets_map or DEFAULT_SHEETS_MAP
    if target_names:
        mapping = {k: v for k, v in mapping.items() if k in target_names or k.replace(".csv", "") in target_names}

    results = {}
    logger.info(f"Syncing {len(mapping)} Google Sheets to {out_dir}...")

    for filename, gid in mapping.items():
        dest = os.path.join(out_dir, filename if filename.endswith(".csv") else f"{filename}.csv")
        success = download_sheet_csv(spreadsheet_id, gid, dest)
        results[filename] = success

    return results


def main():
    parser = argparse.ArgumentParser(description="Sync Google Sheets to local CSV files.")
    parser.add_argument(
        "--spreadsheet_id",
        type=str,
        default=DEFAULT_SPREADSHEET_ID,
        help="Google Spreadsheet ID",
    )
    parser.add_argument(
        "--out_dir",
        type=str,
        default=OUT_DIR,
        help="Destination directory for CSV files",
    )
    parser.add_argument(
        "--sheets",
        nargs="*",
        help="Specific sheet names to download (e.g. manual guidance manual_sells)",
    )
    args = parser.parse_args()

    results = sync_sheets(
        spreadsheet_id=args.spreadsheet_id,
        out_dir=args.out_dir,
        target_names=args.sheets,
    )

    failed = [k for k, v in results.items() if not v]
    if failed:
        logger.error(f"Failed to sync sheets: {failed}")
        sys.exit(1)
    else:
        logger.info("All sheets synced successfully.")


if __name__ == "__main__":
    main()
